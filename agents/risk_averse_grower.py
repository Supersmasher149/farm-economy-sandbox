"""Prioritizes the lowest-loss-chance crop over the highest-profit one, and
fertilizes for the loss-chance reduction rather than the yield bonus.
Purpose: tests whether cautious play is unfairly punished by the economy --
if this agent can never keep pace with the profit-seeking agents, that's a
balance red flag (the game would be teaching players that caution doesn't
pay).
"""

from agents.base import Agent
from simulation import economy_rules


class RiskAverseGrower(Agent):
    name = "risk_averse_grower"
    description = "Always plants the safest (lowest loss-chance) affordable crop over the most profitable one; fertilizes for safety, not yield."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c
            for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda c: (
                c["loss_chance"],
                -economy_rules.expected_profit_per_day(c, player, upgrades_by_id),
            ),
        )

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        if player.money < crop["seed_cost"] + fertilizer_config["cost"]:
            return False
        return economy_rules.fertilizer_safety_value(crop, fertilizer_config) > 0
