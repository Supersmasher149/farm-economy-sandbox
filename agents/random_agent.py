"""Makes every decision by a pseudo-random draw instead of a rule. Purpose:
the baseline floor every other strategy should be able to beat -- if a
deliberate strategy doesn't outperform this, the strategy isn't adding
anything, or the economy is too flat/noisy for strategy to matter.

Decisions are derived from a hash of already-seed-determined state (day,
money, candidate set) rather than an independent RNG stream, so a replayed
run with the same seed reproduces the exact same "random" choices.
"""
import hashlib

from agents.base import Agent
from simulation import economy_rules


def _pseudo_random_unit(*parts) -> float:
    """Deterministic pseudo-random float in [0, 1) derived from `parts`.

    Uses a fixed-algorithm hash rather than the builtin `hash()`, whose
    string hashing is randomized per interpreter process -- that would make
    choices vary across worker processes in a parallel batch run even for
    the same recorded seed.
    """
    digest = hashlib.md5(repr(parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2 ** 32


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
        index = int(_pseudo_random_unit(player.day, len(player.planted), player.total_planted) * len(candidates))
        return candidates[min(index, len(candidates) - 1)]

    def should_buy_upgrade(self, player, upgrade):
        return _pseudo_random_unit(player.day, upgrade["id"], "upgrade") < 0.5

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return _pseudo_random_unit(player.day, crop["id"], "fertilize_at_plant") < 0.5

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return _pseudo_random_unit(planted.day_planted, planted.crop_id, "fertilize_mid_grow") < 0.5
