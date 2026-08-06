"""Waters diligently -- crop care is not this agent's problem -- but manages
money poorly: always reaches for the most expensive crop it can currently
afford (regardless of whether it's actually the best return), and buys
fertilizer on impulse any time it's affordable rather than checking if it
pays for itself. Purpose: isolate financial mismanagement from crop-care
neglect (the opposite failure mode from NeglectfulGrower).
"""

from agents.base import Agent
from simulation import economy_rules


class RecklessSpender(Agent):
    name = "reckless_spender"
    description = "Waters reliably but always buys the priciest affordable crop and fertilizes on impulse, with no cash reserve."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c
            for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c["seed_cost"])

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return player.money >= crop["seed_cost"] + fertilizer_config["cost"]
