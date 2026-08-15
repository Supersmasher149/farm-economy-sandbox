# vectorized/ — high-throughput Monte Carlo sampler

A **separate, experimental** simulator, not a replacement for `simulation/`.
It exists to answer one question the main engine structurally can't: what do
aggregate outcomes look like across **millions** of runs, when you don't need
per-day history and don't need bit-exact replay of a specific seed?

Read this before touching `vectorized/config_arrays.py` or comparing this
module's numbers to `simulation/`'s — they are not meant to agree, for
reasons below.

**Status: Phase 1 ("crop/soil physics parity"), Phase 2 ("storage &
spoilage"), Phase 3 ("markets", single-channel scope), Phase 4
("processing"), Phase 5 ("contracts", simplified scope), and Phase 7
("upgrades") complete.** Crop growth, soil chemistry, weather, watering,
fertilizer, and crop unlocking are ported from the real config-driven
mechanics. Storage/spoilage: lots age, downgrade quality, fully spoil, and
get capacity-trimmed exactly like `simulation/inventory.py`. Markets: every
crop (and, as of Phase 4, every processed product) gets a fresh
supply/demand price roll each day (single effective channel, not the real
engine's 5-channel/fee/reputation system) and every lot still standing
after that day's aging/spoilage/trim is sold in full at that price,
crediting `money`/`total_revenue`/`total_harvest` for real — Phase 2's
"shadow accounting" (money credited instantly at harvest, storage stats
informational-only) is gone; see `kernel.py`'s module docstring for exactly
what's simplified and why. Processing: `config/processing.json`'s recipes
consume a crop and cash to occupy one of a small number of global job
slots, completing into a sellable processed-product lot -- a fixed policy
(try each recipe in config order, start it if affordable) shared by all 3
fixed strategies, same simplification style as markets' "sell everything."
Contracts: `config/buyers.json`'s 7 buyers each get one contract "slot" --
offered, accepted opportunistically on any current stock, delivered
incrementally, and either completed (reputation/relationship up) or failed
at the deadline (real cash penalty, reputation/relationship down) -- the
real engine's multi-day production-forecast scheduler (`is_offer_feasible`)
is what's simplified away, not the offer/deliver/penalty mechanics
themselves. Upgrades: `config/upgrades.json`'s all four effect types are
ported -- more growing plots, more processing job slots, shorter growth
times, bigger/longer-lived storage -- bought by a fixed cash-buffer policy
shared by all 3 strategies, same "simplify the agent" pattern as markets/
processing/contracts. **This phase pushed wall time over the <60s/1M-runs
target for the first time (see Performance below) — read that section
before treating this module as still comfortably inside budget.** Only the
real 11-strategy agent roster is **not** ported — see Roadmap below.

## Why this is a separate tool, not a faster main engine

Two of the main engine's invariants (`CLAUDE.md`) are incompatible with
"vectorize across a million runs at once," by construction, not by omission
— these don't change as more phases land:

| Main engine (`simulation/`) | This module (`vectorized/`) |
|---|---|
| `random.Random(seed)`, one global stream, every draw serialized through it | `splitmix64`, one independent stream per `(run, plot)` — see `rng.py` |
| Bit-exact replay of recorded seeds is load-bearing (`replay-guard` skill, golden baseline) | No bit-exactness claim of any kind against `simulation/` |
| Daily history retained, agents are Python objects | Only final `money`/`total_harvest`/`total_revenue` per run; no per-run object at all |

A `random.Random` stream can't be split across 100,000 parallel runs without
serializing them right back together, so the two determinism models are
fundamentally different, not two implementations of the same one. Don't
expect (or try to make) this module reproduce a `simulation/` seed's output.

A third row used to be here — "full config-driven economy vs. 3 illustrative
crops" — and it's now closed entirely: crop/soil physics *does* read the
real `config/crops.json` + `config/soil.json` + `config/watering_settings.json`
+ `config/fertilizer.json` + `config/weather.json`, storage/spoilage reads
`config/storage.json`, markets reads `config/markets.json`, processing reads
`config/processing.json`, contracts read `config/buyers.json` +
`config/contracts.json`, and upgrades read `config/upgrades.json` (see
`config_arrays.py`). Every real config file that shapes the economy is now
read; what's left unported is agent decision logic (Roadmap item 6), not
config coverage.

If you need bit-exact, config-driven, full-economy runs: use `main.py batch`.
If you need aggregate statistics — mean/variance/distribution of outcomes —
across millions of trials of a physics-accurate-but-economy-simplified
model, in seconds instead of minutes: this module.

## Install

```bash
uv pip install --python .venv/bin/python3 -r requirements-fast.txt
# or: pip install -r requirements-fast.txt
```

Nothing outside `vectorized/` and `scripts/vectorized_*.py` imports this
package or requires numpy/numba — `main.py`, `simulation/`, `runner/`,
`metrics/` are unaffected by whether it's installed, matching the
`requirements-viz.txt` precedent for matplotlib.

## Usage

```python
from vectorized.orchestrator import run_millions

result = run_millions(
    total_runs=1_000_000,
    num_plots=10,
    num_days=365,
    master_seed=42,
    max_memory_gb=2.0,
)
print(result.summary())
```

```
1,000,000 runs x 10 plots x 365 days in 84.25s (11,869 runs/s)
  overall money:   mean=  4196.88  stddev= 6382.07  min=     0.00  max= 24801.29
  overall harvest: mean=  1910.11  stddev= 1199.89
  overall spoiled: mean=     0.00  stddev=    0.00  (storage cost mean=  0.00, shadow accounting -- not deducted from money)
  overall processed: mean=  85.10  stddev=   57.20
  overall contracts: completed mean=88.01  failed mean= 3.65  penalties mean= 45.28
  overall upgrades owned: mean=2.33  max=4
  greedy       (n= 333,330): money mean=    17.84 stddev=   44.66  harvest mean=  570.14
  conservative (n= 333,340): money mean=   270.02 stddev=  101.78  harvest mean= 2406.83
  random       (n= 333,330): money mean= 12302.91 stddev= 4856.79  harvest mean= 2753.36
```

`overall_spoiled` reading ~0 here is still expected, not a bug -- same
reason as Phase 3/4: inventory rarely survives past the day it's created.
`overall money`/`overall harvest` nearly doubled again from Phase 5's
numbers (2,259.72 → 4,196.88; 1,196.69 → 1,910.11): a run that buys
`capacity_1` isn't just richer, it has more growing slots to plant into, so
the harvest/money growth here is upgrades compounding on top of contracts'
already-outsized payout, not a new independent effect. `overall upgrades
owned` averaging 2.33 of 4 (max 4, confirmed bought together in the same
run by `check_upgrades_purchased`) means the typical run across all three
strategies buys roughly half the catalog over a year -- see `by_strategy`
below for how unevenly that lands (`conservative`'s tighter cash buffers
mean it owns far fewer of the catalog than `greedy`/`random` by day 365).

Measured on a single CPU core's worth of `numba(parallel=True)` work (see
Performance below): **1,000,000 runs in ~84.3s, peak RSS ~734MB**. Peak
memory still clears the 2GB target comfortably (**2.8x margin**), but wall
time now **exceeds** the <60s CPU target — **0.71x**, not a margin, a
miss. This is the first phase to fail the budget outright; see Performance
below for why, and the Migration notes section for what that means for
whatever comes next.

```bash
# validate the numba kernel against the pure-Python sequential reference
python3 scripts/vectorized_validate.py

# benchmark runs/sec at a few chunk sizes, project to 1M, report peak RSS
python3 scripts/vectorized_benchmark.py
python3 scripts/vectorized_benchmark.py --sizes 1000 20000 100000
python3 scripts/vectorized_benchmark.py --compare-existing-engine   # also times main.py batch
```

## Config source (`config_arrays.py`)

Phase 1 reversed the first build's deliberate choice not to read
`config/*.json` — see that module's docstring for the full rationale. Short
version: physics *parity* means tracking the real balance numbers, so
`load_vector_config()` reads:

- `config/crops.json` — per-crop economics, growth, stress inputs, nutrient
  demand, family, unlock requirements (only `type: "total_revenue"` is
  understood; anything else fails to load loudly rather than silently
  treating the crop as always-unlocked), (Phase 2) `shelf_life_days`, and
  (Phase 3) `seasonal_demand`
- `config/soil.json` — initial plot values, regen-per-day, rotation/
  soil-health dynamics
- `config/watering_settings.json`, `config/fertilizer.json`
- `config/weather.json` — seasonal temperature/rain/evaporation
- `config/storage.json` (Phase 2) — `capacity`, `daily_cost`,
  `shelf_life_multiplier`; also derives `lots_per_plot`, the provably-safe
  per-plot lot-slot bound (see `VectorConfig`'s docstring comment)
- `config/markets.json` (Phase 3) — `minimum_supply_multiplier`,
  `supply_decay`; NOT the 5-channel `channels` list (`price_multiplier`/
  `min_quality`/`daily_capacity`/fees per channel) or reputation, since
  this phase models one effective channel, not the real 5-channel system
- `config/processing.json` (Phase 4) — `base_capacity`, the `products` list
  (each becomes an item-space entry, see below), and the `recipes` list
  (input/output items and quantities, `min_quality`, `processing_days`,
  `cost`, `shelf_life_days`)
- `config/buyers.json` + `config/contracts.json` (Phase 5) — each buyer's
  `items` (resolved to item-space indices, loud failure on an unknown id,
  same policy as recipes), `quantity_range`, `min_quality`,
  `contract_price_multiplier`, `deadline_days`, `penalty_rate`,
  `min_reputation`, `relationship_bonus_rate`; `contracts.json`'s
  `offer_interval_days`, `offer_expiry_days` (default 3, not currently set
  in the shipped file), `relationship_gain_per_delivery`,
  `relationship_loss_per_failure`, `relationship_bonus_cap`. NOT
  `production_safety_factor` or `fallback_price_multiplier` -- both only
  feed the real engine's `is_offer_feasible` forecast scheduler, which
  this phase doesn't port (see kernel.py's module docstring)
- `config/upgrades.json` (Phase 7) -- read generically by `effect["type"]`
  (`capacity`, `growth_time_reduction`, `storage`, `processing_capacity`),
  not by hardcoded upgrade id, matching `simulation/derived.py`'s own
  type-dispatch fold; an unrecognized effect type fails to load loudly. Two
  new derived scalars, `total_capacity_bonus`/`total_processing_capacity_bonus`
  (sum across the whole catalog -- the max a run could ever unlock), size
  the plot/job-slot max-width allocation described in the Data contract
  section below; `lots_per_plot`'s bound (previous bullet) is recomputed
  using the *worst case* growth_days/shelf-life across every owned upgrade,
  not the base config values -- see `config_arrays.py`'s docstring

into a single frozen `VectorConfig` of numpy arrays, loaded once and passed
through `state.init_runs` / `kernel.simulate_chunk` rather than read per-day
— the same "resolve once, reuse" shape as `simulation/derived.py`'s
`WorldLookups`, just arrays instead of per-id dict lookups since the kernel
needs `array[crop_idx]`.

Phase 4 also unified crops and processed products into one **item space**
(`num_items = num_crops + num_products`): `base_price`/`price_variation`/
`seasonal_demand`/`effective_shelf_life_days` grew from crop-only arrays to
item arrays, crop indices unchanged (0..num_crops-1), products appended
after (num_crops..num_items-1) — the same unification
`simulation/derived.py`'s `items_by_id` already does for the real engine,
so `lot_item_id` can hold either without a second lot-array system. See
`config_arrays.py`'s docstring for the one assumption this required (each
product has exactly one producing recipe, true of every shipped recipe
today) and what happens if that stops being true.

## Data contract (`state.py`)

Structure-of-Arrays, flat numpy arrays, no per-run Python object. Grown from
the original 8-field layout to mirror `simulation/state.py`'s
`PlotState`/`PlantedCrop` field-for-field. **Phase 7: `P` and `J` below are
now *max* widths**, `P = num_plots + config.total_capacity_bonus` and
`J = config.base_capacity + config.total_processing_capacity_bonus` (the
most plots/job-slots a run could ever unlock, not the starting count) --
`active_plots`/`active_job_slots` track how much of that width a given run
has actually unlocked, see this module's Phase 7 docstring:

```
# run-level
money[B]                  float32     total_harvest[B]          float32
total_revenue[B]          float32     strategy_id[B]             int8
total_spoiled[B]           float32    total_storage_cost[B]      float32
total_processed[B]         float32    reputation[B]              float32
total_contracts_completed[B]  float32 total_contracts_failed[B]  float32
total_contract_penalties[B]   float32
active_plots[B]                int16   active_job_slots[B]          int16

# plot-level: soil
moisture[B,P]              float32    nitrogen[B,P]              float32
phosphorus[B,P]             float32   potassium[B,P]             float32
ph[B,P]                     float32   soil_health[B,P]           float32
pest_pressure[B,P]          float32   disease_pressure[B,P]      float32

# plot-level: what's planted
crop_type[B,P]                int8    growth_stage[B,P]            int8
days_to_harvest[B,P]          int16   previous_crop_family[B,P]    int8
fertilized[B,P]                int8

# plot-level: accumulated stress (reset at each planting)
water_stress[B,P]           float32   nutrient_stress[B,P]       float32
temperature_stress[B,P]     float32   pest_stress[B,P]           float32
disease_stress[B,P]         float32

# plot-level: watering/neglect
neglect_days[B,P]             int32   last_watered_day[B,P]       int32

# rng streams (not in the prompt's field list -- see RNG strategy below)
rng_run_state[B]             uint64   rng_plot_state[B,P]         uint64

# lot-level (Phase 2): shape (B, L), L = P(max) * lots_per_plot + J(max) (Phase
# 4 reserves +J for processed-product lots) -- fixed-size mirror of
# simulation/state.py's InventoryLot list, see config_arrays.py's
# lots_per_plot docstring comment for why that bound is provably safe
lot_item_id[B,L]               int8    lot_quantity[B,L]           int32
lot_quality[B,L]                int8   lot_age_days[B,L]           int16

# job-level (Phase 4/7): shape (B, J), J = config.base_capacity +
# config.total_processing_capacity_bonus (max width) -- fixed-size mirror of
# simulation/state.py's ProcessingJob list, global (not per-plot) since
# processing capacity is a global resource; active_job_slots (run-level,
# above) gates how many of these J slots a given run can actually use
job_output_item_id[B,J]        int8    job_output_quantity[B,J]    int32
job_completion_day[B,J]        int32

# buyer-level (Phase 5): shape (B, K), K = config.num_buyers -- exactly one
# contract "slot" per buyer (not a derived bound -- see state.py's docstring)
contract_state[B,K]            int8    contract_item_idx[B,K]      int8
contract_remaining[B,K]        int32   contract_unit_price[B,K]    float32
contract_min_quality_rank[B,K] int8    contract_deadline_day[B,K]  int32
contract_expiry_day[B,K]       int32   contract_penalty_rate[B,K]  float32
buyer_relationship[B,K]        float32

# upgrade-level (Phase 7): shape (B, U), U = config.num_upgrades -- exact
# catalog bound, same pattern as buyer-level above
upgrade_owned[B,U]             int8
```

76 bytes/run-level (+4 for `active_plots`/`active_job_slots`) + 74
bytes/plot + 8 bytes/lot-slot + 9 bytes/job-slot + 20 bytes/buyer + 1
byte/upgrade, **2,205 bytes/run at `num_plots=10`** (Phase 7 grows the
*allocated* widths to `P=18`/`J=3`/`L=75` -- `10 + total_capacity_bonus=8`,
`1 + total_processing_capacity_bonus=2`, `18*lots_per_plot(4)+3` -- up from
Phase 5's 1,235 bytes/run at the same starting `num_plots=10`, +79%, almost
entirely from `P` growing 80% and `lots_per_plot` growing from 3 to 4 (the
worst-case shelf-life-multiplier fold, see `config_arrays.py`'s docstring).
`bytes_per_run(num_plots, num_lot_slots, num_job_slots, num_buyers,
num_upgrades)` in `state.py` is the exact closed-form sum, now taking the
*max* widths, not the starting `num_plots` a caller passes to
`run_millions`; `DEFAULT_MAX_CHUNK = 100_000` still binds before the 2GB
memory budget does (see Memory strategy) -- this byte growth affects chunk
sizing, not peak RSS at a fixed run count, since `choose_chunk_size` just
shrinks the chunk to compensate.

## RNG strategy

`rng.py`'s docstring has the full rationale; short version: every
`(run_index, plot_index)` pair gets its own `splitmix64` stream, seeded
deterministically from `(master_seed, run_index)` and then `(run_seed,
plot_index)`. Because each run's stream depends on nothing but its own global
index, **given the same strategy, results are provably independent of chunk
size and chunk offset** — `scripts/vectorized_validate.py`'s
`check_chunk_size_independence` runs the same global run index, *with an
explicit fixed strategy array*, inside three different chunk shapes and
checks they agree at the state/kernel layer.

**That guarantee doesn't extend to which strategy `run_millions` itself
assigns a given global run index.** `run_millions`'s strategy-of-run
bucketing (the `fractions = (np.arange(this_chunk) + 0.5) / this_chunk` line)
is deliberately computed *within* each chunk, not from each run's global
index over `total_runs` — the goal is that any prefix of processed chunks
stays a representative, correctly-weighted sample even if a run is
interrupted partway (see that line's comment), not that global run index 5
gets the same strategy every time. One consequence: re-running `run_millions`
with the same `total_runs`/`master_seed`/`strategy_weights` but a different
`max_chunk` (or a different `max_memory_gb`, since that feeds
`choose_chunk_size`) assigns a different global-index-to-strategy mapping,
so the aggregate `BatchResult` stats — `overall_money.mean` included — come
out numerically different, not just float-summation-order noise, because a
different subset of runs actually got simulated as each strategy. This is
harmless for what the public API actually promises (each chunking still
produces a valid, representative sample of the same population, and
`by_strategy_*` counts stay proportional to `strategy_weights` in every
chunk), but it does mean **`run_millions`'s aggregate output is not
reproducible across different chunk sizes for the same seed** the way the
raw kernel/state layer is — `tests/test_vectorized.py`'s
`TestRunMillions.test_multi_chunk_matches_single_chunk` (formerly a
too-strong "matches exactly" assertion that this section's earlier wording
predicted and that a real run immediately falsified) checks the accurate,
weaker property: per-chunk representativeness and total counts, not
numerical equality across chunkings. If exact reproducibility independent of
`max_chunk`/`max_memory_gb` is ever needed, the fix is to key
`strategy_of_run` off each run's global index over `total_runs` instead of
its chunk-local position — a real behavior change to weigh against the
interrupted-partway property it would give up, not a one-line correction,
so it's left as a documented limitation rather than changed here.

Draw counts per plot per day are no longer fixed (Phase 1 branches on real
plot state rather than always drawing a fixed sequence): an empty plot draws
1 value (crop pick), a still-growing plot draws 2 (watering, fertilizing),
and a plot that matures today draws 2 more (loss check, yield roll) before
immediately trying to replant in the same day. This is fine for
determinism — kernel.py and reference.py both branch on identical state, so
both draw identically — see kernel.py's docstring.

Phase 3 added a separate **run-level, once-per-day** draw: `num_items`
values as of Phase 4 (one price roll per crop *and* per processed product;
`num_crops` before Phase 4 added products) from the `run_state` stream, not
`rng_plot_state` — market pricing is a daily, run-wide event, not a
per-plot one, the same reasoning that already put weather on `run_state`.
This replaced the harvest-time `price roll` draw the paragraph above used
to describe (Phase 1/2 rolled a price per harvest event, on `plot_state`;
Phase 3 rolls a price per item per day, on `run_state`, once, used by every
plot and every lot that transacts that item that day) — see kernel.py's
module docstring for the full before/after.

Phase 4's processing (jobs completing, jobs starting) draws **no RNG at
all** — recipes are deterministic (fixed cost/output, no roll), and the
"which recipe to start" policy is a fixed config-order preference, not a
randomized choice, so it adds zero new draws to either stream.

Phase 5 added **2 draws per buyer, only on interval days, only for a buyer
whose slot is empty**: one to pick an eligible item (`rng.choice` mirror)
and one to roll a quantity within the buyer's range (`rng.roll_yield`
mirror), both from `run_state` -- contract offers are a run-wide event, not
tied to a plot, same reasoning as market pricing. Accept/deliver/resolve
draw nothing: the simplified accept policy ("any current stock") and the
delivery/deadline logic are both deterministic given the day's state.

## Memory strategy

`orchestrator.choose_chunk_size` picks `chunk_size = min(max_chunk,
floor(max_memory_gb * 2^30 / bytes_per_run(num_plots)))`. `run_millions`
allocates one chunk, seeds it, runs it, folds its results into
`StreamingStats` (Welford, batch/parallel form — see `stats.py`'s
docstring), then `del state; gc.collect()` before the next chunk. Peak
resident memory is therefore bounded by **one chunk's arrays**, not by
`total_runs` — the Performance table below shows it plateauing once
`total_runs` exceeds a few chunks' worth, rather than continuing to climb
with `total_runs`.

## Validation (component E)

`scripts/vectorized_validate.py` checks six things, not one. Its check
functions are also wrapped as regular pytest tests in
`tests/test_vectorized.py` (`pytest.importorskip`-gated on numpy/numba, same
pattern `tests/test_visualize.py` uses for matplotlib) so they run as part of
`python3 -m pytest` whenever `requirements-fast.txt` is installed, and CI's
`vectorized` job (`.github/workflows/ci.yml`) installs it and runs them on
every push -- this module is no longer validated only when someone
remembers to run the script by hand.

1. **Kernel ≡ reference**: `vectorized.kernel`'s numba `prange`-parallel core
   and `vectorized.reference`'s pure-Python scalar per-run loop are two
   implementations of the *same* algorithm (same branch order, same draw
   order, same float32 rounding on every state write — see both modules'
   docstrings, and kernel.py's per-block comments naming which real
   `simulation/` function each block mirrors). They're checked to agree
   within float32 tolerance across a spread of seeds, run indices,
   strategies, and plot counts — 144 combinations, all currently passing,
   now also comparing `total_spoiled`/`total_storage_cost`/
   `total_processed`/`reputation`/`total_contracts_completed`/
   `total_contracts_failed`/`total_contract_penalties` alongside
   `money`/`total_harvest`/`total_revenue`.
2. **Chunk-size independence**: the same global run index gives the same
   result whether it's simulated alone or embedded in chunks of different
   sizes and offsets (the RNG property above, checked directly).
3. **Storage capacity trim + spoilage (Phase 2)**: a tiny-`storage_capacity`
   config variant forces the FEFO capacity-trim branch and full age-out
   spoilage to actually execute — not just be numerically dormant — across
   27 (seed, strategy, plot-count) combinations, asserting kernel ≡
   reference there too. `simulate_chunk` itself raises loudly if any run
   overflows its `lots_per_plot` + `base_capacity` bound (see kernel.py's
   `overflow_events` check), so reaching these assertions at all already
   proves that invariant held.
4. **Processing occurs (Phase 4)**: confirms `total_processed > 0` for at
   least one combination across the same 27-combination grid, under the
   *default* config — unlike Phase 2's check, no forced config variant is
   needed, since both shipped recipes are cheap and gated by inventory
   every fixed strategy routinely harvests.
5. **Contracts occur (Phase 5)**: confirms `total_contracts_completed > 0`
   *and* `total_contracts_failed > 0` across the same 27-combination grid,
   again under the default config -- both outcomes matter here, since a
   nonzero failure count is what proves the simplified accept policy
   ("any current stock," not the real engine's forecast-gated accept)
   actually produces undeliverable contracts and real penalties, not just
   an easier version of the real thing that always succeeds.
6. **Upgrades get purchased (Phase 7)**: confirms both ends of the range
   across the same 27-combination grid, under the default config -- at
   least one combination buys nothing (the "not affordable/willing yet"
   path stays reachable), and at least one buys every upgrade in the
   catalog (`capacity_1`'s plot growth *and* `processing_1`'s job-slot
   growth exercised together in the same run, not just one or the other).
   `active_plots`/`active_job_slots`/`upgrades_owned_count` are now
   compared alongside the money/harvest/storage/contract fields, so a bug
   in the run-end write-back of either counter (a local variable during the
   run, only written to its `BatchState` array once, at the very end) would
   be caught here even though it can't affect that same run's own
   trajectory.

255 checks currently pass. None of these validate against `simulation/`'s
real engine — see "Why this is a separate tool" above for why that
comparison isn't meaningful.

## Deviations from the prompt, and why

- **`prange` over runs, not day-outer `np.where` masking.** Numba's automatic
  array-parallelization of hand-written masked numpy expressions is
  unreliable on code with this much branching (irrigation-cost gating,
  harvest resets, per-strategy dispatch). `prange` over independent runs is
  numba's own documented idiom for "many independent simulations," and every
  run is still fully self-contained (its own RNG streams, no cross-run
  dependency) — see `kernel.py`'s docstring for the full argument.
- **Config-driven crops (`config_arrays.py`), not `config/*.json`-agnostic
  constants.** Reversed from the first build's choice, once physics parity
  was the explicit goal — see `config_arrays.py`'s docstring. As of Phase 7,
  every real config file that shapes the economy is read, including
  `config/upgrades.json`.
- **Upgrade-growable dimensions (plots, job slots) are allocated at their
  max width for every run, not grown on demand.** `capacity_1` and
  `processing_1` each grow a fixed-shape array dimension that every earlier
  phase sized once per batch and shared by every run. A per-run variable-
  shape array isn't expressible in a dense SoA layout, so `state.py`
  allocates `P`/`J` at `num_plots + total_capacity_bonus` /
  `base_capacity + total_processing_capacity_bonus` for *every* run --
  including runs whose agent never buys either upgrade -- and
  `active_plots`/`active_job_slots` gate how much of that width a given run
  has actually unlocked. The alternative (a second, smaller allocation only
  for runs that buy `capacity_1`, requiring a ragged/two-tier chunk layout)
  was rejected as a much larger restructuring than this phase's scope
  asked for; this was the direct cause of Phase 7's performance miss (see
  Performance below) -- worth knowing before assuming it was an accident
  rather than a considered tradeoff. A follow-up pass added an occupied-
  slot free-list (`kernel.py`'s `_insert_lot`/`_remove_lot`) so the
  *lot-slot* dimension's max-width cost no longer applies to every
  lot-slot-scanning block -- but `P` itself is still allocated at max
  width and gated only by `active_plots`, so the per-plot loop still pays
  for 18 plots' worth of width as a run's `active_plots` grows, even
  though most of the six lot-slot blocks now don't. See Migration notes
  for why that's the likelier remaining cost driver, not lot-slot scanning.
- **Markets are single-channel, not the real 5-channel system.** No
  `spot`/`wholesale`/`farm_stand`/`processor`/`specialty` price
  multipliers, quality gates, daily capacities, or fees; the real per-
  channel `min_reputation`/reputation-bonus gating isn't ported either
  (Phase 5's `reputation` field gates *contract* eligibility only, not
  market channels). One effective channel: a daily per-item (crop or
  processed product) supply/demand price, sold in full every day. Also no
  per-strategy `choose_sales` logic — every lot still standing after a
  day's aging/spoilage/trim is sold in full, for all 3 fixed strategies,
  the simplest real strategy's behavior (`fast_seller.choose_sales` dumps
  all inventory the same way) — see `kernel.py`'s module docstring for the
  two behavioral consequences worth knowing (storage/spoilage gets rarer
  but stays real; a plot's same-day spend decisions still use yesterday's
  cash, not today's just-credited sale revenue).
- **Processing has a fixed policy, not per-strategy `choose_processing`
  logic.** Try each recipe in `config/processing.json`'s order, start it
  if a job slot is free and there's enough input inventory (at the
  recipe's minimum quality) and cash — same policy for all 3 fixed
  strategies, same "simplify the agent, not just the mechanic" approach as
  markets' sell-everything. `base_capacity` (currently 1) bounds
  concurrent jobs globally, not per plot; see `kernel.py`'s module
  docstring for the full day-order interaction with selling (processing
  gets first claim on same-day inventory, since it runs before the
  sell-all-lots step).
- **Contracts skip the real production-forecast scheduler, and give each
  buyer one slot instead of unlimited concurrent offers.** No
  `is_offer_feasible` -- no multi-day scheduling of future harvests/
  processing batches against buyer deadlines, no cash reservation across
  competing recipes. Accept is simplified to "any current stock (>0) of
  the item at the required quality" -- current inventory only, no
  forecasting -- which is *more* permissive than the real engine's
  forecast-gated accept, so contracts fail their deadline more often here
  than they would in `simulation/`, not less; see kernel.py's module
  docstring for the exact accept/deliver/resolve policy and why only 2 of
  the real 11 strategies ever used the dropped scheduler anyway. Also
  simplified: one contract slot per buyer at a time (`state.py`'s
  docstring), instead of the real engine's unlimited concurrent offers/
  active contracts per buyer deduplicated only by exact `buyer-item-day`.
- **Upgrade purchases use a cash-buffer threshold, not the real
  `should_buy_upgrade_within_budget` gate.** The real 2 strategies that use
  it (`profit_optimizer`, `progression_player`) check a purchase cooldown,
  a cumulative-spend-vs.-peak-cash cap, and (when priceable) a payback-
  period test -- a composite scheduler-like gate, not a threshold. Same
  "simplify the agent, not just the mechanic" pattern as markets/
  processing/contracts: a `money >= cost * buffer` check (buffer 1.5x for
  greedy, 4.0x for conservative, a coin-flip for random), same shape as the
  existing fertilize/water thresholds, config-catalog order, one shared
  cash pool per run.
- **3 fixed strategies, not the real 11-agent roster.** `agents/*.py`'s
  strategies have real config-driven decision trees (`profit_optimizer`,
  `progression_player`); this module's greedy/conservative/random are
  threshold masks over the (now-real) physics, not ports of those agents.
  Porting the real roster is its own future phase — see Roadmap.
- **This phase is where "no JAX migration needed" stopped being true.**
  Every phase through Phase 5 cleared the <60s CPU / 1M-runs target, with a
  shrinking but still-real margin (20x → 2.7x → 2.3x → 4.4x → 2.9x →
  1.3x). Phase 7 misses it outright -- ~91.5s at 1M runs, **0.66x of
  budget** (see Performance below) -- because of a considered tradeoff
  (allocating upgrade-growable dimensions at max width for every run, not
  just the runs that reach them; previous bullet), not a regression that
  crept in unnoticed. This was flagged and accepted before implementation,
  not discovered after: the alternative (only allocating the extra
  width for runs that actually buy the relevant upgrade) is real
  engineering work -- a ragged/two-tier chunk layout -- deferred rather
  than attempted in this phase, and is the natural next place to look
  before reaching for `prange` retuning or JAX/GPU. See Migration notes
  below for what a human should decide before Phase 6 (full agent roster,
  which would add yet more per-day work on top of an already-over-budget
  kernel) proceeds.

## Risks: the "isolate what can't be vectorized" escape hatch

All three strategies (Greedy/Conservative/Random) mask-vectorize cleanly —
nothing in `run_millions` needs the fallback. `orchestrator.
run_isolated_strategy_fallback` is still a real, exercised path (not just a
claim): it runs `vectorized.reference`'s scalar per-run loop for one strategy
id, in small batches, folding into the same `StreamingStats` the vectorized
path uses. If a future strategy has decision logic that can't be expressed as
array masks (e.g. it needs cross-plot search, not just per-plot thresholds —
plausible once the real agent roster is ported, see Roadmap), route its
`strategy_id` through this function instead of the kernel and merge its
`StreamingStats` with the rest — the pattern to follow is that function's
body and docstring.

## Performance

**Phase 7 is the first phase to miss the <60s CPU / 1M-runs target --
and a first optimization pass narrowed the miss but hasn't closed it.**
Measured on this machine, `.venv` (Python 3.12.9, numpy 2.5.2, numba
0.67.0), starting `num_plots=10`, `num_days=365`, one `run_millions` call
per process (via `/usr/bin/time -l`, isolated rather than a running
high-water mark across sizes):

| Version | Wall time (1M) | Throughput | Peak RSS | vs. 60s target |
|---|---:|---:|---:|---|
| Initial Phase 7 (max-width `range(L)` scans) | 84–92 s | ~10,900–11,900 runs/s | ~735–780 MB | 0.65–0.71x (MISS) |
| **+ occupied-slot free-list** (current) | **73–74 s** | **~13,500–13,700 runs/s** | **~840–880 MB** | **0.81–0.82x (still MISS)** |

(Two isolated 1M-run measurements at each version, quoted as a range rather
than a single number -- ordinary machine noise between otherwise-identical
isolated runs, same as Phase 7's first measurement already showed. Phase
5's comparable figure, for scale: 44.82s, ~22,300 runs/s, 740MB at 1M.)

(numba JIT compilation of `simulate_chunk` happens once per process on first
call and is excluded from these figures, same as the main engine's
Cython/`_fastplot` builds are one-time costs excluded from
`sample_profile.py` numbers.) `--compare-existing-engine` on
`vectorized_benchmark.py` times `main.py batch` alongside this for a
wall-clock reference point — see that script's docstring for why it's not an
apples-to-apples comparison of the same economic model.

**What the free-list fixed, and what it revealed it didn't.** The
Migration notes below (option 1, chosen by the user over kernel
micro-tuning or JAX) diagnosed `num_lot_slots` (L) growing 2.4x as *the*
cost driver, with per-plot-loop growth (10 plots toward 18 as `capacity_1`
gets bought) dismissed as "real but minor by comparison." That dismissal
was wrong, or at least incomplete: `state.py` now tracks an
occupied-slot/free-list per run (`occupied`/`slot_pos`/`free`, see
`_insert_lot`/`_remove_lot`/`_trim_to_capacity`'s docstrings in
`kernel.py`), so aging, both capacity-trim passes, the jobs-complete
insert, the contracts accept/deliver scan, the processing input scan, and
the sell-all-lots loop all now walk *occupied* slots (typically a handful,
matching Phase 3's "inventory rarely survives the day" finding) instead of
`range(75)` -- an O(1) storage-liability check and O(1) lot insertion came
along for free at the same time (former linear scan for "any inventory
exists," former linear scan for "first empty slot"). That's a real,
validated ~20% win (91.5s → 73.3s at the noisy end of each range) -- but
it only closed about a fifth of the gap to the 60s target, which means the
per-plot loop growing toward 18 plots (and the wider arrays' memory-
bandwidth cost, independent of any O(L)-vs-O(occupied) algorithmic
question) is the *larger* remaining driver, not lot-slot scanning. Neither
was touched by this pass -- see Migration notes for what's next.

**Correctness note**: converting to a free-list changes *which physical
slot* a given lot occupies (LIFO free-list order, not "always the
lowest-index empty slot" the original linear scan implicitly gave). That
has no effect on any conserved total (`total_harvest`/`total_spoiled` are
provably order-independent), but it does change which of several
identically-eligible lots (same item, tied quality-gate or tied FEFO
remaining-shelf-life) gets consumed first by contracts/processing/trim --
a real, if economically negligible, per-seed output shift discovered via
`scripts/vectorized_validate.py` immediately catching kernel/reference
disagreement until `reference.py` was updated to the identical scheme (see
its `_insert_lot`/`_remove_lot` docstrings for why `list.pop()` on a
`free = list(range(L))` list exactly reproduces the array version's
pop-from-the-end order). Aggregate/statistical behavior is unaffected;
exact per-seed replay values from before this optimization are not
preserved -- same caveat every phase's numbers have carried since Phase 1.

**Phase 1 cost ~7.5x throughput against the Phase 0 toy kernel; Phase 2 cost
another ~1.16x on top of that; Phase 3 got most of Phase 2's cost back; Phase
4 gave back about a third of Phase 3's gain; Phase 5 cost more than Phase 4
and Phase 2 combined; Phase 7 cost more than every prior phase combined, and
its first optimization pass only recovered a fifth of that** (Phase 0:
~335,000 runs/s at scale, Phase 1: ~44,900, Phase 2: ~38,600, Phase 3:
~73,300, Phase 4: ~48,700, Phase 5: ~22,300, Phase 7 initial: ~10,900,
Phase 7 + free-list: ~13,600). Phase 1's hit was the real price of real
physics; Phase 3's recovery came from lots no longer surviving past the day
they're created, collapsing most of the aging/trim loop's per-slot work to
a cheap `continue` (see "Deviations from the prompt" above). Phase 5's cost
had two real drivers: contracts becoming the dominant economic path, and a
`num_buyers`-length scan running every day regardless of buyer state.
Phase 7's cost was different in kind from every prior phase's -- not new
per-day work proportional to a small config-driven count, but a multiplier
applied to every existing lot-slot-scanning block at once, because the
fixed-shape-array answer to "a run might unlock more plots/job-slots" was
to make the shared array bigger for every run. The free-list removed that
multiplier from the *lot-slot* side of that tradeoff; the *plot-count* side
(P allocated at 18 for every run) is still exactly as it was.

Two shapes worth reading, not just the headline row:

- **Throughput improved at every chunk size, not just at 1M** -- the
  free-list's win isn't a large-N-only effect, since occupied-vs-L was
  already a large gap even at small run counts (occupancy depends on how
  many plots/days have run, not on how many *runs* are in the batch).
- **Peak RSS rose slightly with this change** (~740-780MB → ~840-880MB) --
  the three new per-run scratch arrays (`occupied`/`slot_pos`/`free`, each
  `int32 * num_lot_slots`) add real bytes that `bytes_per_run` doesn't
  account for (they're per-run scratch inside `_simulate_chunk_core`, like
  `market_supply`, not `BatchState` fields -- see Data contract above for
  why that's the right call architecturally, but it does mean this
  specific cost is invisible to `choose_chunk_size`'s budgeting). Still
  comfortably under the 2GB target (~2.3-2.4x margin, down from 2.8x) --
  worth knowing if a future change also grows L, since this margin has
  room but is no longer as flat as Phase 5's.

## Roadmap: closing the gap with the real engine

Phases 1–5 and 7 cover crop/soil physics, storage/spoilage, single-channel
markets, processing, simplified contracts, and upgrades. Remaining
subsystems, roughly in order of vectorization difficulty (see the
difficulty table this was scoped from):

1. ~~Crop/soil physics parity~~ — **done**: multi-stress growth, N/P/K/pH,
   family rotation, quality grading, fertilizer, revenue-gated unlocks,
   config-driven.
2. ~~Storage & spoilage~~ — **done**: fixed-size lot-slot arrays
   (`state.py`'s `(B, L)` dimension), age-tracking + decay + FEFO
   capacity-trim ported from `simulation/inventory.py`, config-driven from
   `config/storage.json`.
3. ~~Markets~~ — **done, single-channel scope**: per-crop supply/demand
   price roll + decay each day, replacing the old harvest-time
   `roll_price`-style draw; every lot standing after aging/spoilage/trim is
   sold in full at that price. This is what resolved Phase 2's shadow
   accounting (harvest stopped crediting `money` directly). NOT ported: the
   real 5-channel system (`simulation/markets.py`'s `spot`/`wholesale`/
   `farm_stand`/`processor`/`specialty`, each with its own price
   multiplier, quality gate, daily capacity, fee structure), reputation, or
   per-strategy `choose_sales` logic — see "Deviations from the prompt"
   above. A future sub-phase could add multi-channel capacity/fees/
   reputation without redoing the daily-price-roll mechanics this phase
   built; it would mainly need per-channel capacity tracking (another
   per-run array, same shape as the lot-slot pattern) and a real per-
   strategy sell decision instead of "sell everything."
4. ~~Processing~~ — **done**: fixed-size job-slot arrays (`state.py`'s
   `(B, J)` dimension, `J = config.base_capacity`), recipes consuming a
   crop + cash to complete into a sellable processed-product lot, config-
   driven from `config/processing.json`. Required unifying crops and
   products into one item space (`config_arrays.py`'s docstring) so
   product lots could reuse the existing lot-slot array rather than
   needing a second one. NOT ported: per-strategy `choose_processing`
   logic (fixed config-order recipe preference instead, same
   simplification style as markets), processing-capacity upgrades.
5. ~~Contracts & buyer relationships~~ — **done, simplified scope**: 7
   buyers (`config/buyers.json`), one contract slot each (`state.py`'s
   `(B, K)` dimension), offer/accept/deliver/deadline-penalty mechanics
   ported; `reputation` and per-buyer `buyer_relationship` are new real
   `BatchState` fields. NOT ported: `is_offer_feasible`'s multi-day
   production-forecast scheduler (greedy batch scheduling, sorted
   future-harvest arrivals, cash reservation across competing recipes) --
   the roadmap's own "least naturally mask-shaped subsystem" flag turned
   out to be avoidable rather than requiring
   `run_isolated_strategy_fallback`, once the accept decision was
   simplified to "any current stock" instead of a forecast (see
   "Deviations from the prompt" above); also not ported: per-strategy
   `choose_contracts` logic (only 2 of the real 11 strategies use it
   anyway) and the unlimited-concurrent-offers-per-buyer real engine
   allows. This phase cost real throughput/memory margin (see
   Performance above) -- a genuine trade for the ~300x money/~5x harvest
   swing contracts turned out to dominate, once ported even in simplified
   form.
6. **Full agent roster** (`agents/*.py`, 11 strategies): port real decision
   logic (e.g. `profit_optimizer`, `progression_player`) instead of the 3
   threshold-mask strategies here — this is also where a real
   `choose_sales` (hold vs. sell, channel selection) and a real
   `choose_contracts`/`is_offer_feasible` forecast scheduler would land,
   since the 3 fixed strategies don't have per-strategy sale/contract
   logic to port yet. Variable difficulty per agent. **Blocked on a
   performance decision, not a design one**: Phase 7 already missed the
   60s/1M-runs target (0.66x, see Performance above); this phase would add
   yet more per-day agent-decision work on top of a kernel that's already
   over budget, so it should not start until a human has decided how to
   respond to Phase 7's miss (see Migration notes below) — starting anyway
   would only make the next measurement worse without addressing why.
7. ~~Upgrades~~ — **done**: all four `config/upgrades.json` effect types
   ported -- `capacity` (+8 plots) and `processing_capacity` (+2 job slots)
   grow `state.py`'s `P`/`J` dimensions, allocated at max width for every
   run with `active_plots`/`active_job_slots` gating how much of that width
   a given run has unlocked; `growth_time_reduction` folds into a per-run
   multiplier applied only at planting time; `storage`'s `capacity_bonus`/
   `shelf_life_multiplier` fold into per-run-per-day effective values used
   everywhere the base `config/storage.json` values were used before. NOT
   ported: the real `should_buy_upgrade_within_budget` gate (cash-buffer
   threshold instead, same simplification style as every other phase's
   agent decisions) -- see "Deviations from the prompt" above. **This
   phase missed the performance target** (Performance above) -- the
   max-width allocation tradeoff that made it a clean single-phase port
   is also its direct cost; see Migration notes for what to do next.

Each phase should get its own `scripts/vectorized_validate.py`-style check
before the next one starts, the same way Phase 1's 144 kernel-vs-reference
comparisons, Phase 2's 27 forced-capacity-trim comparisons, Phase 4's 27
processing-occurs comparisons, Phase 5's 27 contracts-occur comparisons,
and Phase 7's 27 upgrades-purchased comparisons gate them. Phase 3 needed
no new check function: its logic runs on every simulated day, so the
existing checks already exercised it.

## Migration notes: swapping in JAX later

**The trigger fired, and a first fix landed but didn't close it.** Phase
5's Migration notes said upgrades should treat the (then 1.3x) margin as a
hard constraint, and that missing it "is the trigger to revisit `prange`
tuning or finally reach for JAX/GPU, not a later phase." Phase 7 missed
it -- ~84-92s at 1M runs, 0.65-0.71x of the 60s budget. The user chose
option 1 (below) of the three then on offer; an occupied-slot free-list
(see Performance above) closed about a fifth of the gap -- 73-74s, 0.81-
0.82x -- and revealed that option 1's original diagnosis was incomplete:
lot-slot scanning (L growing 2.4x) was real but not the dominant driver
after all. **This is still a decision a human needs to make, not a solved
problem.** Updated options, roughly in order of effort:

1. **Finish option 1: stop allocating *plots* at max width for every
   run**, the half not yet done. The free-list already fixed the
   *lot-slot* side of "allocate for the max, gate with a counter"; `P`
   (plots) is still allocated at 18 for every run regardless of whether
   `capacity_1` is ever bought, and the per-plot loop growing toward that
   width as a run progresses is now the likelier dominant cost (see
   Performance above). A two-tier chunk layout (small-P arrays for runs
   that never reach `capacity_1`, a separate max-width pass for the ones
   that do) is the natural next step -- it's the sub-approach the original
   plan set aside as the higher-risk one (it touches
   `orchestrator.run_millions`'s streaming/`StreamingStats` flow, not just
   `state.py` + `kernel.py`'s lot-slot blocks), but the free-list's
   lower-risk alternative doesn't have an equivalent for *plot count* the
   way it did for *lot slots* (a plot's per-day work -- soil regen, stress
   accumulation, harvest, planting -- can't be skipped via an "occupied"
   index the way an empty lot slot can, since every active plot has real
   work to do every day it's active).
2. **`prange`/kernel micro-tuning** on what's left (the per-plot loop
   itself, or the wider arrays' memory-bandwidth cost independent of
   iteration count) -- lower ceiling than fully finishing option 1, no
   architectural change.
3. **JAX/GPU**, below -- the largest-effort option, and the one Phase 5's
   notes flagged as the last resort, not the first one to reach for.

Whichever is chosen, re-run the isolated-process 1M-run benchmark this
section's numbers use, and update the Performance section above with the
result before Phase 6 (full agent roster, blocked on this -- see Roadmap)
proceeds.

If GPU throughput becomes the actual constraint (option 3):

- Replace `rng.py`'s `splitmix64` with `jax.random.split`/`jax.random.uniform`
  keyed the same way: derive a `PRNGKey` per run from `(master_seed,
  run_index)` via `jax.random.fold_in`, and a per-plot key the same way from
  the run key — same shape of guarantee (chunk-size independence) as
  `splitmix64` gives here, different primitive.
- `kernel.py`'s `_simulate_chunk_core` becomes a `jax.lax.scan` over days
  (not a Python `for day in range(num_days)` — JAX traces once and the loop
  body must be shape-stable) with the plot loop vectorized as real array ops
  (`jnp.where` masks), not the scalar `for p in range(num_plots)` this module
  uses — JAX has no numba-`prange`-style "compile a scalar loop and
  parallelize it" option, so this rewrite would need to actually do the
  mask-vectorized style the original prompt described in component B. Phase
  1's variable per-plot draw counts (1, 2, or 3 draws depending on branch)
  would need to become "always draw the max, mask off the unused ones" to
  stay `jnp.where`-shaped — a real behavior-preserving rewrite, not a
  mechanical port.
  `run_millions` chunk-then-`del`-then-`gc.collect()` structure carries over
  unchanged; `jax.jit(simulate_chunk, static_argnums=...)` replaces `@njit`.
- `state.py`'s numpy arrays become `jnp.ndarray`; `float32` stays the state
  dtype (JAX defaults to float32 unless `jax_enable_x64` is set, which
  matches this module's existing choice for free).
- `stats.py`'s `StreamingStats.update` already takes a batch and does the
  reduction with plain array ops (`batch.mean()`, `batch.var()`) — swapping
  `np.asarray` for `jax.numpy.asarray` (or just calling `np.asarray(jax_array)`
  to pull results back to host before aggregating) is the only change needed
  there.
