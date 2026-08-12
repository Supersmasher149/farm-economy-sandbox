# vectorized/ — high-throughput Monte Carlo sampler

A **separate, experimental** simulator, not a replacement for `simulation/`.
It exists to answer one question the main engine structurally can't: what do
aggregate outcomes look like across **millions** of runs, when you don't need
per-day history and don't need bit-exact replay of a specific seed?

Read this before touching `vectorized/crops.py` or comparing this module's
numbers to `simulation/`'s — they are not meant to agree, for reasons below.

## Why this is a separate tool, not a faster main engine

Three of the main engine's invariants (`CLAUDE.md`) are incompatible with
"vectorize across a million runs at once," by construction, not by omission:

| Main engine (`simulation/`) | This module (`vectorized/`) |
|---|---|
| `random.Random(seed)`, one global stream, every draw serialized through it | `splitmix64`, one independent stream per `(run, plot)` — see `rng.py` |
| Full config-driven economy: contracts, processing, markets, buyer relationships, upgrades, soil chemistry | 3 illustrative crops, no contracts/processing/markets — see `crops.py` |
| Bit-exact replay of recorded seeds is load-bearing (`replay-guard` skill, golden baseline) | No bit-exactness claim of any kind against `simulation/` |
| Daily history retained, agents are Python objects | Only final `money`/`total_harvest` per run; no per-run object at all |

A `random.Random` stream can't be split across 100,000 parallel runs without
serializing them right back together, so the two determinism models are
fundamentally different, not two implementations of the same one. Don't
expect (or try to make) this module reproduce a `simulation/` seed's output.

If you need bit-exact, config-driven, full-economy runs: use `main.py batch`.
If you need aggregate statistics — mean/variance/distribution of outcomes —
across millions of trials of a simplified model, in seconds instead of
minutes: this module.

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
1,000,000 runs x 10 plots x 365 days in 3.56s (281,086 runs/s)
  overall money:   mean=    13.83  stddev=   18.62  min=     0.00  max=   167.42
  overall harvest: mean=   153.09  stddev=   98.48
  greedy       (n= 333,330): money mean=    35.16 stddev=   18.55  harvest mean=   55.44
  conservative (n= 333,340): money mean=     3.78 stddev=    2.50  harvest mean=  271.22
  random       (n= 333,330): money mean=     2.54 stddev=    2.56  harvest mean=  132.59
```

Measured on a single CPU core's worth of `numba(parallel=True)` work (see
Performance below): **1,000,000 runs in ~3.0s, peak RSS ~264MB** — well
inside the prompt's <60s CPU / 2GB targets (20x and 7.8x margin
respectively), with headroom for a much bigger `total_runs` before either
budget binds.

```bash
# validate the numba kernel against the pure-Python sequential reference
python3 scripts/vectorized_validate.py

# benchmark runs/sec at a few chunk sizes, project to 1M, report peak RSS
python3 scripts/vectorized_benchmark.py
python3 scripts/vectorized_benchmark.py --sizes 1000 20000 100000
python3 scripts/vectorized_benchmark.py --compare-existing-engine   # also times main.py batch
```

## Data contract (`state.py`)

Structure-of-Arrays, flat numpy arrays, no per-run Python object:

```
money[B]              float32     total_harvest[B]    float32
strategy_id[B]        int8        moisture[B,P]        float32
nitrogen[B,P]         float32     crop_type[B,P]        int8   (-1 = empty)
growth_stage[B,P]     int8        days_to_harvest[B,P]  int16
rng_run_state[B]      uint64      rng_plot_state[B,P]   uint64
```

The last two aren't in the prompt's field list but travel with the rest of
the SoA rather than living in a side object — they're what makes a chunk's
results independent of chunk size and chunk position (see next section).

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
   docstrings). They're checked to agree within float32 tolerance across a
   spread of seeds, run indices, strategies, and plot counts.
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
  dependency) — see `kernel.py`'s docstring for the full argument. It reaches
  the actual goal (millions of runs, wall-clock budget) more reliably than
  chasing numba's auto-parallelization on masked array code would have.
- **3 illustrative crops, not `config/crops.json`.** Reusing the real config
  would either couple this experimental module to the balance-tuning config
  (an edit made for `main.py batch` purposes silently changes this module's
  numbers too) or require reimplementing `simulation/derived.py`'s config
  resolution — see `crops.py`'s docstring.
- **No JAX migration was needed to hit the target.** The numpy + numba path
  already clears <60s CPU by ~20x margin at 1M runs (see Performance below),
  so JAX wasn't pursued. Migration notes below in case GPU throughput becomes
  the actual constraint later.

## Risks: the "isolate what can't be vectorized" escape hatch

All three strategies (Greedy/Conservative/Random) mask-vectorize cleanly —
nothing in `run_millions` needs the fallback. `orchestrator.
run_isolated_strategy_fallback` is still a real, exercised path (not just a
claim): it runs `vectorized.reference`'s scalar per-run loop for one strategy
id, in small batches, folding into the same `StreamingStats` the vectorized
path uses. If a future strategy has decision logic that can't be expressed as
array masks (e.g. it needs cross-plot search, not just per-plot thresholds),
route its `strategy_id` through this function instead of the kernel and merge
its `StreamingStats` with the rest — the pattern to follow is that function's
body and docstring.

## Performance

Measured on this machine, `.venv` (Python 3.12.9, numpy 2.5.2, numba 0.67.0),
`P=10` plots, `num_days=365`, one `run_millions` call per process (via
`/usr/bin/time -l`, so each row's peak RSS is isolated rather than a running
high-water mark across multiple sizes in one process):

| Runs | Plots × Days | Wall time | Throughput | Peak RSS | vs. targets |
|---:|---:|---:|---:|---:|---|
| 1,000 | 10 × 365 | 0.014 s | ~73,500 runs/s | 103 MB | — |
| 10,000 | 10 × 365 | 0.041 s | ~246,300 runs/s | 108 MB | — |
| 100,000 | 10 × 365 | 0.290 s | ~344,700 runs/s | 160 MB | — |
| 500,000 | 10 × 365 | 1.489 s | ~335,900 runs/s | 268 MB | — |
| **1,000,000** | **10 × 365** | **2.99 s** | **~334,500 runs/s** | **264 MB** | **20x under 60s · 7.8x under 2GB** |

(numba JIT compilation of `simulate_chunk` — a few hundred ms — happens once
per process on first call and is excluded from these figures, same as the
main engine's Cython/`_fastplot` builds are one-time costs excluded from
`sample_profile.py` numbers.) `--compare-existing-engine` on
`vectorized_benchmark.py` times `main.py batch` alongside this for a
wall-clock reference point — see that script's docstring for why it's not an
apples-to-apples comparison of the same economic model.

Two shapes worth reading, not just the headline row:

- **Throughput ramps then plateaus, it doesn't keep climbing.** 1k→10k→100k
  runs/sec rises sharply (per-call dispatch/allocation overhead amortizing
  over more runs), then flattens around ~335,000 runs/s from 100,000 runs
  onward — same shape as the main engine's straggler-effect writeup for
  worker count in `CLAUDE.md`'s Performance section. It flattens out well
  before 100,000 runs/chunk, which is why `DEFAULT_MAX_CHUNK = 100_000` was a
  reasonable default rather than something to tune further.
- **Peak RSS is chunk-bounded, not run-count-bounded — but not perfectly
  flat.** It climbs 103→160MB from 1k→100k runs (still one chunk the whole
  way, since `total_runs <= DEFAULT_MAX_CHUNK`), then steps up to ~265–270MB
  at 500k/1M runs (5 and 10 chunks respectively) and *stays flat between
  those two* rather than continuing to climb with `total_runs` — consistent
  with the streaming `del state; gc.collect()` design. The step-up itself is
  most likely allocator arena retention (macOS/glibc malloc not always
  returning freed pages to the OS between chunks) rather than an actual
  per-chunk leak; either way it's still 7.8x under the 2GB budget.

## Migration notes: swapping in JAX later

Not needed to hit this prompt's target (20x margin above target already, on
CPU, single process) — recorded here since the prompt asked for it.

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
  mask-vectorized style the original prompt described in component B.
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
