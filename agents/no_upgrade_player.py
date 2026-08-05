"""Identical to ProfitOptimizer in every respect except it never buys an
upgrade. Purpose: a clean control group -- diff its results against
ProfitOptimizer to see exactly how much upgrades are worth, and whether
skipping them entirely is still a viable way to play.
"""
from agents.base import Agent
from simulation import economy_rules


class NoUpgradePlayer(Agent):
    name = "no_upgrade_player"
    description = "Plays like the profit optimizer but never buys an upgrade -- isolates how much upgrades are actually worth."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        safe_candidates = [
            crop for crop in candidates
            if economy_rules.can_spend_with_reserve(player, crop["seed_cost"])
        ]
        if not safe_candidates:
            return min(candidates, key=lambda c: c["growth_days"])
        candidates = safe_candidates
        return max(candidates, key=lambda c: economy_rules.expected_profit_per_day(c, player, upgrades_by_id))

    def should_buy_upgrade(self, player, upgrade):
        return False

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        if not economy_rules.can_spend_with_reserve(
            player, crop["seed_cost"] + fertilizer_config["cost"]
        ):
            return False
        return economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config) > 0
