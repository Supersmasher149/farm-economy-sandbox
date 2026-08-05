"""Plants exactly like ProfitOptimizer -- on paper it is chasing the same
expected-profit-maximizing crop choices -- but rarely actually waters the
farm. Purpose: isolate how much watering neglect alone erodes an otherwise
sound crop strategy (this agent's crop and upgrade logic is deliberately
identical to ProfitOptimizer so the only variable is watering diligence).
"""
from agents.base import Agent
from simulation import economy_rules

# Waters roughly 1 day in 7: this agent "means well" but keeps forgetting.
WATERING_DILIGENCE = 0.15


class NeglectfulGrower(Agent):
    name = "neglectful_grower"
    description = "Picks crops like a profit optimizer but waters only ~15% of days; crops accrue neglect and underperform."
    watering_diligence = WATERING_DILIGENCE

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        candidates = [
            c for c in crops
            if economy_rules.is_crop_unlocked(c, player) and player.money >= c["seed_cost"]
        ]
        if not candidates:
            return None
        safe_candidates = [
            crop for crop in candidates
            if economy_rules.can_spend_with_reserve(player, crop["seed_cost"])
        ]
        if not safe_candidates:
            return min(candidates, key=lambda c: c["growth_days"])
        candidates = safe_candidates
        return max(candidates, key=lambda c: economy_rules.expected_profit_per_day(c, player, upgrades_by_id))

    def should_buy_upgrade(self, player, upgrade):
        return player.money >= upgrade["cost"]
