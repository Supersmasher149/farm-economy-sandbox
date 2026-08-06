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
        # `candidates` above already excludes anything unlocked/affordable/
        # unable to mature by the final simulated day, so looking the
        # contracted crop up there (rather than back in crops_by_id) reuses
        # that same maturity check instead of silently bypassing it.
        candidates_by_id = {c["id"]: c for c in candidates}
        active = next(
            (
                contract for contract in player.active_contracts
                # Deadline resolution runs at end of day, after crop
                # decisions -- a contract past its deadline but not yet
                # resolved must not still drive today's planting.
                if not contract.resolved and player.day <= contract.deadline_day
            ),
            None,
        )
        if active:
            contracted_crop = candidates_by_id.get(active.item_id)
            if contracted_crop is not None:
                days_to_deadline = active.deadline_day - player.day
                growth_days = economy_rules.effective_growth_days(contracted_crop, player, upgrades_by_id)
                still_short = contracts.forecast_committed_supply(player, active) < active.remaining
                if growth_days <= days_to_deadline and still_short:
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
        planned_capacity = dict(player.channel_capacity_used)
        quantities = {}
        for lot in player.inventory_lots:
            by_quality = quantities.setdefault(lot.item_id, {})
            by_quality[lot.quality] = by_quality.get(lot.quality, 0) + lot.quantity

        routes = {}
        for item_id, by_quality in quantities.items():
            for quality in sorted(by_quality, key=lambda value: -markets.QUALITY_MULTIPLIERS[value]):
                remaining = by_quality[quality]
                while remaining > 0:
                    candidates = [
                        (
                            markets.quote(
                                player, item_id, quality, channel, remaining,
                                capacity_used=planned_capacity,
                            ),
                            channel,
                        )
                        for channel in channels
                    ]
                    candidates = [pair for pair in candidates if pair[0]]
                    if not candidates:
                        break
                    offer, channel = max(
                        candidates, key=lambda pair: pair[0]["net"] / pair[0]["quantity"]
                    )
                    sold = offer["quantity"]
                    route = (item_id, quality, channel["id"])
                    routes[route] = routes.get(route, 0) + sold
                    planned_capacity[channel["id"]] = planned_capacity.get(channel["id"], 0) + sold
                    remaining -= sold

        return [
            {
                "item_id": item_id,
                "quantity": quantity,
                "channel_id": channel_id,
                "quality": quality,
            }
            for (item_id, quality, channel_id), quantity in routes.items()
        ]

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
