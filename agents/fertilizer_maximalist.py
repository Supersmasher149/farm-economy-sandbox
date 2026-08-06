"""Identical to ProfitOptimizer except it fertilizes every planting it can
afford, without checking whether the math says it's worth it. Purpose: a
clean control group -- diff its results against ProfitOptimizer (which only
fertilizes when expected-profitable) to see fertilizer's true ROI and
whether "always fertilize" is close enough to optimal to be a safe default.
"""

from agents.profit_optimizer import ProfitOptimizer


class FertilizerMaximalist(ProfitOptimizer):
    name = "fertilizer_maximalist"
    description = "Plays like the profit optimizer but fertilizes every planting it can afford, math be damned -- isolates fertilizer ROI."

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return player.money >= crop["seed_cost"] + fertilizer_config["cost"]

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return True
