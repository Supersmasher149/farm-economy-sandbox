# Pre-Release Simulation Audit Fixes

## Scope

A pre-release correctness audit of `simulation/` only (`agents/`, `runner/`,
and `main.py` were read for context but not changed). Ten findings, F1-F10,
all fixed here.

The audit found **no determinism violations and no daily-order violations**.
`engine.run_day` fires every step in the order CLAUDE.md documents, the only
randomness source is `RandomEvents`' single `random.Random(seed)`, and the
places where float fold order or set iteration order could have leaked into
results are already pinned deliberately (`CropProfile.nutrient_demand` as an
ordered tuple, `effective_growth_days` iterating the config list rather than
the owned-upgrade set, `_build_market_profiles` matching `items_by_id`
order). Replay stability was additionally confirmed across
`PYTHONHASHSEED` 0/1/12345, which neither `pytest` nor the golden harness
covers.

## Replay impact

**F1 is the only fix that changes simulated outcomes. F2-F10 are exactly
replay-neutral**, verified by removing F1's single line and confirming all 44
golden `(strategy, seed)` combos still matched the pre-change baseline.

F1 changes 9 of 44 combos. `golden_baseline.json` is recaptured in this
change.

## Findings and fixes

### F1: evaporation was never applied to fallow plots (balance validity)

`weather.apply_weather` added rainfall to every plot, but evaporation was
applied only inside `crop_growth.update_crop_stress`, which runs only for
plots that have a crop. A fallow plot therefore took on rainfall and never
gave any back: it saturated at `1.0` and stayed there.

Measured before the fix, 10 days at rain 0.10 / evaporation 0.08 from a
start of 0.30: a fallow plot reached `1.0` while a planted plot under
identical weather reached `0.5`.

Fix: evaporation is read once per day in `apply_weather` and applied to
fallow plots in the `planted is None` branch, mirroring what
`update_crop_stress` already does for occupied ones (rainfall first, then
evaporation, floored at 0.0).

**Why the balance impact is concentrated.** Plots start at moisture 0.65 and
crop `min_moisture` thresholds are 0.30 / 0.42 / 0.48. Every strategy has
three fallow plot-days on day 0 (plots are empty when weather runs, and are
planted later the same day), but one day's evaporation leaves moisture near
0.57 — above all three thresholds — so day-0 fallow alone changes nothing.
Only *sustained* fallow drives moisture below a threshold and produces water
stress. That is exactly the divergence set observed: `random_agent` (23.1%
fallow plot-days), `diversifier` (14.6%), `reckless_spender` (3.4%) and
`neglectful_grower` diverge; every strategy that keeps its slots filled
(`fast_seller`, `profit_optimizer`, `progression_player`,
`no_upgrade_player`, `fertilizer_maximalist`, `risk_averse_grower`,
`upgrade_rusher`) is bit-identical.

The effect is large for the affected strategies — several now go bankrupt at
seeds where they previously survived — because it compounds: drier fallow
plots produce worse harvests, which means less cash, which means more slots
left fallow. This is a real economic consequence of the corrected mechanic,
not a tuning decision. `config/soil.json`'s `regen_per_day.moisture` (already
supported and validated, currently unset) is the shipped knob for softening
it if a balance pass decides the new equilibrium is too harsh.

### F2: planting bought seed it already held, and could drain cash

`_plant_open_slots` called `buy_seeds` unconditionally, so a seed left over
from a previously failed planting was never used — and nothing else in the
simulation consumes `seed_inventory`, making that cash unrecoverable.
Meanwhile `contracts._future_crop_capacity` *does* credit `seed_inventory`
as usable supply, so `is_offer_feasible` was optimistic by exactly the
amount the planting path could not spend.

Separately the loop's exit condition was "ran out of money" rather than
"planted something": if `buy_seeds` succeeded but `plant_seed` failed, the
slot stayed open and the loop bought again. Measured before the fix, a
player with a plots/`slots_total` mismatch converted its entire $100 balance
into 19 unplantable seeds in a single day.

Fix: `_can_fund_seed` treats a held seed as fundable (used for both the
per-candidate observations and the selected crop); the purchase is skipped
when a seed is already held; the loop now also requires an actually-free
plot and breaks if `plant_seed` fails.

### F3: an expiry penalty could credit the farm

`penalty = min(player.money, shortfall * rate)` took its bound from a
negative balance, so `money -= penalty` *increased* cash. Measured: a farm at
`-50.0` was paid $50 for failing a contract. `record_expense` silently
dropped the negative via its `amount <= 0` guard while
`player.contract_penalties` still accumulated it, leaving the two
irreconcilable and inflating operating profit.

Cash cannot currently go negative (every spend site guards, and
`collect_storage_liability` clamps), so this defended an invariant rather
than fixing observed output. Both operands are now floored at zero. The
penalty remains deliberately bounded by cash on hand.

### F4: fertilizer escaped the multiplier cap; its quality bonus was hard-coded

`harvest_multipliers` clamps yield to `[0.1, 1.5]`, then
`compute_harvest_outcome` added the configured fertilizer bonus *after* that
clamp — making fertilizer the one input not subject to the cap the design doc
states applies to all of them. The fertilized quality bonus was also a
hard-coded `0.05` rather than config.

Fix: the bonus is added and then re-bounded via the named
`YIELD_MULTIPLIER_BOUNDS`; the quality bonus is read from
`fertilizer.quality_bonus`, defaulting to `0.05`. Both are replay-neutral at
the shipped config — the cap is never reached in practice.

### F5: resolved contracts accumulated for the life of a run

Neither `deliver` (on completion) nor `resolve_expired` removed resolved
contracts from `active_contracts`, so the list grew monotonically and was
re-scanned every day by `resolve_expired` and every agent's delivery hook —
O(n²) over a run plus unbounded memory, which matters at the 2,000-day
horizon `test_balance_policies` pins and for multi-million-run batches.

Every consumer already filters on `not resolved`, so pruning is
behaviour-preserving. Completion and failure totals remain in
`contracts_completed` / `contracts_failed`.

### F6/F10: derived caches ignored their config argument; unstable fold order

`WorldLookups.effective_storage` and `.processing_capacity` keyed only on
`frozenset(upgrades_owned)`, so a call with the same upgrades but a different
config returned the first config's answer. Measured:
`effective_storage({"capacity": 999}, ...)` returned `{"capacity": 100}`.
Correct today (the engine always passes the same object) but a trap for the
next caller. Both now key on the config's identity as well, holding a strong
reference so `id()` cannot be recycled.

`processing_capacity` also folded over the owned-upgrade set unsorted. That
is safe while configuration forces integer amounts, but it would silently
break replay the moment a fractional effect were added, so it now sorts —
matching what `effective_storage` already did.

### F7: `losses_by_cause` mixed measures

`crop_loss` counted harvest events while `rejected_quality` and `spoilage`
counted units, so the dict could not be summed or compared. Keys are now
`crop_loss_events`, `rejected_quality_units`, `spoilage_units`. Nothing in
`metrics/` reads this dict yet; the reported loss rate uses
`total_crops_lost` / `total_harvest_events`, which were and remain
consistent event counts.

### F8: plot dynamics were constants, not configuration

`config/soil.json` made the *regeneration* side of soil, pest, and disease
tunable while the *depletion* side stayed hard-coded across `weather.py`,
`actions.py`, and `crop_growth.py` — so an ablation like the one the
2026-08-05 soil-regen work ran on the same-family replant penalty required
editing simulation code, against CLAUDE.md's "rebalancing means editing that
JSON."

A new `soil.dynamics` section, resolved once per world into
`derived.SoilDynamics` and reached via `WorldLookups.soil_dynamics`, now
covers the harvest soil-health cost and floor, fallow decay/regen rates,
pest/disease growth and caps, the same-family yield and quality penalties,
and the soil-health yield curve. Every default equals the constant it
replaced, so a config omitting the section reproduces prior behaviour
exactly. `contracts.fallback_price_multiplier` (was a bare `1.15`) and
`fertilizer.quality_bonus` (F4) are configurable on the same principle.
`configuration.SOIL_DYNAMICS_BOUNDS` validates the section and is pinned by
test to stay in sync with `derived.DEFAULT_SOIL_DYNAMICS`.

### F9: production cost and processing revenue were never real

`InventoryLot.unit_cost` was `seed_cost / amount`, excluding watering and
fertilizer, and `PlayerState.processing_revenue` was declared but never
assigned — so any margin derived from either was wrong or zero. `unit_cost`
was also entirely unread: every caller of `inventory.consume` discarded the
cost it returned, so this was dead accounting rather than a live wrong
number.

`PlantedCrop.accrued_cost` now tracks real cash spent per planting (seed plus
any fertilizer and watering) and becomes the harvested lot's `unit_cost`;
`markets.sell` credits `processing_revenue` when the lots sold are products.

Surfacing processing margin in `reports/summary_report.md` is a `metrics/`
change and was out of this audit's scope — the state is now correct, the
report still does not show it.

## Not changed

`engine.run_day` and `actions.water_farm` read `agent.watering_diligence`
directly rather than through a decision hook. It is a documented attribute on
`Agent` and the strategy-control design defines `NeglectfulGrower` by it, so
this is intentional; it is noted only because it means `should_water()`
returning True does not guarantee watering.

The bankruptcy rule is unchanged, per the diagnostic-reporting design's
explicit "the existing bankruptcy rule is preserved."

## Verification

- Full suite green: 184 passed (159 pre-existing + 25 added in
  `tests/test_prerelease_audit_fixes.py`).
- `ruff check` and `ruff format --check` clean.
- F2-F10 confirmed replay-neutral by isolating F1 (all 44 combos matched the
  old baseline with F1's line removed).
- `golden_baseline.json` recaptured for F1; 9 of 44 combos changed, all in
  strategies that leave plots fallow.
- One pre-existing test was updated:
  `test_soil_regen_moisture_is_applied_at_runtime` passed a weather dict with
  no `evaporation` key and so depended on fallow plots never evaporating. It
  now pins `evaporation: 0.0` explicitly, isolating the regen term it is
  actually testing.
