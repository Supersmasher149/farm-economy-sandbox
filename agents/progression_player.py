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
            (
                contract
                for contract in player.active_contracts
                # Deadline resolution runs at end of day, after crop
                # decisions -- a contract past its deadline but not yet
                # resolved must not still drive today's planting.
                if not contract.resolved and player.day <= contract.deadline_day
            ),
            None,
        )
        if active:
            contracted_crop = next((c for c in affordable if c["id"] == active.item_id), None)
            if contracted_crop:
                days_to_deadline = active.deadline_day - player.day
                growth_days = economy_rules.effective_growth_days(
                    contracted_crop, player, upgrades_by_id
                )
                matures_in_time = growth_days <= days_to_deadline and (
                    player.total_days is None or growth_days <= player.total_days - player.day
                )
                still_short = contracts.forecast_committed_supply(player, active) < active.remaining
                if matures_in_time and still_short:
                    return contracted_crop

        if player.money < recovery_threshold:
            return min(affordable, key=lambda c: c["growth_days"])

        safe = [
            crop for crop in affordable if economy_rules.crop_seed_reserve_gate(crop, player, 1.0)
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
        # Fertilizing every non-fast crop unconditionally is not "saving toward
        # the upgrades" -- it was the single largest expense in the roster
        # (fertilizer outspending seeds roughly 2:1) while this agent's main
        # crop carried a negative fertilizer marginal profit. Keep the
        # role-based intent (a progression player doesn't bother fertilizing
        # the throwaway fast crop) but require the spend to actually pay.
        if crop.get("role") == "fast":
            return False
        if not economy_rules.can_spend_with_reserve(player, fertilizer_config["cost"]):
            return False
        return economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config) > 0

    def choose_sales(self, player, channels, items_by_id):
        # This agent already negotiates contracts, so dumping the rest of the
        # harvest at spot was an inconsistent level of commercial competence.
        return self.route_sales_by_best_price(player, channels)

    def choose_contracts(self, player, offers):
        if any(not contract.resolved for contract in player.active_contracts):
            return []
        affordable_scale = max(6, player.slots_total * 3)
        suitable = [
            offer
            for offer in offers
            if offer.quantity <= affordable_scale
            and contracts.is_offer_profitable(player, offer)
            and contracts.is_offer_feasible(player, offer)
        ]
        return [max(suitable, key=lambda offer: offer.unit_price).id] if suitable else []
