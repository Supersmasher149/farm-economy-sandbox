"""Always plants whichever affordable crop has the highest expected profit
per slot per day, and reinvests aggressively. Purpose: surface dominant
crops and upgrade exploits.
"""
from agents.base import Agent
from simulation import economy_rules
from simulation import inventory, markets


class ProfitOptimizer(Agent):
    name = "profit_optimizer"
    description = "Maximizes expected profit per slot per day; waters reliably; fertilizes only when the math says it pays off."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: economy_rules.expected_profit_per_day(c, player, upgrades_by_id))

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config) > 0

    def choose_contracts(self, player, offers):
        return [offer.id for offer in offers if offer.unit_price >= player.market_prices.get(offer.item_id, 0) * 1.15]

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
        if player.money < crop["seed_cost"] + fertilizer_config["cost"]:
            return False
        return economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config) > 0
