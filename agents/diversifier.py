"""Deliberately spreads plantings across every unlocked, affordable crop
instead of monocropping the single best option -- every other agent in the
roster always converges on one "best" crop. Purpose: tests whether
diversification meaningfully reduces variance/bankruptcy risk, and whether
monoculture (implicit in every profit-seeking agent) is actually optimal.
"""

from agents.base import Agent
from simulation import economy_rules


class Diversifier(Agent):
    name = "diversifier"
    description = "Always plants whichever unlocked, affordable crop it has used least so far -- deliberate portfolio spread instead of monoculture."

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
            key=lambda c: (player.crop_plant_counts.get(c["id"], 0), c["seed_cost"]),
        )

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]
