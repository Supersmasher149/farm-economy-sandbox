"""Always plants the cheapest affordable crop, regardless of profitability,
to preserve maximum cash toward the next upgrade -- and buys upgrades the
instant they're affordable. Purpose: tests the "upgrade too fast" balance
rule and whether rushing upgrades ahead of any real income is a dominant
exploit.
"""

from agents.base import Agent
from simulation import economy_rules


class UpgradeRusher(Agent):
    name = "upgrade_rusher"
    description = "Always plants the cheapest affordable crop to hoard cash, and buys every upgrade the moment it's affordable."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c
            for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: c["seed_cost"])

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]

    def choose_sales(self, player, channels, items_by_id):
        # Hoarding cash toward the next upgrade is the whole point of this
        # agent, so leaving revenue on the table by dumping everything at spot
        # contradicted the strategy it exists to probe: it was measuring
        # "rush upgrades while selling badly" rather than "rush upgrades".
        return self.route_sales_by_best_price(player, channels)
