"""Always plants whichever affordable crop has the highest expected profit
per slot per day, and reinvests aggressively. Purpose: surface dominant
crops and upgrade exploits.
"""
from agents.base import Agent
from simulation import contracts, economy_rules
from simulation import inventory, markets


class ProfitOptimizer(Agent):
    name = "profit_optimizer"
    description = "Maximizes expected profit per slot per day; waters reliably; fertilizes only when the math says it pays off."

    # Below this soil_health_factor, relax (not remove) the fertilizer
    # marginal-profit bar as maintenance spend -- soil nutrients never
    # regenerate on their own (see economy_rules.soil_quality_risk), so a
    # strict per-crop EV check under-fertilizes crops that are only mildly
    # unprofitable to fertilize once soil health is factored in. This never
    # forces fertilizer onto a crop whose own economics are deeply negative
    # (e.g. quickweed, at roughly -6 nominal marginal profit) -- that would
    # just trade a bankruptcy-by-quality-rejection for a bankruptcy-by-
    # fertilizer-bill.
    SOIL_HEALTH_FERTILIZE_THRESHOLD = 0.5
    SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR = -3.0

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if player.total_days is not None:
            remaining_days = player.total_days - player.day
            # Never sink a seed cost into a crop that provably can't mature
            # (and so can't be sold) before the run ends -- if that empties
            # the candidate list, leaving the slot idle for the rest of the
            # run is correct, not a bug to fall back around.
            candidates = [
                c for c in candidates
                if economy_rules.effective_growth_days(c, player, upgrades_by_id) <= remaining_days
            ]
        if not candidates:
            return None
        active = next(
            (contract for contract in player.active_contracts if not contract.resolved),
            None,
        )
        if active:
            contracted_crop = crops_by_id.get(active.item_id)
            if contracted_crop and economy_rules.is_crop_unlocked(contracted_crop, player):
                if player.money >= contracted_crop["seed_cost"]:
                    return contracted_crop

        crop = economy_rules.choose_crop_with_relaxed_reserve(
            candidates, player, upgrades_by_id, reserve_fractions=(1.0, 0.5, 0.25)
        )
        if crop is not None:
            return crop
        return min(candidates, key=lambda c: c["growth_days"])

    def should_buy_upgrade(self, player, upgrade):
        return economy_rules.should_buy_upgrade_within_budget(player, upgrade)

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        marginal_profit = economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config)
        low_soil_health = economy_rules.soil_health_factor(player) < self.SOIL_HEALTH_FERTILIZE_THRESHOLD
        if low_soil_health and marginal_profit > self.SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR:
            # Soil maintenance is exempt from the full reserve (only raw
            # affordability): once soil health is critically low, skipping
            # a cheap, not-badly-unprofitable fertilizer application to
            # protect the reserve just guarantees the next harvest gets
            # rejected outright, which hurts cash flow far more than the
            # fertilizer would.
            return player.money >= fertilizer_config["cost"]
        if not economy_rules.can_spend_with_reserve(player, fertilizer_config["cost"]):
            return False
        return marginal_profit > 0

    def choose_contracts(self, player, offers):
        if any(not contract.resolved for contract in player.active_contracts):
            return []
        suitable = [
            offer
            for offer in offers
            if contracts.is_offer_profitable(player, offer)
            and contracts.is_offer_feasible(player, offer)
        ]
        return [max(suitable, key=lambda offer: offer.unit_price).id] if suitable else []

    def choose_processing(self, player, recipes, items_by_id):
        decisions = []
        for recipe in recipes:
            available = inventory.available_quantity(player, recipe["input_item_id"], recipe.get("min_quality", "processing"))
            if available < recipe["input_quantity"]:
                continue
            input_value = player.market_prices.get(recipe["input_item_id"], 0) * recipe["input_quantity"]
            output_value = player.market_prices.get(recipe["output_item_id"], 0) * recipe["output_quantity"]
            if output_value > input_value + recipe.get("cost", 0):
                decisions.append({"recipe_id": recipe["id"], "batches": 1})
        return decisions

    def choose_sales(self, player, channels, items_by_id):
        decisions = []
        quantities = {}
        quality = {}
        for lot in player.inventory_lots:
            quantities[lot.item_id] = quantities.get(lot.item_id, 0) + lot.quantity
            quality.setdefault(lot.item_id, lot.quality)
        for item_id, quantity in quantities.items():
            channel = markets.best_channel(player, item_id, quality[item_id], channels, quantity)
            if channel:
                decisions.append({"item_id": item_id, "quantity": quantity, "channel_id": channel["id"]})
        return decisions

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        marginal_profit = economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config)
        low_soil_health = economy_rules.soil_health_factor(player) < self.SOIL_HEALTH_FERTILIZE_THRESHOLD
        if low_soil_health and marginal_profit > self.SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR:
            return player.money >= crop["seed_cost"] + fertilizer_config["cost"]
        if not economy_rules.can_spend_with_reserve(
            player, crop["seed_cost"] + fertilizer_config["cost"]
        ):
            return False
        return marginal_profit > 0
