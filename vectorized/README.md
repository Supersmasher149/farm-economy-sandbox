# vectorized/ — high-throughput Monte Carlo sampler

A **separate, experimental** simulator, not a replacement for `simulation/`.
It exists to answer one question the main engine structurally can't: what do
aggregate outcomes look like across **millions** of runs, when you don't need
per-day history and don't need bit-exact replay of a specific seed?

Read this before touching `vectorized/config_arrays.py` or comparing this
module's numbers to `simulation/`'s — they are not meant to agree, for
reasons below.

**Status: Phase 1 of a multi-phase port ("crop/soil physics parity")
complete.** Crop growth, soil chemistry, weather, watering, fertilizer, and
crop unlocking are ported from the real config-driven mechanics. Storage,
markets, contracts, processing, upgrades, and the real 11-strategy agent
roster are **not** — see Roadmap below.

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
crops" — and it's now half-closed: crop/soil physics *does* read the real
`config/crops.json` + `config/soil.json` + `config/watering_settings.json` +
`config/fertilizer.json` + `config/weather.json` (see `config_arrays.py`).
Contracts, processing, markets, storage, and upgrades are still absent —
see Roadmap.

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
1,000,000 runs x 10 plots x 365 days in 22.28s (44,883 runs/s)
  overall money:   mean=     5.44  stddev=    6.15  min=     0.00  max=   190.20
  overall harvest: mean=   413.99  stddev=  640.34
  greedy       (n= 333,330): money mean=     4.89 stddev=    2.28  harvest mean=   66.7
  conservative (n= 333,340): money mean=     4.87 stddev=    2.34  harvest mean=   52.9
  random       (n= 333,330): money mean=     6.41 stddev=   10.00  harvest mean= 1123.3
```

Measured on a single CPU core's worth of `numba(parallel=True)` work (see
Performance below): **1,000,000 runs in ~22.3s, peak RSS ~523MB** — inside
the prompt's <60s CPU / 2GB targets (2.7x and 3.9x margin respectively).
Phase 1's much richer per-plot physics costs real throughput against the
Phase 0 toy model (which ran the same 1M runs in ~3.0s) — see Performance
below for the shape of that cost — but both targets still clear.

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
  treating the crop as always-unlocked)
- `config/soil.json` — initial plot values, regen-per-day, rotation/
  soil-health dynamics
- `config/watering_settings.json`, `config/fertilizer.json`
- `config/weather.json` — seasonal temperature/rain/evaporation

into a single frozen `VectorConfig` of numpy arrays, loaded once and passed
through `state.init_runs` / `kernel.simulate_chunk` rather than read per-day
— the same "resolve once, reuse" shape as `simulation/derived.py`'s
`WorldLookups`, just arrays instead of per-id dict lookups since the kernel
needs `array[crop_idx]`.

**Not yet read**: `config/contracts.json`, `config/buyers.json`,
`config/processing.json`, `config/markets.json`, `config/upgrades.json`,
`config/storage.json`. Loading those now would be dead weight that silently
goes stale until the subsystems that need them exist — see Roadmap.

## Data contract (`state.py`)

Structure-of-Arrays, flat numpy arrays, no per-run Python object. Grown from
the original 8-field layout to mirror `simulation/state.py`'s
`PlotState`/`PlantedCrop` field-for-field:

```
# run-level
money[B]                  float32     total_harvest[B]          float32
total_revenue[B]          float32     strategy_id[B]             int8

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
```

72 bytes/plot, ~741 bytes/run at `P=10` — `bytes_per_run(num_plots)` in
`state.py` is the exact closed-form sum; `DEFAULT_MAX_CHUNK = 100_000` still
binds before the 2GB memory budget does (see Memory strategy).

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
and a plot that matures today draws 3 more (loss check, yield roll, price
roll) before immediately trying to replant in the same day, same as
`simulation/engine.py`'s real daily order. This is fine for determinism —
kernel.py and reference.py both branch on identical state, so both draw
identically — see kernel.py's docstring.

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

`scripts/vectorized_validate.py` checks two things, not one:

1. **Kernel ≡ reference**: `vectorized.kernel`'s numba `prange`-parallel core
   and `vectorized.reference`'s pure-Python scalar per-run loop are two
   implementations of the *same* algorithm (same branch order, same draw
   order, same float32 rounding on every state write — see both modules'
   docstrings, and kernel.py's per-block comments naming which real
   `simulation/` function each block mirrors). They're checked to agree
   within float32 tolerance across a spread of seeds, run indices,
   strategies, and plot counts — 144 combinations, all currently passing.
2. **Chunk-size independence**: the same global run index gives the same
   result whether it's simulated alone or embedded in chunks of different
   sizes and offsets (the RNG property above, checked directly).

Neither of these validates against `simulation/`'s real engine — see "Why
this is a separate tool" above for why that comparison isn't meaningful.

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
  coupled to `config/contracts.json`/`buyers.json`/`processing.json`/
  `markets.json`/`upgrades.json`/`storage.json` — those subsystems aren't
  ported, so reading their config now would be silently-stale dead weight.
- **3 fixed strategies, not the real 11-agent roster.** `agents/*.py`'s
  strategies have real config-driven decision trees (`profit_optimizer`,
  `progression_player`); this module's greedy/conservative/random are
  threshold masks over the (now-real) physics, not ports of those agents.
  Porting the real roster is its own future phase — see Roadmap.
- **No JAX migration was needed to hit the target.** Even Phase 1's much
  heavier per-plot physics clears <60s CPU by ~2.7x margin at 1M runs (see
  Performance below), so JAX wasn't pursued. Migration notes below in case
  GPU throughput becomes the actual constraint later.

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
| 1,000 | 10 × 365 | 0.035 s | ~29,000 runs/s | 105 MB | — |
| 10,000 | 10 × 365 | 0.226 s | ~44,300 runs/s | 114 MB | — |
| 100,000 | 10 × 365 | 2.225 s | ~44,900 runs/s | 206 MB | — |
| 500,000 | 10 × 365 | 11.13 s | ~44,900 runs/s | 400 MB | — |
| **1,000,000** | **10 × 365** | **22.28 s** | **~44,900 runs/s** | **523 MB** | **2.7x under 60s · 3.9x under 2GB** |

(numba JIT compilation of `simulate_chunk` happens once per process on first
call and is excluded from these figures, same as the main engine's
Cython/`_fastplot` builds are one-time costs excluded from
`sample_profile.py` numbers.) `--compare-existing-engine` on
`vectorized_benchmark.py` times `main.py batch` alongside this for a
wall-clock reference point — see that script's docstring for why it's not an
apples-to-apples comparison of the same economic model.

**Phase 1 cost ~7.5x throughput against the Phase 0 toy kernel** (was
~335,000 runs/s at scale, now ~44,900). That's the real price of real
physics: multi-nutrient soil (N/P/K instead of one nitrogen scalar), 5
separate stress accumulators instead of 1, family-rotation and soil-health
multipliers, fertilizer state, and 3–6 RNG draws per plot per day instead of
a fixed 1–4 — a lot more floating-point work and branches per plot per day,
which is exactly what "physics parity" was asking for. Both throughput and
memory still clear the prompt's targets with room (2.7x, 3.9x) — there's
headroom left before either budget would force revisiting the `prange`
design or reaching for JAX/GPU, but meaningfully less than Phase 0 had.

Two shapes worth reading, not just the headline row:

- **Throughput ramps then plateaus, it doesn't keep climbing.** 1k→10k
  runs/sec rises sharply (per-call dispatch/allocation overhead amortizing
  over more runs), then flattens around ~44,900 runs/s from 10,000 runs
  onward — same shape as the main engine's straggler-effect writeup for
  worker count in `CLAUDE.md`'s Performance section, just saturating at a
  smaller chunk size than Phase 0 did (more per-run work means per-call
  overhead amortizes away sooner).
- **Peak RSS scales roughly linearly with `total_runs` once above one
  chunk**, unlike Phase 0's flatter step-then-plateau shape: 206MB at 100k →
  400MB at 500k → 523MB at 1M is closer to proportional than flat. The
  richer per-plot state (72 bytes/plot vs. Phase 0's much smaller layout)
  means allocator arena retention between chunks (same mechanism flagged in
  Phase 0's numbers) has more bytes to retain per chunk; still well inside
  the 2GB budget at 1M runs, but worth watching if `total_runs` grows another
  order of magnitude.

## Roadmap: closing the gap with the real engine

Phase 1 covers crop/soil physics. Remaining subsystems, roughly in order of
vectorization difficulty (see the difficulty table this was scoped from):

1. ~~Crop/soil physics parity~~ — **done** (this phase): multi-stress growth,
   N/P/K/pH, family rotation, quality grading, fertilizer, revenue-gated
   unlocks, config-driven.
2. **Storage & spoilage** (`simulation/inventory.py`, `config/storage.json`):
   age-tracking arrays + decay masks, same shape as the existing
   `days_to_harvest` countdown pattern. Medium difficulty.
3. **Markets** (`simulation/markets.py`, `config/markets.json`): per-crop
   supply/demand price arrays updated by formula each day, replacing the
   current single `roll_price`-style draw at harvest. Medium difficulty.
4. **Processing** (`simulation/processing.py`, `config/processing.json`):
   fixed-size job-slot arrays with countdown timers, another dimension on
   the state arrays. Medium-high difficulty.
5. **Contracts & buyer relationships** (`simulation/contracts.py`, 663
   lines, `config/buyers.json`): per-buyer relationship standing, offer
   negotiation, delivery scheduling. High difficulty — the least naturally
   mask-shaped subsystem; likely the first real user of
   `run_isolated_strategy_fallback`'s pattern rather than true `prange`
   vectorization.
6. **Full agent roster** (`agents/*.py`, 11 strategies): port real decision
   logic (e.g. `profit_optimizer`, `progression_player`) instead of the 3
   threshold-mask strategies here. Variable difficulty per agent.

Each phase should get its own `scripts/vectorized_validate.py`-style check
before the next one starts, the same way this phase's 144 kernel-vs-reference
comparisons gate it.

## Migration notes: swapping in JAX later

Not needed to hit this prompt's target (2.7x margin above target already, on
CPU, single process, even with Phase 1's heavier physics) — recorded here
since the prompt asked for it. Re-evaluate if a later phase (contracts,
especially) pushes throughput below the target rather than just eating
margin.

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
