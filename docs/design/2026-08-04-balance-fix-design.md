# Farm Economy Balance Fix Design

## Goal

Prevent the nominal expected-value agent from converting profitable crop
choices into cash-flow ruin, while keeping contracts and upgrades meaningful
for agents that can afford to plan around them.

## Findings

- `expected_profit_per_day` is a nominal, no-neglect calculation. Its arithmetic
  is correct, but it is not a sufficient decision rule when a planting consumes
  the farm's working capital or when environmental stress downgrades harvests.
- `ProfitOptimizer` and `ProgressionPlayer` buy upgrades as soon as their price
  is available. `capacity_1` can therefore consume the entire cash balance,
  leaving no money for seed, fertilizer, or watering costs.
- Fertilizer's nominal marginal values are positive for greenleaf and purplehaze
  and negative for quickweed. The problem is spending fertilizer without a cash
  floor, not the marginal-value arithmetic.
- Local contracts are profitable in isolation, but the agents compare them to
  the raw market price, accept overlapping offers, and do not plan the crop
  required to fulfill them. The regional processor's largest offer is also too
  large for three slots within its deadline.

## Design

Full-world runs expose a configurable `operating_reserve` on `PlayerState`,
loaded from `simulation_settings.json`. The balance scenario uses a `$100`
reserve; legacy test configurations default to zero. The reserve is advisory,
not a global spending restriction, so reckless strategies remain useful
balance probes.

The optimizer and progression agent:

- fall back to the fastest affordable crop below the reserve;
- only buy upgrades or fertilizer when the post-purchase balance keeps the
  reserve;
- allow at most one active contract;
- compare contract revenue with the best eligible market channel after fees;
- prioritize the contracted crop while an active contract is being produced;
- reject offers whose quantity cannot be produced before their deadline.

The regional processor contract is tuned to `6–15` units with a 14-day
deadline, matching the output possible from three standard-growth slots.
Contract feasibility uses a configurable 45% production safety factor, and
contract expiry and penalties remain unchanged.

## Verification

Focused tests cover reserve-aware decisions, contract opportunity pricing and
production feasibility, and the progression recovery threshold. The existing
test suite must remain green. A fixed-seed batch and the requested 10,000-run
batch must show `profit_optimizer` above `fast_seller` in final cash and below
it in bankruptcy rate.

## Correction — 2026-08-05: day-0 upgrade cascade and soil-quality risk

The original fix above was verified only at its own 45-day/$60 default
config, where `tests/test_balance_policies.py::
test_full_world_optimizer_beats_fast_seller_without_extra_ruin` passes.
Re-testing at a longer, richer stress config (200 days, $1000 starting
money — the same shape of scenario a real balance review would run to
stress-test the economy) found two further gaps.

**Gap 1: day-0 upgrade cascade.** `ProfitOptimizer`/`ProgressionPlayer.
should_buy_upgrade` bought *any* upgrade the instant `can_spend_with_reserve`
passed, with no cap on how much capital went to upgrades in one day. With
$1000 starting capital, this bought all four upgrades ($880 total) on day 0,
leaving thin working capital for the rest of a 200-day run.

Fix: `economy_rules.should_buy_upgrade_within_budget` (used by both agents'
`should_buy_upgrade`) adds a purchase cooldown (`player.upgrade_purchase_
days`), a cap on cumulative upgrade spend relative to `player.highest_
money`, and — when priceable via the new `economy_rules.upgrade_payback_
days` — a payback-period-vs-remaining-days check (`PlayerState.total_days`,
plumbed from `runner/single_run.py`).

**Gap 2 (larger): soil-quality risk is invisible to nominal EV.** Tracing a
long-horizon run showed the deeper problem: `expected_profit_per_day`
assumes every harvest sells at full nominal yield and price forever. In
reality, plot nitrogen/phosphorus/potassium (`simulation/crop_growth.py`)
only ever decrease — never regenerating except via fertilizer — and once
depleted enough, harvests get discounted to a lower quality grade or
rejected outright (`grade == "rejected"` in `simulation/actions.py`, zero
revenue). A nominal-EV-only ranking keeps preferring nutrient-hungry crops
(`greenleaf`, `purplehaze`) even as they degrade their own future yield,
walking a farm's real returns to near zero over a long run — while
`fast_seller`'s quickweed-only "dumb" strategy incidentally avoids this by
having far lower nutrient demand and a much faster growth cycle.

Fix: `economy_rules.soil_health_factor` (scarcest of N/P/K across plots),
`soil_quality_risk` (discount scaled by both how depleted the soil is and
how nutrient-hungry the crop is), and `quality_adjusted_profit_per_day`
(EV discounted by that risk) replace nominal EV in `best_crop_by_expected_
profit`'s ranking. Below a critical soil-health threshold, ranking is
bypassed entirely in favor of planting whichever candidate is least
nutrient-demanding (pure triage, not a profit comparison).
`ProfitOptimizer.should_fertilize`/`should_use_fertilizer` also fertilize
as soil maintenance (bypassing the full reserve, requiring only raw
affordability) once soil health drops low, excluding crops whose fertilizer
economics are badly negative on their own (e.g. quickweed) so maintenance
spend doesn't itself become a drain. `choose_crop` also skips any crop that
provably can't mature before the run ends (`PlayerState.total_days`).

**Verified outcome:** at the 45-day/$60 default config, these changes are a
clear net improvement — `profit_optimizer`'s average final money rose from
about $101 to about $138 (still 0% bankruptcy, still cleanly ahead of
`fast_seller`'s ~$60). **At the 200-day/$1000 stress config, the aggregate
outcome is statistically unchanged from the unfixed baseline**: ~100%
bankruptcy, average final money near $0–5, average bankruptcy day around
126–128 either way. Individual traces show real behavioral improvement
(soil-health-aware crop switching, later bankruptcy in specific runs,
sometimes surviving to day 190+), but not enough to change the aggregate.

This appears to be a structural property of the simulation's soil economy —
nutrients never regenerate on their own at any rate, so sustained,
slot-filling reinvestment over a long enough horizon eventually outpaces
what fertilizer alone can restore, for any strategy, `fast_seller` included
(it shows a small but non-zero 2% bankruptcy rate at the same stress
config). Multiple escalating agent-decision-policy changes (crop-ranking
risk aversion, soil-health-triggered fertilizing, critical-health triage,
endgame maturity filtering) did not move this aggregate outcome. Closing it
for real likely requires a change to shared simulation mechanics (e.g. some
form of soil regeneration), not agent policy — out of scope here. No
regression test is pinned for the 200-day/$1000 case; see the note at the
end of `tests/test_balance_policies.py`.
