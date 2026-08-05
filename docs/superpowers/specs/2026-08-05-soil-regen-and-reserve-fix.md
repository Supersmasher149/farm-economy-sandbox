# Soil Regeneration and Reserve-Gate Fix

## Goal

Given the 365-day batch report (`reports/summary_report.md`): make Purple
Haze actually reachable and worth planting for profit-seeking agents, and
make long-run survival meaningfully better than the pre-fix baseline (100%
bankruptcy, every strategy, always for `no_viable_reinvestment`).

## Findings

Three specific, compounding bugs, not one vague "the economy is hard":

1. Plot nitrogen/phosphorus/potassium (`simulation/crop_growth.py`) only
   ever decrease, never regenerate except via fertilizer. `plot.soil_health`
   already regenerates (`simulation/weather.py`, `+0.005/day`), but only on
   fallow plots -- dead code under continuous replanting, the common case
   for any strategy trying to maximize output. `economy_rules.quality_
   adjusted_profit_per_day` (which already ranks Purple Haze highest at
   healthy soil) correctly discounts nutrient-hungry crops as soil depletes,
   but since depletion never reverses, that discount only ever gets worse,
   permanently entrenching Quickweed.
2. `config/simulation_settings.json` set `operating_reserve: 100` against
   `start_money: 60` -- unreachable from day one and never reached given
   Quickweed's thin margins, so every reserve-gated agent decision (crop
   choice, upgrades, fertilizer) collapsed to its most conservative
   fallback. `ProgressionPlayer`'s `recovery_threshold = max(operating_
   reserve, cheapest_seed*3)` evaluated to 100, keeping it in permanent
   fast-crop recovery mode.
3. `agents/profit_optimizer.py` called `economy_rules.choose_crop_with_
   relaxed_reserve(..., reserve_fractions=(1.0,))`, overriding that helper's
   own default ladder (`(1.0, 0.5)`) down to a single all-or-nothing tier --
   discarding the graduated fallback the helper was built to provide, so it
   fell straight to "always plant the fastest crop" instead of trying the
   50%-reserve tier where quality-aware ranking could run.

## Design

- `config/soil.json` gains a `"regen_per_day"` key (nitrogen 0.02,
  phosphorus 0.01, potassium 0.01) -- a natural mineralization/weathering
  trickle applied to every plot every day, planted or not (unlike
  `soil_health`'s existing fallow-only regen). Threaded through
  `simulation/derived.WorldLookups.nutrient_regen` (cached once per
  world/crops pair, matching the existing pattern for `weather`/`fertilizer`
  etc.) and applied in `simulation/weather.apply_weather`, which now takes
  an optional `nutrient_regen` parameter defaulting to no regen for full
  backward compatibility.
- `config/simulation_settings.json`: `operating_reserve` lowered from `100`
  to `30` (half of `start_money`).
- `agents/profit_optimizer.py`: `reserve_fractions=(1.0,)` →
  `(1.0, 0.5, 0.25)`, restoring (and extending) the relaxation ladder.

None of these touch the already-validated risk-model constants
(`NUTRIENT_RISK_SENSITIVITY`, `CRITICAL_SOIL_HEALTH`,
`SAME_FAMILY_REPLANT_DISCOUNT`) or `config/crops.json`/`config/
fertilizer.json`'s numbers -- those already point the right direction
(Purple Haze's fertilizer marginal profit was already strongly positive);
the problem was never their arithmetic, it was that nothing let them run.

## Verified outcome

**Purple Haze adoption (the goal this fix squarely hits):** at the 365-day
default config, `profit_optimizer`'s Purple Haze share rose from ~0.01% to
consistently 6-7% across multiple batch sizes (100/300/1,000 runs), and
Greenleaf from ~6% to 14-16%. `progression_player` and other reserve-gated
strategies show the same qualitative shift. This is a real, repeatable
effect, not sample noise.

**Long-run survival (partially addressed, not solved):** bankruptcy rate
remains 100% for every strategy tested at 365 days, including the fixed
`profit_optimizer`. Calibration ablations (temporary, reverted, not part of
this fix) isolated why:

- Nutrient regen at 10x the shipped rate (0.2/0.15/0.15/day -- near-total
  daily restoration) barely changed `fast_seller`'s burn rate. Quickweed's
  own nutrient demand is small enough that even the shipped, modest regen
  rate already neutralizes most of its nutrient-driven quality discount --
  nutrient depletion was never the dominant drain for a Quickweed
  monoculture, only for higher-demand crops attempting to compete with it.
- Disabling market supply-saturation and the spot channel's 3% fee
  similarly left `fast_seller`'s burn rate almost unchanged.
- Disabling the same-family replant penalty
  (`crop_growth.harvest_multipliers`' 0.85x/0.9x yield/quality haircut on
  repeated same-family plantings) was the single largest lever found:
  `fast_seller`'s average bankruptcy day extended from ~95-100 to ~133 --
  still well short of 365.

So the persistent ~$0.5-0.6/day burn that bankrupts every monoculture
strategy is dominated by the same-family replant penalty and market-price
saturation, not primarily by nutrient depletion. That penalty is a
deliberate crop-rotation incentive (not a bug), so this fix does not touch
it. Fully eliminating long-run bankruptcy would be a larger economic
rebalance -- more starting capital, a softer family-replant penalty, or
accepting non-trivial bankruptcy as an intended outcome for a static,
non-adaptive strategy -- rather than a mechanics bug fix, and is out of
scope here.

## Testing

`tests/test_balance_policies.py::test_full_world_optimizer_diversifies_
without_trailing_fast_seller` replaces the prior `..._beats_fast_seller_
without_extra_ruin` (which asserted `avg_final_money >`, a comparison that
is not a meaningful signal once both strategies are at 100% bankruptcy and
final money is clipped near the bankruptcy trigger regardless of burn rate
-- its sign flipped between the 100-run and 1,000-run calibration samples).
The new test asserts on `crop_usage_pct` (the stable, repeatable effect of
this fix) and keeps a relaxed bankruptcy-rate/contract-failure check.

## Correction: long-horizon survival was not actually solved, and now is

The "Long-run survival" finding above turned out to be incomplete. Follow-up
instrumented tracing (not just aggregate batch numbers -- actually logging
`plot.soil_health`/`pest_pressure`/`disease_pressure`/nutrients day by day
for a continuously-farmed plot) found the real remaining bug: **three more
plot-level metrics have the exact same "only regenerates when fallow"
defect** the nitrogen/phosphorus/potassium fix above already targeted, and
no shipped agent strategy ever deliberately fallows a plot:

- `soil_health` (`simulation/actions.py`, `-0.02` per harvest event, regen
  `+0.005/day` only when `plot.crop is None`): traced falling from 0.7 to
  its 0.1 floor by day 90-135 under continuous `FastSeller` play -- almost
  exactly matching observed bankruptcy timing.
- `pest_pressure`: grows `+0.005/day` unconditionally while a crop is
  growing, decays only when fallow. Traced climbing from 0.05 to 0.79 (near
  its 0.8 cap) by day 150.
- `disease_pressure`: same pattern via rainfall; traced 0.03 -> 0.65 by day
  150.

`pest_pressure`/`disease_pressure` feed the harvest stress formula scaled by
each crop's own susceptibility (`config/crops.json`), and Purple Haze's
(1.25/1.2) is meaningfully higher than Quickweed's (0.8/0.9) -- which is why
a Purple-Haze-heavy rotation, even tested with perfectly healthy soil and
unlimited capital, still lost money (revenue covered only ~49% of expenses,
from a ~30-45% harvest rejection rate that had nothing to do with soil
nutrients). This is why the earlier ablations blamed the same-family
penalty and market saturation: those experiments never touched
`soil_health`/pest/disease, which were quietly still collapsing to their
worst values in every one of them.

**Fix:** extend the same unconditional-but-modest daily regen already
shipped for nitrogen/phosphorus/potassium to these three metrics.
`config/soil.json`'s `regen_per_day` gains `soil_health: 0.01,
pest_pressure: 0.005, disease_pressure: 0.01`; `simulation/weather.
apply_weather` (parameter renamed `plot_regen`, was `nutrient_regen`, since
it's no longer nutrient-only) applies them unconditionally, with the
existing fallow-only bonuses (`soil_health +0.005`, pest/disease `*0.9`
decay) still stacking on top -- so deliberate rest/rotation keeps paying off
faster, it's just no longer the only way to avoid guaranteed collapse.

**Verified outcome, this time for real:** with all six regen terms active,
`FastSeller` (permanent single-crop monoculture -- the worst case, since the
same-family penalty never lifts) ran **2,000 simulated days** with no
bankruptcy, ending at **$4,424**. `ProfitOptimizer` ran the same 2,000 days,
ending at **$69,761**. Reckless/neglectful/random strategies were re-checked
at 10 seeds each over 365 days to confirm this doesn't trivialize risk:
`reckless_spender` still fails 6/10, `random_agent` 10/10, `neglectful_
grower` 2/10 -- bankruptcy is still real for genuinely bad play, it's just
no longer *unavoidable for good play*.

`tests/test_balance_policies.py::test_long_horizon_disciplined_strategies_
are_self_sustaining` pins this: a 2,000-day run (via `run_single` with
`config["days"]` overridden, seed 42) for `FastSeller` and `ProfitOptimizer`
asserts no bankruptcy and growth above starting money.
