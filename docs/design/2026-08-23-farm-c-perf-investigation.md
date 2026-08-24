# farm-c performance investigation — 2026-08-23

Companion to `2026-08-11-perf-investigation.md`, which covered the Python
simulator. This one covers the C port only. Method and constraints are the
same: wall clock over profiler self-time, and every change must leave the
port bit-exact — `make test`, `make golden-check-profile`, and both
`c-parity` modes gate everything below.

## Method

`build/farm-c-release` (`-O2`, no sanitizers), `/usr/bin/time -l`, and
`sample` at 1 ms, against `../config`. Sequential profiles taken over
4000-run batches, parallel over 12000-run batches, on a 6P+2E core machine.
Speedups are interleaved min-of-5 between two binaries built from the same
sources, so flag attribution is real rather than inferred.

Tree state: the uncommitted `py_round_ndigits` `__int128` fast path landed
mid-investigation. Every number here is against a build that includes it.
That change is separately a large win — 8-worker system time 1.01s → 0.14s,
wall clock 0.68s → 0.28s on `--runs 300` — and nothing here revisits it.

## Headline: the documented binary was the wrong binary

`./farm-c` — what `README.md` and `farm-c/CLAUDE.md` used in every command
example — is built with `-fsanitize=address,undefined` at `-O0`. On
`batch --runs 100 --seed 42 --workers 8`:

| Binary | wall | user |
|---|---|---|
| `./farm-c` (ASan+UBSan, `-O0`) | 1.61s | 8.76s |
| `build/farm-c-release` (`-O2`) | 0.08s | 0.45s |

**20x, for a byte-identical CSV.** An optimized build existed but was named
`profile` and framed as a profiling-only tool ("the only build to time"), so
nothing told a reader it was also the build to *run*. No source change in
this investigation came close to mattering as much.

Fixed by renaming the target to `release` (`profile` kept as an alias) and
rewriting the build guidance in both files: `./farm-c` tests,
`build/farm-c-release` simulates.

## Where sequential time went

Share of all samples, `--workers 1`, before any change here:

| Bucket | Share | Detail |
|---|---|---|
| `_tlv_get_addr` (libdyld) | **7.1%** | hottest single leaf in the program |
| SoA gather/scatter accessors | **10.8%** | `planted_crop_columns_{get,set}`, `plot_columns_{get,set}` |
| malloc/free | 4.9% | `vec_grow`, per-day decision buffers, per-call vectors |
| `strcmp` + PLT stubs | 3.1% | crop-family comparison in per-plot loops |
| `config_find_crop`/`_item` | 1.8% | one bounds check each, not inlined |

Roughly **22% of runtime was call overhead, TLS indirection, and PLT stubs
for functions doing almost no work** — all of it because these are one-line
accessors defined in a different translation unit from every caller, with no
LTO in the build.

## What landed

### 1. `make release` (was `make profile`)

See headline. Documentation change plus a target rename; no codegen change.

### 2. `-flto` on the release build — 1.32x / 1.24x

| | before | after | |
|---|---|---|---|
| `--runs 1000 --workers 1` | 2.910s | 2.210s | **1.317x** |
| `--runs 2000 --workers 8` | 1.680s | 1.360s | **1.235x** |

This is the whole 22% bucket being collected. It also took `_tlv_get_addr`
from **7.1% to 1.0%**, by inlining the allocation-latch readers so the TLV
lookups hoist out of the loops that were repeating them.

Two things this required, both load-bearing:

- **`$(FP)` must be on the link line.** With LTO the real codegen happens at
  link, so `-ffp-contract=off` has to reach it or FMA contraction returns at
  the one step nothing else inspects. It already did; `make
  golden-check-profile` is what proves it, which is why `make test` runs it.
  All 44 baseline combos pass and the full-precision CSV is byte-identical at
  `--workers 1` and `8`.
- **`-Wl,-object_path_lto,...`.** LTO writes its real object to a temp path
  the linker deletes, so `dsymutil` finds no debug info and `sample` reports
  the entire binary as `???`. That matters more than usual here because this
  is now also the build you are told to profile. The flag keeps the object.

### 3. Per-day decision buffers pooled onto `FarmState` — 1.02x / 1.05x

`engine.c` steps 12/13/14/15 declared `ContractDecisionBuffer accepts = {0}`
and friends as locals and freed them at the bottom of every simulated day —
four malloc/free pairs per day per run. They now live on `FarmState`
(alongside the three existing `ScratchBuffer`s, same precedent) and are reset
to `count = 0` before each agent call, so they reach steady-state capacity in
the first few days and never allocate again.

The buffer types moved from `agent.h` down into `state.h` because `agent.h`
already includes `state.h`; `agent.h` keeps the vtable and a pointer to the
new home, so no other file's includes changed. `farm_state_destroy` releases
them, on the failure paths too — which is why `ENGINE_CHECK_ALLOC` no longer
frees. `test_engine.c`'s four allocation-failure cases still pass under ASan.

Larger at 8 workers (1.053x) than sequentially (1.018x), as allocator
traffic is contended.

### Combined

| | before | after | |
|---|---|---|---|
| `--runs 1000 --workers 1` | 2.910s | 2.160s | **1.347x** |
| `--runs 2000 --workers 8` | 1.680s | 1.320s | **1.273x** |

Byte-identical CSV; 44/44 golden on both the sanitized and release builds;
`c_parity.py check` and `baseline` both OK.

## What was measured and rejected

### `-ftls-model=local-exec` — 1.00x, and it is a no-op here

`_tlv_get_addr` at 7.1% looked like a one-flag fix. It is not: **the flag
does nothing on Mach-O.** clang emits identical `TLVP` descriptor +
`__tlv_bootstrap` codegen with and without it — verified by compiling a
one-line `_Thread_local` translation unit both ways and diffing the assembly,
and by the two full binaries being functionally identical. TLS models are an
ELF concept. Removing that tax at the source would mean threading a
per-worker context struct instead of the `_Thread_local` file statics in
`contracts.c` and `rng_hash.c`. LTO made the question much less pressing
(7.1% → 1.0%).

### Per-run `FarmState` pooling — worth 0.017%, not done

This was the plan's Track 3b: reuse one `FarmState` per worker thread instead
of `farm_state_init`/`farm_state_destroy` per run, on the theory that ~40
allocations and ~40 frees per run were worth a few percent.

Measured directly, with a standalone benchmark calling the real pair in a
loop against the real config:

```
farm_state_init + farm_state_destroy: 0.37 us per pair
one 365-day run:                      ~2207 us
```

**0.017%.** The allocator stays on its nano-zone fast path and the arrays are
small. Not implemented — it is the single most determinism-dangerous change
on the list (one field `reset` forgets leaks run N into run N+1, and which
seeds expose it is luck) in exchange for nothing measurable.

Note the trap that nearly hid this: extrapolating fixed cost from a
`--days 365` vs `--days 730` pair gives "20.4% fixed", because per-day cost
*falls* as a run goes on (runs end early on bankruptcy). The `--days 1` point
(95 µs/run, including a day of simulation and a CSV row) already bounded the
truth an order of magnitude below that. Two points do not make a line here.

### The SoA measurement gap

`34424e5` and `8ef7ad6` converted `FarmState.plots` and `FarmState.planted`
to structure-of-arrays and recorded **no before/after measurement**. Before
LTO, their gather/scatter accessors were the largest single item in the
profile at 10.8% — `weather.c:113-117` gathers 10 plot columns and 13 planted
columns into stack rows, calls `crop_growth_update_stress`, and scatters both
back, per planted plot per day, purely to keep that function's plain-pointer
signature for `tests/test_physics.c`. LTO now inlines that away, so the cost
is largely recovered without touching the layout, but the question of whether
SoA beat the AoS it replaced was never answered and still is not.

## Ranked candidates not taken

1. **Kill the gather/scatter shim** (`src/weather.c:113-117`,
   `src/actions.c:172-187`, `src/contracts.c:277`) — have the physics
   functions take column pointers plus an index. LTO recovered most of this;
   what remains is the redundant copying of fields the callee never reads.
2. **Intern `crop->family` to an integer id at config load** — removes
   `strcmp` from `src/economy_rules.c:128`, in a per-plot loop reached per
   candidate crop per reserve fraction per open slot per day (~1000+/day),
   and from `src/crop_growth.c:73`. `strcmp` is still 5.1% of the current
   profile, now the largest non-farm-c bucket.
3. **Reuse a `_Thread_local StrBuf` in `rng_hash.c`'s `rng_decision_random`**
   instead of malloc/free per decision. `random_agent` only, but that
   strategy does 20-30 string-build + BLAKE2b hashes per simulated day, and
   it is the dominant caller of both the remaining allocator traffic and the
   remaining `snprintf`/`dtoa` time.
4. **`actions_plant_seed` rescans plots from zero** (`src/actions.c:32-38`),
   defeating the monotonic `next_plot` cursor `src/engine.c:79-87` maintains.
   Filling k slots is O(n·k), not the O(n) the comment claims.
5. **`contracts.c` per-call vectors** — ~6-10 malloc/free pairs per product
   feasibility check. The blocker is the `const FarmState *` call chain
   documented at `src/contracts.c:407-428`, which is why
   `contracts_scratch_sort` is already a `_Thread_local` file static.
6. **Per-slot condvars in the reorder ring** (`src/batch.c:212`, `:259-261`)
   — one shared `result_ready` condvar means `pool_publish` wakes the
   deliverer for slots it is not waiting on. Low value: the delivering thread
   is idle ~70% of the time already.

## Parallel scaling, for the record

After the `py_round_ndigits` fix, `--runs 300 --seed 42`: 0.88s / 0.46s /
0.31s / 0.28s at 1 / 2 / 4 / 8 workers (3.1x). Workers 6, 8, and 12 are
indistinguishable (~0.50s at `--runs 600`) — the 2 E-cores contribute
nothing, so the default `os.cpu_count()`-style worker count is already at the
knee. The main thread spends ~70% of its time blocked in `batch_run_parallel`
waiting on the reorder ring, so CSV and HTML writing are not a bottleneck.

## Re-running this

```bash
cd farm-c
make release
./build/farm-c-release batch --runs 4000 --seed 42 --workers 1 --no-progress --csv /tmp/x.csv &
sample $! 8 1 -f /tmp/sample.txt        # symbolizes thanks to -Wl,-object_path_lto
sed -n '/Sort by top of stack/,$p' /tmp/sample.txt | head -30
```

Never profile or time `./farm-c`; see `farm-c/CLAUDE.md`.
