"""Plants exactly like ProfitOptimizer -- on paper it is chasing the same
expected-profit-maximizing crop choices -- but rarely actually waters the
farm. Purpose: isolate how much watering neglect alone erodes an otherwise
sound crop strategy (this agent's crop and upgrade logic is deliberately
identical to ProfitOptimizer so the only variable is watering diligence).
"""

from agents.profit_optimizer import ProfitOptimizer

# Waters roughly 1 day in 7: this agent "means well" but keeps forgetting.
WATERING_DILIGENCE = 0.15


class NeglectfulGrower(ProfitOptimizer):
    name = "neglectful_grower"
    description = "Picks crops like a profit optimizer but waters only ~15% of days; crops accrue neglect and underperform."
    watering_diligence = WATERING_DILIGENCE
