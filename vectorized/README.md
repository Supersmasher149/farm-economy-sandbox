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
("processing"), and Phase 5 ("contracts", simplified scope) complete.**
Crop growth, soil chemistry, weather, watering, fertilizer, and crop
unlocking are ported from the real config-driven mechanics. Storage/
spoilage: lots age, downgrade quality, fully spoil, and get
capacity-trimmed exactly like `simulation/inventory.py`. Markets: every
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
themselves. Upgrades and the real 11-strategy agent roster are **not**
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
`config/storage.json`, markets reads `config/markets.json`, processing reads
`config/processing.json`, and contracts read `config/buyers.json` +
`config/contracts.json` (see `config_arrays.py`). Only upgrades are still
absent — see Roadmap.

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
1,000,000 runs x 10 plots x 365 days in 44.82s (22,313 runs/s)
  overall money:   mean=  2259.72  stddev= 3231.43  min=     0.00  max= 15519.94
  overall harvest: mean=  1196.69  stddev=  674.61
  overall spoiled: mean=     0.00  stddev=    0.00  (storage cost mean=  0.00, shadow accounting -- not deducted from money)
  overall processed: mean=  45.22  stddev=   33.60
  overall contracts: completed mean=63.06  failed mean= 5.87  penalties mean= 81.36
  greedy       (n= 333,330): money mean=    15.17 stddev=   37.66  harvest mean=  454.07
  conservative (n= 333,340): money mean=   445.44 stddev=  338.87  harvest mean= 1739.41
  random       (n= 333,330): money mean=  6318.61 stddev= 2531.04  harvest mean= 1396.58
```

`overall_spoiled` reading ~0 here is still expected, not a bug -- same
reason as Phase 3/4: inventory rarely survives past the day it's created.
The big change from Phase 4's numbers is `overall money`/`overall harvest`
jumping roughly 300x and 5x: contracts get first claim on inventory (ahead
of processing and market selling, see kernel.py's day order) and pay far
better than the market ever does -- buyer `contract_price_multiplier`s run
1.3x-4.0x base price, vs. the market's quality-scaled 0.65x-1.35x, and a
relationship bonus stacks on top the more a buyer's contracts get
fulfilled. That extra cash funds far more replanting, which is why harvest
volume rose too, not just money. `overall_contracts` shows both outcomes
this phase's simplified accept policy produces: most offers do complete
(mean 63 vs. mean 5.87 failed), but "accept on any current stock" instead
of the real engine's forecast-gated accept genuinely does produce some
un-deliverable contracts and real cash penalties -- see "Deviations from
the prompt" below.

Measured on a single CPU core's worth of `numba(parallel=True)` work (see
Performance below): **1,000,000 runs in ~44.8s, peak RSS ~740MB** — inside
the prompt's <60s CPU / 2GB targets, but only just: **1.3x and 2.8x margin
respectively** -- by far the thinnest margin of any phase so far, see
Performance below.

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

**Not yet read**: `config/upgrades.json` (including upgrades' storage
`capacity_bonus`/`shelf_life_multiplier` effects and processing-capacity
bonuses — Phase 2-4 use only the base config values). Loading it now
would be dead weight that silently goes stale until that subsystem
exists — see Roadmap.

## Data contract (`state.py`)

Structure-of-Arrays, flat numpy arrays, no per-run Python object. Grown from
the original 8-field layout to mirror `simulation/state.py`'s
`PlotState`/`PlantedCrop` field-for-field:

```
# run-level
money[B]                  float32     total_harvest[B]          float32
total_revenue[B]          float32     strategy_id[B]             int8
total_spoiled[B]           float32    total_storage_cost[B]      float32
total_processed[B]         float32    reputation[B]              float32
total_contracts_completed[B]  float32 total_contracts_failed[B]  float32
total_contract_penalties[B]   float32

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

# buyer-level (Phase 5): shape (B, K), K = config.num_buyers -- exactly one
# contract "slot" per buyer (not a derived bound -- see state.py's docstring)
contract_state[B,K]            int8    contract_item_idx[B,K]      int8
contract_remaining[B,K]        int32   contract_unit_price[B,K]    float32
contract_min_quality_rank[B,K] int8    contract_deadline_day[B,K]  int32
contract_expiry_day[B,K]       int32   contract_penalty_rate[B,K]  float32
buyer_relationship[B,K]        float32
```

74 bytes/plot + 8 bytes/lot-slot + 9 bytes/job-slot + 20 bytes/buyer,
1,235 bytes/run at `P=10` (31 lot slots, 1 job slot, 7 buyers,
`lots_per_plot=3`, `base_capacity=1`) —
`bytes_per_run(num_plots, num_lot_slots, num_job_slots, num_buyers)` in
`state.py` is the exact closed-form sum; `DEFAULT_MAX_CHUNK = 100_000`
still binds before the 2GB memory budget does (see Memory strategy).

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

`scripts/vectorized_validate.py` checks five things, not one:

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

228 checks currently pass. None of these validate against `simulation/`'s
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
  coupled to `config/upgrades.json` — that subsystem isn't ported, so
  reading its config now would be silently-stale dead weight.
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
- **3 fixed strategies, not the real 11-agent roster.** `agents/*.py`'s
  strategies have real config-driven decision trees (`profit_optimizer`,
  `progression_player`); this module's greedy/conservative/random are
  threshold masks over the (now-real) physics, not ports of those agents.
  Porting the real roster is its own future phase — see Roadmap.
- **No JAX migration was needed to hit the target, but the margin is now
  thin.** This clears <60s CPU by ~1.3x margin at 1M runs with all five
  phases' physics/storage/markets/processing/contracts in place (see
  Performance below) -- down from Phase 4's already-flagged 2.9x -- so JAX
  still wasn't pursued, but this is the first phase where "re-evaluate
  before the next one" (Migration notes below) is a real, not
  theoretical, concern.

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
| 1,000 | 10 × 365 | 0.16 s | ~6,100 runs/s | 107 MB | — |
| 10,000 | 10 × 365 | 0.54 s | ~18,700 runs/s | 122 MB | — |
| 100,000 | 10 × 365 | 4.63 s | ~21,600 runs/s | 252 MB | — |
| 500,000 | 10 × 365 | 22.82 s | ~21,900 runs/s | 489 MB | — |
| **1,000,000** | **10 × 365** | **44.82 s** | **~22,300 runs/s** | **740 MB** | **1.3x under 60s · 2.8x under 2GB** |

(Phase 4's numbers, for comparison: 20.55s, ~48,700 runs/s, 357MB at 1M —
Phase 5 costs more than double the wall time. This is the tightest margin
of any phase so far; see below for exactly where the cost goes and why.)

(numba JIT compilation of `simulate_chunk` happens once per process on first
call and is excluded from these figures, same as the main engine's
Cython/`_fastplot` builds are one-time costs excluded from
`sample_profile.py` numbers.) `--compare-existing-engine` on
`vectorized_benchmark.py` times `main.py batch` alongside this for a
wall-clock reference point — see that script's docstring for why it's not an
apples-to-apples comparison of the same economic model.

**Phase 1 cost ~7.5x throughput against the Phase 0 toy kernel; Phase 2 cost
another ~1.16x on top of that; Phase 3 got most of Phase 2's cost back; Phase
4 gave back about a third of Phase 3's gain; Phase 5 cost more than Phase 4
and Phase 2 combined** (Phase 0: ~335,000 runs/s at scale, Phase 1: ~44,900,
Phase 2: ~38,600, Phase 3: ~73,300, Phase 4: ~48,700, Phase 5: ~22,300).
Phase 1's hit was the real price of real physics; Phase 3's recovery came
from lots no longer surviving past the day they're created, collapsing most
of the aging/trim loop's per-slot work to a cheap `continue` (see
"Deviations from the prompt" above). Phase 5's cost has two real drivers,
neither of them a rounding error: (1) contracts are now the *dominant*
economic path (see the Usage section's ~300x money jump), so the "sell all
matured lots"/processing loops that used to often iterate over a handful of
occupied slots now regularly iterate over the same slots *again* inside the
contracts accept/deliver block first -- more slots are occupied for more of
each day's work, not just one more block bolted on; (2) the offer-generation
block runs a `num_buyers`-length loop with 2 RNG draws each, every
`offer_interval_days`, and the accept/deliver/resolve block runs a
`num_buyers`-length loop with an inner `num_lot_slots` scan *every single
day* regardless of whether any buyer has an open slot. Both throughput and
memory still clear the prompt's targets, but only just (1.3x, 2.8x) --
this is the first phase where the margin itself, not just its shrinking
trend, is worth treating as a real constraint on what Phase 6 (upgrades) or
a later multi-channel-markets sub-phase can still afford to add. See
Migration notes below.

Two shapes worth reading, not just the headline row:

- **Throughput no longer plateaus by 100,000 runs the way every earlier
  phase did -- it's still gently rising through 500k** (18,700 → 21,600 →
  21,900 runs/s from 10k → 100k → 500k, vs. Phase 4's clean flatten by
  100k). The per-call dispatch overhead this shape usually amortizes away
  is now a smaller fraction of a much heavier per-run cost, so it takes
  more runs per chunk to fully amortize -- not a new mechanism, just a
  side effect of the per-run cost itself growing.
- **Peak RSS grew roughly 2x over Phase 4's at every size** (357MB →
  740MB at 1M) -- far more than `bytes_per_run` alone explains (~1,030 to
  ~1,235, only +20%), so most of this growth is the same allocator-arena
  variability flagged in Phase 2/4's numbers, not a new leak, just a
  larger-magnitude instance of it at this run count. Still comfortably
  under the 2GB budget, but 2.8x is the thinnest memory margin of any
  phase measured so far (below even Phase 1's 3.9x).

## Roadmap: closing the gap with the real engine

Phases 1–5 cover crop/soil physics, storage/spoilage, single-channel
markets, processing, and simplified contracts. Remaining subsystems,
roughly in order of vectorization difficulty (see the difficulty table
this was scoped from):

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
   logic to port yet. Variable difficulty per agent.
7. **Upgrades** (`config/upgrades.json`): including storage's own
   `capacity_bonus`/`shelf_life_multiplier` effects (`derived.py`'s
   `effective_storage`) and processing-capacity bonuses, not folded in
   during Phase 2/4 since no upgrades exist yet to apply them. Given
   Phase 5's already-thin margin (Performance above), this phase should
   budget for a performance pass, not just a feature port.

Each phase should get its own `scripts/vectorized_validate.py`-style check
before the next one starts, the same way Phase 1's 144 kernel-vs-reference
comparisons, Phase 2's 27 forced-capacity-trim comparisons, Phase 4's 27
processing-occurs comparisons, and Phase 5's 27 contracts-occur comparisons
gate them. Phase 3 needed no new check function: its logic runs on every
simulated day, so the existing checks already exercised it.

## Migration notes: swapping in JAX later

Still not *needed* -- 1.3x margin above target, on CPU, single process,
with Phase 1-5's physics/storage/markets/processing/contracts all in place
— but this is the phase that turns "worth watching" into "budget for it
explicitly." Contracts alone cost more than Phase 2 and Phase 4 combined
(Performance above). Upgrades (Roadmap item 7, next) should treat this
margin as a hard constraint: if it can't land without pushing throughput
below the 60s/1M target, that's the trigger to revisit `prange` tuning or
finally reach for JAX/GPU, not a later phase.

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
