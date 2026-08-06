"""Prefers the shortest growth-duration crop for frequent income; avoids
big spends. Purpose: test whether short-growth crops become dominant
through rapid reinvestment.
"""

from agents.base import Agent
from simulation import economy_rules

# Fast Seller only buys upgrades it can afford comfortably (a small buffer
# left over) and only if the upgrade is relatively cheap.
CHEAP_UPGRADE_COST_CEILING = 150
AFFORDABILITY_BUFFER = 1.2


class FastSeller(Agent):
    name = "fast_seller"
    description = (
        "Always plants the shortest-growth crop; waters reliably; avoids fertilizer spend."
    )

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c
            for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: c["growth_days"])

    def should_buy_upgrade(self, player, upgrade):
        if upgrade["cost"] > CHEAP_UPGRADE_COST_CEILING:
            return False
        return player.money >= upgrade["cost"] * AFFORDABILITY_BUFFER

    def choose_sales(self, player, channels, items_by_id):
        quantities = {}
        for lot in player.inventory_lots:
            quantities[lot.item_id] = quantities.get(lot.item_id, 0) + lot.quantity
        return [
            {"item_id": item_id, "quantity": quantity, "channel_id": "spot"}
            for item_id, quantity in quantities.items()
        ]
