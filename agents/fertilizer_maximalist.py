"""Identical to ProfitOptimizer except it fertilizes every planting it can
afford, without checking whether the math says it's worth it. Purpose: a
clean control group -- diff its results against ProfitOptimizer (which only
fertilizes when expected-profitable) to see fertilizer's true ROI and
whether "always fertilize" is close enough to optimal to be a safe default.
"""
from agents.base import Agent
from simulation import economy_rules


class FertilizerMaximalist(Agent):
    name = "fertilizer_maximalist"
    description = "Plays like the profit optimizer but fertilizes every planting it can afford, math be damned -- isolates fertilizer ROI."

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

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return player.money >= crop["seed_cost"] + fertilizer_config["cost"]

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return True
