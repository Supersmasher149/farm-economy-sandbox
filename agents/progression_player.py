"""Aims to reach both upgrades, buying them the moment they're affordable
so upgrade money is effectively reserved before seed spending. Falls back
to the fastest crop when money is dangerously low to recover, otherwise
prefers the standard crop as the balanced default. Purpose: test the
intended progression path.
"""
from agents.base import Agent
from simulation import economy_rules

# Below this multiple of the cheapest seed cost, prioritize recovery over
# the balanced/standard crop choice.
RECOVERY_MONEY_MULTIPLE = 3


class ProgressionPlayer(Agent):
    name = "progression_player"
    description = "Saves toward both upgrades, falls back to a fast crop to recover when low; waters reliably."

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [c for c in crops if economy_rules.is_crop_unlocked(c, player)]
        if not candidates:
            return None

        cheapest_cost = min(c["seed_cost"] for c in candidates)
        affordable = [c for c in candidates if player.money >= c["seed_cost"]]
        if not affordable:
            return None

        if player.money < cheapest_cost * RECOVERY_MONEY_MULTIPLE:
            return min(affordable, key=lambda c: c["growth_days"])

        standard = next((c for c in affordable if c.get("role") == "standard"), None)
        if standard:
            return standard
        return min(affordable, key=lambda c: c["seed_cost"])

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return crop.get("role") != "fast"

    def choose_contracts(self, player, offers):
        if any(not contract.resolved for contract in player.active_contracts):
            return []
        affordable_scale = max(6, player.slots_total * 3)
        suitable = [offer for offer in offers if offer.quantity <= affordable_scale]
        return [max(suitable, key=lambda offer: offer.unit_price).id] if suitable else []
