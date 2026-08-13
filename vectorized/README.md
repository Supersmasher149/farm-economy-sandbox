# vectorized/ — high-throughput Monte Carlo sampler

A **separate, experimental** simulator, not a replacement for `simulation/`.
It exists to answer one question the main engine structurally can't: what do
aggregate outcomes look like across **millions** of runs, when you don't need
per-day history and don't need bit-exact replay of a specific seed?

Read this before touching `vectorized/config_arrays.py` or comparing this
module's numbers to `simulation/`'s — they are not meant to agree, for
reasons below.

**Status: Phase 1 ("crop/soil physics parity"), Phase 2 ("storage &
spoilage"), Phase 3 ("markets", single-channel scope), and Phase 4
("processing") complete.** Crop growth, soil chemistry, weather, watering,
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
Contracts, upgrades, and the real 11-strategy agent roster are **not**
ported — see Roadmap below.

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
crops" — and it's now further closed: crop/soil physics *does* read the real
`config/crops.json` + `config/soil.json` + `config/watering_settings.json` +
`config/fertilizer.json` + `config/weather.json`, storage/spoilage reads
`config/storage.json`, markets reads `config/markets.json`, and processing
reads `config/processing.json` (see `config_arrays.py`). Contracts and
upgrades are still absent — see Roadmap.

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
1,000,000 runs x 10 plots x 365 days in 20.55s (48,658 runs/s)
  overall money:   mean=     7.43  stddev=   14.93  min=     0.00  max=  1046.51
  overall harvest: mean=   253.85  stddev=  347.40
  overall spoiled: mean=     0.00  stddev=    0.00  (storage cost mean=  0.00, shadow accounting -- not deducted from money)
  overall processed: mean=   23.23  stddev=   31.28
  greedy       (n= 333,330): money mean=     5.01 stddev=    3.12  harvest mean=  110.64
  conservative (n= 333,340): money mean=     5.96 stddev=    9.58  harvest mean=  103.87
  random       (n= 333,330): money mean=    11.32 stddev=   23.33  harvest mean=  547.05
```

`overall_spoiled` reading ~0 here is expected, not a bug: since every lot
still standing gets sold in full at the end of each day (Phase 3), almost
nothing survives long enough to age out or need capacity-trimming under the
default `storage_capacity` -- see "Deviations from the prompt" below for
when it still matters. `overall_storage_cost` still reads `0` too: the
liability *charge* itself is still shadow accounting (never subtracted from
`money`), independent of whether harvest revenue is. `overall_processed`
being nonzero (Phase 4) confirms processing routinely fires under the
default config, not just in the forced-nothing-required sense
`check_processing_occurs` checks -- see Validation below.

Measured on a single CPU core's worth of `numba(parallel=True)` work (see
Performance below): **1,000,000 runs in ~20.6s, peak RSS ~357MB** — inside
the prompt's <60s CPU / 2GB targets (2.9x and 5.7x margin respectively).
Phase 4 costs real throughput back (Phase 3: ~73,300 runs/s, Phase 4:
~48,700) -- the daily recipe-start loop and the extra same-day re-trim pass
are real per-day work, not free -- but memory actually drops (see
Performance below for why).

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

**Not yet read**: `config/contracts.json`, `config/buyers.json`,
`config/upgrades.json` (including upgrades' storage
`capacity_bonus`/`shelf_life_multiplier` effects and processing-capacity
bonuses — Phase 2-4 use only the base config values). Loading those now
would be dead weight that silently goes stale until the subsystems that
need them exist — see Roadmap.

## Data contract (`state.py`)

Structure-of-Arrays, flat numpy arrays, no per-run Python object. Grown from
the original 8-field layout to mirror `simulation/state.py`'s
`PlotState`/`PlantedCrop` field-for-field:

```
# run-level
money[B]                  float32     total_harvest[B]          float32
total_revenue[B]          float32     strategy_id[B]             int8
total_spoiled[B]           float32    total_storage_cost[B]      float32
total_processed[B]         float32

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

# lot-level (Phase 2): shape (B, L), L = P * lots_per_plot + base_capacity (Phase
# 4 reserves the +base_capacity for processed-product lots) -- fixed-size mirror
# of simulation/state.py's InventoryLot list, see config_arrays.py's
# lots_per_plot docstring comment for why that bound is provably safe
lot_item_id[B,L]               int8    lot_quantity[B,L]           int32
lot_quality[B,L]                int8   lot_age_days[B,L]           int16

# job-level (Phase 4): shape (B, J), J = config.base_capacity -- fixed-size
# mirror of simulation/state.py's ProcessingJob list, global (not per-plot)
# since processing capacity is a global resource
job_output_item_id[B,J]        int8    job_output_quantity[B,J]    int32
job_completion_day[B,J]        int32
```

74 bytes/plot + 8 bytes/lot-slot + 9 bytes/job-slot, 1,030 bytes/run at
`P=10` (31 lot slots -- 30 plot-driven + 1 job-completion reserve -- and 1
job slot, `lots_per_plot=3`, `base_capacity=1`) —
`bytes_per_run(num_plots, num_lot_slots, num_job_slots)` in `state.py` is
the exact closed-form sum; `DEFAULT_MAX_CHUNK = 100_000` still binds before
the 2GB memory budget does (see Memory strategy).

## RNG strategy

`rng.py`'s docstring has the full rationale; short version: every
`(run_index, plot_index)` pair gets its own `splitmix64` stream, seeded
deterministically from `(master_seed, run_index)` and then `(run_seed,
plot_index)`. Because each run's stream depends on nothing but its own global
index, **results are provably independent of chunk size and chunk offset** —
`scripts/vectorized_validate.py`'s `check_chunk_size_independence` runs the
same global run index inside three different chunk shapes and checks they
agree, and `run_millions` relies on exactly this property to stream chunks
without changing what a given run's outcome is.

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

`scripts/vectorized_validate.py` checks four things, not one:

1. **Kernel ≡ reference**: `vectorized.kernel`'s numba `prange`-parallel core
   and `vectorized.reference`'s pure-Python scalar per-run loop are two
   implementations of the *same* algorithm (same branch order, same draw
   order, same float32 rounding on every state write — see both modules'
   docstrings, and kernel.py's per-block comments naming which real
   `simulation/` function each block mirrors). They're checked to agree
   within float32 tolerance across a spread of seeds, run indices,
   strategies, and plot counts — 144 combinations, all currently passing,
   now also comparing `total_spoiled`/`total_storage_cost`/
   `total_processed` alongside `money`/`total_harvest`/`total_revenue`.
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

201 checks currently pass. None of these validate against `simulation/`'s
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
  was the explicit goal — see `config_arrays.py`'s docstring. Still not
  coupled to `config/contracts.json`/`buyers.json`/`upgrades.json` — those
  subsystems aren't ported, so reading their config now would be
  silently-stale dead weight.
- **Markets are single-channel, not the real 5-channel system.** No
  `spot`/`wholesale`/`farm_stand`/`processor`/`specialty` price
  multipliers, quality gates, daily capacities, or fees; no reputation
  (doesn't exist anywhere in `vectorized/` state). One effective channel:
  a daily per-item (crop or processed product) supply/demand price, sold
  in full every day. Also no per-strategy `choose_sales` logic — every lot
  still standing after a day's aging/spoilage/trim is sold in full, for
  all 3 fixed strategies, the simplest real strategy's behavior
  (`fast_seller.choose_sales` dumps all inventory the same way) — see
  `kernel.py`'s module docstring for the two behavioral consequences worth
  knowing (storage/spoilage gets rarer but stays real; a plot's same-day
  spend decisions still use yesterday's cash, not today's just-credited
  sale revenue).
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
- **3 fixed strategies, not the real 11-agent roster.** `agents/*.py`'s
  strategies have real config-driven decision trees (`profit_optimizer`,
  `progression_player`); this module's greedy/conservative/random are
  threshold masks over the (now-real) physics, not ports of those agents.
  Porting the real roster is its own future phase — see Roadmap.
- **No JAX migration was needed to hit the target.** This clears <60s CPU
  by ~2.9x margin at 1M runs with all four phases' physics/storage/
  markets/processing in place (see Performance below), so JAX wasn't
  pursued. Migration notes below in case GPU throughput becomes the actual
  constraint later.

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

Measured on this machine, `.venv` (Python 3.12.9, numpy 2.5.2, numba 0.67.0),
`P=10` plots, `num_days=365`, one `run_millions` call per process (via
`/usr/bin/time -l`, so each row's peak RSS is isolated rather than a running
high-water mark across multiple sizes in one process):

| Runs | Plots × Days | Wall time | Throughput | Peak RSS | vs. targets |
|---:|---:|---:|---:|---:|---|
| 1,000 | 10 × 365 | 0.14 s | ~6,900 runs/s | 105 MB | — |
| 10,000 | 10 × 365 | 0.30 s | ~33,200 runs/s | 119 MB | — |
| 100,000 | 10 × 365 | 2.15 s | ~46,500 runs/s | 231 MB | — |
| 500,000 | 10 × 365 | 10.27 s | ~48,700 runs/s | 352 MB | — |
| **1,000,000** | **10 × 365** | **20.55 s** | **~48,700 runs/s** | **357 MB** | **2.9x under 60s · 5.7x under 2GB** |

(Phase 3's numbers, for comparison: 13.64s, ~73,300 runs/s, 462MB at 1M —
Phase 4 costs real throughput back, but memory actually drops: see below
for why.)

(numba JIT compilation of `simulate_chunk` happens once per process on first
call and is excluded from these figures, same as the main engine's
Cython/`_fastplot` builds are one-time costs excluded from
`sample_profile.py` numbers.) `--compare-existing-engine` on
`vectorized_benchmark.py` times `main.py batch` alongside this for a
wall-clock reference point — see that script's docstring for why it's not an
apples-to-apples comparison of the same economic model.

**Phase 1 cost ~7.5x throughput against the Phase 0 toy kernel; Phase 2 cost
another ~1.16x on top of that; Phase 3 got most of Phase 2's cost back; Phase
4 gave back about a third of Phase 3's gain** (Phase 0: ~335,000 runs/s at
scale, Phase 1: ~44,900, Phase 2: ~38,600, Phase 3: ~73,300, Phase 4:
~48,700). Phase 1's hit was the real price of real physics; Phase 3's
recovery came from lots no longer surviving past the day they're created,
collapsing most of the aging/trim loop's per-slot work to a cheap
`continue` (see "Deviations from the prompt" above). Phase 4's added cost
is the new
run-level, once-per-day work: the recipe-start loop (small, `num_recipes`
iterations, but each scans lot slots twice -- once to check availability,
once to consume) and the extra same-day `_trim_to_capacity` pass after jobs
complete (cheap when nothing needs trimming, which is most days, but still
an `O(num_lot_slots)` scan every day regardless). Both throughput and
memory still clear the prompt's targets with real room (2.9x, 5.7x) —
memory's margin actually *grew* despite `total_processed` and the job-slot
arrays adding bytes/run, which the RSS shape below explains.

Two shapes worth reading, not just the headline row:

- **Throughput ramps then plateaus, it doesn't keep climbing.** 1k→10k→100k
  runs/sec rises sharply (per-call dispatch/allocation overhead amortizing
  over more runs), then flattens around ~46,500-48,700 runs/s from 100,000
  runs onward — same shape as the main engine's straggler-effect writeup for
  worker count in `CLAUDE.md`'s Performance section.
- **Peak RSS dropped from Phase 3's numbers at every size, not just 1M** —
  105→105MB, 115→119MB, 230→231MB roughly flat at the small end, but
  452→352MB and 462→357MB at 500k/1M is a real difference, not noise at
  that scale. Bytes/run went *up* (1,030 vs. Phase 3's ~1,009), so this
  isn't a smaller-footprint explanation — it's allocator arena behavior
  varying run to run (flagged as a real characteristic of this measurement,
  not a bug, back in Phase 2's numbers too): treat any single Performance
  table as one sample of a noisy quantity, not an exact figure, and don't
  read cross-phase RSS deltas as caused by a specific code change unless
  the bytes/run math actually explains the direction.

## Roadmap: closing the gap with the real engine

Phases 1–4 cover crop/soil physics, storage/spoilage, single-channel
markets, and processing. Remaining subsystems, roughly in order of
vectorization difficulty (see the difficulty table this was scoped from):

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
5. **Contracts & buyer relationships** (`simulation/contracts.py`, 663
   lines, `config/buyers.json`): per-buyer relationship standing, offer
   negotiation, delivery scheduling. High difficulty — the least naturally
   mask-shaped subsystem; likely the first real user of
   `run_isolated_strategy_fallback`'s pattern rather than true `prange`
   vectorization.
6. **Full agent roster** (`agents/*.py`, 11 strategies): port real decision
   logic (e.g. `profit_optimizer`, `progression_player`) instead of the 3
   threshold-mask strategies here — this is also where a real
   `choose_sales` (hold vs. sell, channel selection) would land, since the
   3 fixed strategies don't have per-strategy sale logic to port yet.
   Variable difficulty per agent.
7. **Upgrades** (`config/upgrades.json`): including storage's own
   `capacity_bonus`/`shelf_life_multiplier` effects (`derived.py`'s
   `effective_storage`), not folded in during Phase 2 since no upgrades
   exist yet to apply them.

Each phase should get its own `scripts/vectorized_validate.py`-style check
before the next one starts, the same way Phase 1's 144 kernel-vs-reference
comparisons, Phase 2's 27 forced-capacity-trim comparisons, and Phase 4's
27 processing-occurs comparisons gate them. Phase 3 needed no new check
function: its logic runs on every simulated day, so the existing checks
already exercised it.

## Migration notes: swapping in JAX later

Not needed to hit this prompt's target (2.9x margin above target already, on
CPU, single process, with Phase 1-4's physics/storage/markets/processing all
in place) — recorded here since the prompt asked for it. Re-evaluate if a
later phase (contracts, especially) pushes throughput below the target
rather than just eating margin -- 2.9x is the thinnest margin since Phase 0,
worth watching.

If GPU throughput becomes the actual constraint:

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
