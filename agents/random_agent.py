"""Baseline agent using a deterministic policy stream per simulation run."""
from agents.base import Agent
from simulation import economy_rules


class RandomAgent(Agent):
    name = "random_agent"
    description = "Baseline: makes every decision (crop, upgrade, fertilizer) by an unweighted random draw."
    watering_diligence = 0.5

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda c: c["id"])
        index = int(
            player.decision_random(
                "choose_crop", len(player.planted), player.total_planted,
                tuple(c["id"] for c in candidates),
            ) * len(candidates)
        )
        return candidates[min(index, len(candidates) - 1)]

    def should_buy_upgrade(self, player, upgrade):
        return player.decision_random("upgrade", upgrade["id"]) < 0.5

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return player.decision_random("fertilize_at_plant", crop["id"]) < 0.5

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return player.decision_random(
            "fertilize_mid_grow", planted.day_planted, planted.crop_id,
        ) < 0.5
