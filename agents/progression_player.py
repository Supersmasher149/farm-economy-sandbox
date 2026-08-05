"""Aims to reach both upgrades, buying them the moment they're affordable
so upgrade money is effectively reserved before seed spending. Falls back
to the fastest crop when money is dangerously low to recover, otherwise
prefers the standard crop as the balanced default. Purpose: test the
intended progression path.
"""
from agents.base import Agent
from simulation import contracts, economy_rules

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

        recovery_threshold = max(
            economy_rules.operating_reserve(player),
            cheapest_cost * RECOVERY_MONEY_MULTIPLE,
        )
        active = next(
            (contract for contract in player.active_contracts if not contract.resolved),
            None,
        )
        if active:
            contracted_crop = next((c for c in affordable if c["id"] == active.item_id), None)
            if contracted_crop:
                return contracted_crop

        if player.money < recovery_threshold:
            return min(affordable, key=lambda c: c["growth_days"])

        safe = [
            crop for crop in affordable
            if economy_rules.crop_seed_reserve_gate(crop, player, 1.0)
        ]
        if not safe:
            return min(affordable, key=lambda c: c["growth_days"])
        standard = next((c for c in safe if c.get("role") == "standard"), None)
        if standard:
            return standard
        return min(safe, key=lambda c: c["seed_cost"])

    def should_buy_upgrade(self, player, upgrade):
        return economy_rules.should_buy_upgrade_within_budget(player, upgrade)

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return (
            crop.get("role") != "fast"
            and economy_rules.can_spend_with_reserve(player, fertilizer_config["cost"])
        )

    def choose_contracts(self, player, offers):
        if any(not contract.resolved for contract in player.active_contracts):
            return []
        affordable_scale = max(6, player.slots_total * 3)
        suitable = [
            offer for offer in offers
            if offer.quantity <= affordable_scale
            and contracts.is_offer_profitable(player, offer)
            and contracts.is_offer_feasible(player, offer)
        ]
        return [max(suitable, key=lambda offer: offer.unit_price).id] if suitable else []
