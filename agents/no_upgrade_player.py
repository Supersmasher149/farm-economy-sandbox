"""Identical to ProfitOptimizer in every respect except it never buys an
upgrade. Purpose: a clean control group -- diff its results against
ProfitOptimizer to see exactly how much upgrades are worth, and whether
skipping them entirely is still a viable way to play.
"""
from agents.profit_optimizer import ProfitOptimizer


class NoUpgradePlayer(ProfitOptimizer):
    name = "no_upgrade_player"
    description = "Plays like the profit optimizer but never buys an upgrade -- isolates how much upgrades are actually worth."

    def should_buy_upgrade(self, player, upgrade):
        return False
