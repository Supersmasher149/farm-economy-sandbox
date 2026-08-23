# farm-c: a pure-C port of the farm-economy simulator (in progress)

This is a self-contained, standalone build of the pure-C port described in
[`docs/c-port-plan.md`](../docs/c-port-plan.md), built up phase by phase
against real (not placeholder) C types and checked at every phase against
the real Python modules as the oracle -- see
`.claude/plans/shimmying-singing-dragonfly.md` for the phase plan this
follows. It started as just the 11 agent strategies in `../agents/*.py`
(the decision surface) plus every shared "pure economics" helper they call,
and has since grown the RNG, physics, state-mutation, production
configuration, modern daily-engine, single-run, and batch layers underneath
them (Phases 0-6, described below). The C engine is directly callable
through `engine_run_day`; `farm-c single` runs one complete simulation and
`farm-c batch` runs every strategy many times each. The legacy `world=None`
path remains out of scope.

The six phases so far:

- **Phase 0**, `src/rng.c`: a bit-exact MT19937 port of `random.Random`, the
  single-generator RNG `../simulation/random_events.py:RandomEvents` wraps
  and every physics/market/contract module downstream of it depends on. It
  doesn't run inside the agent port above (agents never touch the RNG
  directly) -- it was staged ahead of Phase 1 as the first module that
  actually calls it.
- **Phase 1**, `src/crop_growth.c` + `src/weather.c` (+ `src/pyfloat.c` for
  shared float-semantics helpers): a bit-exact port of
  `../simulation/crop_growth.py` and `../simulation/weather.py` --
  per-plot daily stress accumulation, harvest yield/quality multipliers and
  grading, and season/weather generation. `crop_growth_update_stress` and
  `weather_apply`'s per-plot loop reuse the arithmetic already proven in
  `../simulation/_fastplotmodule.c` (same Neumaier summation, same literal
  `max`/`min` clamp forms); `harvest_multipliers`, `quality_grade`, and
  `compute_harvest_outcome` are fresh ports, since the fastplot kernel
  doesn't cover them. `weather.py`'s `round(x, ndigits)` calls are ported as
  a `snprintf("%.*f")` + `strtod` round-trip (`pyfloat.c:py_round_ndigits`)
  rather than a from-scratch port of CPython's David Gay dtoa -- both target
  libcs implement correctly-rounded decimal conversion, which is the
  property CPython's own dtoa/strtod pair relies on, and this is checked
  against real recorded `round()` output rather than assumed (see
  Verification below).
- **Phase 2**, `src/actions.c` (new) plus extensions to `src/inventory.c`,
  `src/markets.c`, `src/contracts.c`, and a new `src/processing.c`: every
  state-*mutating* function the modern (`world`-driven) `run_day` path
  calls -- buying/planting/watering/fertilizing/harvesting crops, buying
  upgrades, FEFO inventory consumption, storage aging/spoilage/capacity
  enforcement, daily price updates, channel sales, processing jobs starting
  and completing, and the full contract lifecycle (offer generation,
  accept, deliver, expiry resolution). `FarmState` (`state.h`) grew the
  rest of `PlayerState`'s fields these need (market supply/revenue-by-
  channel, the various running totals, quality/loss breakdowns, ...), and
  `config.h` grew the config fields Phase 0/1 never needed (buyer offer-
  generation terms, storage/markets top-level config, recipe shelf life,
  fertilizer nutrients-added, ...). This also let `contracts.c` retire its
  one documented simplification: `_best_possible_grade`'s
  `QUALITY_STANDARD` stand-in now calls the real
   `crop_growth_harvest_multipliers`/`crop_growth_quality_grade`, since
   Phase 1 supplies the stress physics it needs.
- **Phase 3**, `src/config_loader.c` plus the production cJSON vendor:
  directory loading for the eleven world configuration files and separate
  simulation settings, with resolved defaults, indexed references, and
  schema/range/enum validation.
- **Phase 4**, `src/engine.c`: the modern, world-driven 23-step daily engine.
  It uses the existing mutators and Agent vtable, preserves their iteration
  and RNG order, applies effective storage and processing upgrades, and keeps
  decision buffers private to each phase. Step 17 waters *and* fertilizes in
  one pass per crop, matching `simulation/engine.py:176-183`. It was two
  separate passes until the `c-parity` skill caught it: both actions spend
  money, so splitting them reorders the debits, which shifted the rounding of
  every later balance and changed which actions were affordable when cash was
  tight. The legacy `water_farm` and
  `sell_all` path is deliberately not implemented.
- **Phase 5**, `src/runner.c` and `main.c`: the reusable single-run lifecycle
  and `farm-c single` CLI, including explicit or generated seeds, optional
  daily history, and human-readable final summaries.
- **Phase 6**, `src/batch.c` and `farm-c batch`: the C analogue of
  `../runner/batch_run.py` -- run every strategy `--runs` times each off one
  base seed. Seed minting is bit-exact with Python's:
  `rng_randrange_2_32` (`src/rng.c`) ports the `getrandbits(33)` path
  `random.Random.randrange(2**32)` takes -- one bit past every other RNG
  call in this port, which only ever needed `getrandbits(k<=32)` -- so a
  `--seed` shared between `farm-c batch` and `python3 main.py batch` mints
  the identical per-run seed sequence in the identical agent-major order,
  letting the two be cross-checked run-for-run. Each run's outcome streams
  through a callback as a flattened `BatchRunResult` (the scalar subset of
  `metrics/run_results.py`'s `RunResult` -- no crop-count/percentage dicts)
  and its `FarmState` is freed before the next run starts, so peak memory
  stays bounded independent of batch size, same as `run_batch`'s own
  streaming discipline. `farm-c batch` aggregates those into a per-strategy
  summary table and, with `--csv`, a raw per-run CSV.
- **Phase 7**, `src/aggregate.c`, `src/warnings.c`, `src/dashboard.c`: the
  reporting layer `docs/c-port-plan.md` Section 12 lists as its Later
  Version. `warnings.c` ports `metrics/warnings.py`'s threshold rules;
  `dashboard.c` writes `--html`, a self-contained interactive report drawn
  by vanilla JS from an embedded payload (no dependencies, opens offline).
  Both read `aggregate.c`, which owns every derived per-strategy value so
  the terminal table, the warnings and the page cannot disagree about what
  an average means -- the same single-source-of-truth rule `../CLAUDE.md`
  states for the Python reporting path. `summary.json` and
  `summary_report.md` remain out of scope.

- **Phase 8**, `src/trajectory.c` and `src/golden.c`: the port's own
  replay gate. `trajectory.c` hashes a full per-day state snapshot into a
  chained digest whose bytes are reproducible from Python; `golden.c` adds
  `farm-c golden capture|check|trace|payload` over a committed baseline.
  See Verification below.

The engine is wired into a reusable single-run runner (`farm-c single`) and a
batch driver (`farm-c batch`). The legacy `world=None` path remains out of
scope.

## Scope boundary

**Faithful, full-fidelity port.** Everything below matches its Python
source function-for-function, including tie-break behavior and rounding.
Phases 0-1's functions are pure (no `FarmState` mutation); Phase 2's are
the state-*mutating* functions the same "modern `run_day` path only" scope
decision includes -- see the Phase 2 paragraph above:

- All 18 functions of `../simulation/economy_rules.py` (`src/economy_rules.c`)
  -- the core of every agent's crop/upgrade/fertilizer decisions.
- `../simulation/markets.py`'s `quote`, `best_channel`, and
  `QUALITY_MULTIPLIERS` (`src/markets.c`).
- `../simulation/inventory.py`'s `available_quantity` (`src/inventory.c`).
- `../simulation/derived.py`'s `effective_growth_days` and
  `nutrient_demand_total` (`src/derived.c`) -- the Python versions memoize on
  config-object identity purely because they sit on a per-plot-per-day hot
  path re-reading a mutable-dict config; a static C `ResolvedConfig` needs no
  such cache, so it's a straight recompute here.
- `PlayerState.decision_random` (`src/rng_hash.c` + a vendored minimal
  BLAKE2b, `src/blake2b.c`) -- RandomAgent's hash-based policy stream, ported
  bit-for-bit: same BLAKE2b digest, same Python-`repr()`-shaped payload
  string for the exact four argument shapes `../agents/random_agent.py`
  passes. Verified against `hashlib.blake2b` directly (see git history) for
  BLAKE2b known-answer vectors and all four call shapes.
- All 11 agents (`src/agents/*.c`), function-for-function against their
  Python source, including the exact same constants and tie-break behavior
  (Python's `min`/`max` keep the first element on an exact tie; every C loop
  here replaces its running best only on a *strict* inequality to match).

- `../simulation/crop_growth.py`'s `update_crop_stress`, `harvest_multipliers`,
  `quality_grade`, and `compute_harvest_outcome` (`src/crop_growth.c`).
- `../simulation/weather.py`'s `season_for_day`, `generate_weather`, and the
  plain (non-`_fastplot`-accelerated) body of `apply_weather`
  (`src/weather.c`) -- ported line-for-line from the reference Python loop,
  not from the optional accelerator, so every conditional field write
  matches Python's `if regen_x: plot.x = ...` exactly.
- `../simulation/actions.py`'s modern-path functions -- `buy_seeds`,
  `plant_seed`, `water_crop`, `buy_fertilizer`, `fertilize_crop`,
  `harvest_mature`, `buy_upgrade`, `do_nothing` (`src/actions.c`).
  `water_farm`/`sell_all` back only the legacy (`world=None`) `run_day`
  path this port doesn't target (see `docs/c-port-plan.md`'s scope
  decision) and are intentionally not ported.
- `../simulation/inventory.py`'s remaining functions -- `consume` (FEFO),
  `capture_storage_liability`, `collect_storage_liability`,
  `enforce_storage_capacity`, `age_and_spoil` (`src/inventory.c`).
- `../simulation/markets.py`'s `update_daily_prices` and `sell`
  (`src/markets.c`).
- `../simulation/processing.py`'s `start_job` and `complete_jobs`
  (`src/processing.c`, new).
- `../simulation/contracts.py`'s day-loop mutators -- `generate_offers`,
  `accept`, `deliver`, `resolve_expired`, `visible_offers`,
  `offer_expiry_day`, `is_offer_expired` (`src/contracts.c`). Also,
  `_best_possible_grade` (used by `_future_crop_arrivals`, and therefore by
  the already-ported `contracts_is_offer_feasible`/
  `contracts_forecast_committed_supply`) is no longer the `QUALITY_STANDARD`
  stand-in the agent-decision slice shipped with -- it now calls the real
  `crop_growth_harvest_multipliers`/`crop_growth_quality_grade`, since
  Phase 1 supplies the stress physics it needs. This retires the one
  documented simplification the agent-only slice carried.

## Layout

```
include/            farm_types.h, config.h, state.h, agent.h, economy.h,
                     markets.h, inventory.h, contracts.h, derived.h,
                     rng.h, rng_hash.h, blake2b.h, vec_util.h, pyfloat.h,
                     crop_growth.h, weather.h, actions.h, processing.h,
                     engine.h, runner.h, batch.h, aggregate.h, warnings.h,
                     dashboard.h
src/                 economy_rules.c, markets.c, inventory.c, contracts.c,
                     derived.c, rng.c, rng_hash.c, blake2b.c, config.c,
                     state.c, vec_util.c, agent.c, agent_registry.c,
                     pyfloat.c, crop_growth.c, weather.c, actions.c,
                     processing.c, engine.c, runner.c, batch.c,
                     aggregate.c (per-strategy stats, one source of truth
                     for every derived number), warnings.c (balance rules),
                     dashboard.c + dashboard_js.h (the --html report; the
                     .h holds its CSS/JS as string literals)
src/agents/          base.c (shared defaults + route_sales_by_best_price),
                     one file per agent, matching ../agents/*.py 1:1
tests/               test_pyfloat.c (differential test of
                     py_round_ndigits' exact integer fast path against its
                     own libc round-trip reference),
                     test_agents.c (fixture-driven parity test for the
                     agent port), test_rng.c (same, for rng.c),
                     test_physics.c (same, for crop_growth.c/weather.c),
                     test_mutation.c (same, for actions.c/inventory.c/
                     markets.c/processing.c/contracts.c's mutators),
                     test_engine.c (23-step ordering, per-crop water/
                     fertilize interleaving, bookkeeping, failure cleanup,
                     and repeatability), test_runner.c
                     (single-run lifecycle), test_batch.c (seed minting
                     against real Python `randrange(2**32)` output, job
                     order, batch-vs-single-run parity, and that every run's
                     every field is identical at every worker count),
                     test_dashboard.c (--html payload well-formedness,
                     agreement with the aggregator, escaping, and the
                     self-contained property), test_cli.sh (CLI smoke
                     tests, including that neither --html nor --workers
                     changes batch results for a fixed seed),
                     fixtures/{agents,rng,physics,mutation}.json (generated,
                     checked in), third_party/cJSON.{h,c} (legacy fixture
                     copy); include/cJSON.h and src/cJSON.c are the production
                     vendored parser.
```

## Building and testing

### Production configuration

The production loader reads the eleven world files and the separate
`simulation_settings.json` from a directory:

```c
ResolvedConfig world;
SimulationSettings settings;
ConfigError error;
config_load_directory("../config", &world, &error);
config_load_simulation_settings("../config", &settings, &error);
config_destroy(&world);
```

`ResolvedConfig` owns its strings, arrays, and buyer item lists. Call
`config_destroy` after both successful and failed loads; it is safe on a
zeroed or failed object. The loader rejects unknown fields, invalid types,
 ranges, enums, duplicate IDs, and unresolved references. The production
 `src/cJSON.c` object is linked directly; the fixture
loaders in the older parity tests remain test-only and are not used by this
API.

```bash
cd farm-c
make fixtures          # regenerates tests/fixtures/agents.json from the
                        # real Python agents (needs the repo's venv; see
                        # ../tools/export_agent_fixtures.py)
make fixtures-rng      # regenerates tests/fixtures/rng.json from CPython's
                        # random.Random (see ../tools/export_rng_fixtures.py)
make fixtures-physics  # regenerates tests/fixtures/physics.json from the
                        # real crop_growth.py/weather.py (see
                        # ../tools/export_physics_fixtures.py)
make fixtures-mutation # regenerates tests/fixtures/mutation.json from the
                        # real actions.py/inventory.py/markets.py/
                        # processing.py/contracts.py (see
                        # ../tools/export_mutation_fixtures.py)
 make test              # builds and runs every tests/test_* binary under
                         # -fsanitize=address,undefined, including runner
                         # and batch tests, then checks the committed
                         # golden baseline
 make golden-check      # just the golden baseline
 make golden-capture    # re-records it (see Verification)
 make farm-c            # builds the single-run/batch CLI
```

`make test` alone (no Python needed) re-runs against whatever fixtures are
already checked in, and re-checks the committed golden baseline the same way.

### Single runs

From `farm-c/`, run one configured simulation:

```bash
make farm-c
./farm-c single --strategy profit_optimizer --seed 42
./farm-c single --strategy fast_seller --seed 42 --verbose
./farm-c single --config ../config --strategy random_agent
```

The default strategy is `profit_optimizer`, the default config directory is
`../config`, and an omitted seed is generated and printed as `actual_seed`.
Successful bankrupt runs still exit zero; invalid arguments, configuration,
unknown strategies, and engine failures exit nonzero. Output is intended for
humans and is not yet a stable machine-readable report.

The public runner API is in `include/runner.h`. `runner_run_single` borrows
the `ResolvedConfig` and `Agent`, but owns the `FarmState` stored in its
`RunResult`. Call `runner_run_result_destroy` exactly once for every result,
including a partially initialized result after failure. The per-day callback
receives read-only state after each completed day; its context remains owned by
the caller.

### Batch runs

From `farm-c/`, run every strategy `--runs` times each off one base seed:

```bash
make farm-c
./farm-c batch --runs 1000 --seed 42
./farm-c batch --runs 1000 --seed 42 --csv reports/run_results.csv
./farm-c batch --runs 1000 --seed 42 --html reports/dashboard.html
./farm-c batch --runs 200 --strategy fast_seller --strategy profit_optimizer
./farm-c batch --runs 100 --days 30 --start-money 300     # diagnostic overrides
./farm-c batch --runs 1000 --seed 42 --workers 1          # force the sequential path
./farm-c batch --runs 1000 --seed 42 --workers 4          # pick the worker count
```

An omitted `--strategy` runs the full 11-agent roster in `AGENT_REGISTRY`
order; repeat `--strategy NAME` to run a subset. An omitted `--seed` mints a
fresh base seed (`/dev/urandom`-backed) and prints it as `base_seed`, the
batch analogue of `single`'s `actual_seed`. `--days`/`--start-money` override
`simulation_settings.json` for this batch only, matching
`python3 main.py batch --runs N --days N --start-money N`'s diagnostic
overrides. `--csv PATH` writes one row per completed run (see
`include/batch.h`'s `BatchRunResult` for the column set -- the scalar subset
of `metrics/run_results.py`'s `RunResult`, no crop-count/percentage dicts);
without it, only the per-strategy summary table prints. Either way the
summary is followed by the balance warnings `src/warnings.c` ports from
`metrics/warnings.py` -- the signal `main.py batch`'s report leads with.

`--html PATH` writes an interactive report (`src/dashboard.c`): a sortable
overview table, the warnings, and five charts -- final-money distribution,
bankruptcy rate, average profit per day, crop mix, and watering rate vs crop
loss rate as one scatter panel per strategy. Clicking a strategy filters
every panel at once; hovering a scatter point names its seed, and clicking
one fills in the `farm-c single --strategy X --seed N --verbose` that
reproduces exactly that run.

Three things worth knowing about it. It is **fed from the batch, not from
the CSV** -- `BatchRunResult` carries `crop_plant_counts`,
`total_crops_lost`, `total_harvest_events` and `slot_days`, which the 22 CSV
columns drop and the crop-mix and scatter panels need; the cost is that
`--html` renders the batch you are running, not an archived one. It is
**one self-contained file** with no CDN, no external stylesheet, and no
network access at all -- charts are drawn by ~200 lines of vanilla JS
(`src/dashboard_js.h`) building SVG from an embedded payload, the same
opens-offline property `metrics/dashboard.py` gets by base64-inlining its
PNGs. And the payload is a **display artifact**: doubles are written at
`%.10g`, not the `%.17g` the CSV uses, so `run_results.csv` remains the
exact record of a run. Page size is roughly 1.5 MB for a full
`--runs 1000` batch (11,000 rows).

There is still no `summary.json`/`summary_report.md` equivalent here.

## Parallel batches

`farm-c batch` spreads its runs over one worker thread per CPU core by
default. `--workers N` sets the count; `--workers 1` forces the sequential
loop, which is still the reference the parallel path is checked against.
Python needs a *process* pool because the GIL makes threads useless for
CPU-bound work; C does not, so this is threads over one address space,
sharing the single immutable `ResolvedConfig` rather than pickling a copy
per worker.

**Worker count is a performance knob and nothing else.** For a fixed
`--seed`, the CSV, the HTML dashboard, the summary table and the warnings
are byte-identical at every worker count. Three things get that
(`include/batch.h` has the full argument):

1. Seeds are still minted by one `FarmRng` in strict job order -- workers
   claim jobs under a mutex and mint inside that critical section, so job
   *i* gets the *i*-th `randrange(2**32)` draw no matter which thread runs
   it.
2. Runs share nothing: a `FarmState`, a `FarmRng` and a `const
   ResolvedConfig *` nobody writes to, with stateless const agent
   singletons. The two file-scope scratch buffers on the run path
   (`contracts.c`, `rng_hash.c`) were already `_Thread_local`.
3. Results are *delivered* in job order, not completion order. A bounded
   reorder ring parks finished runs until their turn while the calling
   thread drains it sequentially, so `on_result` fires in exactly the
   agent-major order the sequential path used. Summing the same doubles in
   a different order would not give the same aggregates, which is why
   completion-order delivery is not an option.

The ring is also what bounds memory: at most `4 * workers` runs are in
flight or awaiting delivery, so peak memory tracks worker count, never
batch size.

`tests/test_batch.c` asserts every field of every run is identical across
worker counts 1/2/3/5/8/64 and auto, using a roster whose run lengths differ
wildly (`reckless_spender` goes bankrupt early, `profit_optimizer` plays the
full horizon) so workers really do finish out of order.
`tests/test_cli.sh` repeats the check on the CSV and the HTML report.

### What the speedup actually cost

Measured on an 8-core machine, `--runs 500` (5,500 runs), `-O2` profile
build, best of three:

| workers | 1 | 2 | 4 | 6 | 8 |
|---|---|---|---|---|---|
| wall clock | 1.43s | 0.75s | 0.47s | 0.39s | 0.39s |

The first attempt scaled only **1.6x** at 8 workers while burning 3.5x the
CPU, 1.4s of it in the kernel. The pool was not the problem: the contended
lock was inside libc. `py_round_ndigits` implemented Python's
`round(x, ndigits)` as `snprintf("%.*f")` + `strtod`, and on Darwin both
reach `localeconv_l()`, which takes a **process-wide** `os_unfair_lock`.
`weather_generate` rounds three values a day, so a batch made ~1.5M calls,
every one of them through one global lock.

`src/pyfloat.c` now answers the common case from exact 128-bit integer
arithmetic instead of decimal text: `x` is a dyadic rational `m * 2^e`, so
`x * 10^n` is exactly `m * 5^n * 2^(e+n)`, and rounding *that* half-to-even
is the first of the round-trip's two roundings, exact by construction. The
second -- decimal `I / 10^n` back to the nearest double -- is a single IEEE
division of two exactly-representable operands whenever `|I| <= 2^53` and
`n <= 22`, which is correctly rounded and therefore is what `strtod`
returns. Anything outside those preconditions declines and falls back to the
libc round-trip, which remains the definition of the operation.

That is not a claim to be taken on trust, so `tests/test_pyfloat.c` is a
differential test rather than a table: it recomputes the libc round-trip and
demands bit-identical results over 2.69M cases -- the real weather ranges
swept densely, exact halfway cases (which is what rules out a
`nearbyint(x*10^n)/10^n` shortcut), 300k random doubles across the full
exponent range, subnormals, both zeros and both infinities. Comparison is by
bit pattern, never `==`, because `round(-0.0, 2)` must stay negative zero --
the first thing the test caught was exactly that, a sign read off the
mantissa instead of the sign bit.

Removing the lock made the sequential path **1.25x** faster too, and took
kernel time at 8 workers from 1.42s to 0.20s.

Seed minting is bit-exact with `runner/batch_run.py`'s
`seed_rng.randrange(2**32)`: `rng_randrange_2_32` (`src/rng.c`) is the one
place in this port that draws `getrandbits(33)` rather than the `k<=32` fast
path every other RNG call uses (see its header comment for how CPython
builds that extra bit). Practically, this means a `--seed` shared between
`farm-c batch` and `python3 main.py batch` mints the identical per-run seed
for the identical strategy at the identical position in the run list, so
the two can be diffed run-for-run -- confirmed by hand for `fast_seller`
seed 42 in `tests/test_batch.c` and again against a live `run_single` call
during development (see git history), both bit-exact on `money`, `revenue`,
`expenses`, and `total_harvested`.

Every compile/link rule builds with `-ffp-contract=off`. Without it, a
compiler may legally fuse an expression shaped like `a + (b - a) * x` (e.g.
`rng_uniform`) into a single-rounded FMA instead of two separately-rounded
IEEE-754 operations -- Python never does this, so it silently produces
1-ulp divergences that only `-ffp-contract=off` prevents. This is exactly
what caught `rng_uniform`/`rng_roll_price` drifting from the Python oracle
in ~2% of `test_rng.c`'s cases before the flag was added (see
`docs/c-port-plan.md` Section 7, and `../simulation/_fastplotmodule.c`'s
header comment for the same requirement on the existing weather/crop-growth
kernel).

### Timing

Both commands report how long the run took. `single` prints
`elapsed_seconds` (wall time around the `runner_run_single` call only --
config loading and printing are excluded, same scope `main.py`'s per-run
timing would cover). `batch` prints an `elapsed` summary line
(`MM:SS elapsed (N.NNNs, R sim/s)`) once every run has completed.

`batch` also draws a self-overwriting progress line on stderr while it
runs -- bar, percent, done/total, sim/s, elapsed, and estimated time left --
mirroring `../runner/progress.py`'s `main.py batch` line, format for format
(see `src/progress.c`). It draws only when stderr is a terminal; `--progress`
and `--no-progress` force it on or off (e.g. for a piped `batch > report.txt`
that should still show progress, or an interactive shell that shouldn't).
Drawing the line never touches `FarmState` or `FarmRng` -- `on_batch_result`
only calls `progress_advance` after `batch_run`'s own callback has already
recorded the result -- so turning it on or off cannot change a batch's
outcome for a given seed, the same boundary `../CLAUDE.md` documents for the
Python reporter. This is a cosmetic/diagnostic feature, not part of the
bit-exact Python contract: its rendering isn't fixture-tested against
Python, only against hand-computed expectations (`tests/test_progress.c`).

```bash
./farm-c batch --runs 1000 --seed 42 --progress      # force the bar on
./farm-c batch --runs 1000 --seed 42 --no-progress    # force it off
```

### Recording run/batch summaries to Mem0

`--mem0`, accepted by both `single` and `batch`, records a one-line free-text
summary to the [Mem0 Platform](https://mem0.ai) after the run/batch
completes normally -- `single` records one memory for the run; `batch`
records one memory per strategy, built from the same `StrategySummary` the
terminal table and warnings already read (`include/aggregate.h`), not one
per individual run.

This is an optional, off-by-default network dependency, kept out of the
default build the same way `simulation/_fastplotmodule.c` and the Cython
build are in the Python tree (see `../CLAUDE.md`):

```bash
make WITH_MEM0=1 farm-c            # links libcurl; default `make farm-c` does not
export MEM0_API_KEY=...            # from https://app.mem0.ai/dashboard/api-keys
./farm-c single --strategy profit_optimizer --seed 42 --mem0
./farm-c batch --runs 1000 --seed 42 --mem0
```

Without `WITH_MEM0=1`, `src/mem0_client.c` compiles to a stub that always
fails with a message saying so, so `--mem0` fails fast (before running
anything, exit code 2) rather than being silently unavailable. The same
fail-fast happens if the binary was built with `WITH_MEM0=1` but
`MEM0_API_KEY` isn't set. A failure recording an individual memory *after*
the run/batch already completed (a network error, a bad key) is reported to
stderr but does not change the run's exit code -- the simulation work it's
summarizing already succeeded by that point.

`src/mem0_client.c`/`include/mem0_client.h` never touch `FarmState` or any
other engine type directly; callers in `main.c` hand it a plain string, the
same read-only-consumer boundary `../CLAUDE.md` draws around the Python
statistical layer. It draws no simulation RNG and runs after
`runner_run_single`/`batch_run` return, so it cannot affect a run's
determinism or replay under `--seed`.

## Verification

Whole-run verification rests on two gates that answer different questions.
Neither is sufficient alone, and CI runs both (`.github/workflows/ci.yml`,
jobs `farm-c` and `farm-c-parity`).

**The committed baseline — does the C still do what it did?**
`tests/golden_baseline.json` records all 11 strategies against four fixed
seeds (the same set `.claude/skills/replay-guard` uses for the Python
package, so a failure is comparable combo-for-combo). Each combo stores 24
end-of-run fields *and* a chained per-day trajectory digest — a blake2b over
every simulated day's full state, so a divergence that cancels out before the
final tally still fails. `make test` checks it; it needs no Python, which is
what makes it usable where there is no interpreter or no live Python tree.
`make golden-capture` re-records it, deliberately a separate target rather
than something `make test` can do for you.

**c-parity — is what the C does still what *Python* does?** The baseline on
its own is circular: captured from an already-drifted C, it would agree with
that drift forever. `python3 ../.claude/skills/c-parity/scripts/c_parity.py
baseline` re-derives every recorded number, digests included, from the live
Python modules. The digest format is a cross-language contract implemented
twice — `src/trajectory.c` builds it from a `FarmState`, the skill's
`_day_payload` from a `PlayerState` — so a mismatch localizes to a day via
`./farm-c golden trace` and then to a line via `./farm-c golden payload` vs
`c_parity.py payload`.

`tests/test_engine.c` covers callback order, same-seed multi-day
repeatability, the step-ordering boundaries no agent decision or RNG draw
would reveal, and allocation-failure cleanup;
`tests/test_config_invalid.c` covers the loader's rejection surface (IO,
malformed JSON, schema, range, reference and argument errors, each asserting
teardown is safe afterwards); `tests/test_reporting.c` pins every balance
threshold on its boundary and the aggregator's undefined-vs-zero rules; and
`tests/test_trajectory.c` covers the digest's own sensitivity (a 1-ulp soil
change, `-0.0` vs `0.0`, list ordering, and the empty-prefix chaining rule)
without needing Python.
Verification of the lower layers remains fixture-based, with the real Python
modules as the oracle:

1. `tools/export_agent_fixtures.py` builds a handful of `PlayerState`
   scenarios in the same directly-constructed style as
   `tests/test_strategy_controls.py`'s `policy_inputs()`, runs all 11 real
   agent classes' full decision surface (`choose_crop`, `should_buy_upgrade`,
   `should_water`, `should_fertilize`, `choose_contracts`,
   `choose_contract_deliveries`, `choose_processing`, `choose_sales`,
   `should_use_fertilizer`) against each scenario, and records the
   config + scenarios + expected outputs as JSON.
2. `tests/test_agents.c` loads that JSON, builds the equivalent
   `FarmState`/`ResolvedConfig`, calls the matching C vtable function, and
   asserts the decision matches -- currently **408 checks, 0 failed**
   (396 fixture cases across 4 scenarios × 11 agents × 9 methods, plus 12
   vtable function-pointer identity checks for the three control agents that
   subclass `ProfitOptimizer` in Python -- see `agent.h`'s header comment).
3. Independently of the fixture round-trip, a one-off spot-check loaded the
   **real** `config/crops.json` (not the synthetic fixture world) into both
   languages and compared day-0 `choose_crop` for six agents directly against
   the real Python classes -- exact match on every one.
4. `src/rng.c` is verified the same way, against `random.Random` as the
   oracle instead of the agents: `tools/export_rng_fixtures.py` seeds a real
   `RandomEvents` for 8 seeds (chosen to cover both the single- and
   two-32-bit-word branches of Python's integer seeding) and records a
   long, fixed-order, 2800-call-per-seed sequence across all 7
   `RandomEvents` operations; `tests/test_rng.c` replays the identical
   sequence through one `FarmRng` seeded the same way and asserts every
   result matches with `==`, not an epsilon -- currently **22400 checks, 0
   failed**. The sequence length deliberately crosses MT19937's 624-word
   regeneration boundary several times per seed, and includes `choice`
   calls over a length-1 sequence to force `_randbelow`'s rejection-sampling
   loop to actually reject and redraw.
5. `src/crop_growth.c`/`src/weather.c` are verified the same way, against
   the real `simulation/crop_growth.py`/`simulation/weather.py` as the
   oracle: `tools/export_physics_fixtures.py` builds a small synthetic crop
   catalog plus hand-picked edge-case scenarios (moisture/pH/temperature on
   both sides of a crop's tolerance band, clamp-saturating accumulated
   stress, fertilized vs. not, same-family rotation penalty vs. mismatch vs.
   no-family, `plot=None`, a 40-seed sweep of `compute_harvest_outcome`, and
   a full `apply_weather` day over fallow/growing/regen/no-regen plots) and
   records every input/output as `float.hex()`; `tests/test_physics.c`
   replays each case and asserts `==` -- currently **542 checks, 0 failed**.
   `compute_harvest_outcome`'s cases are RNG-adjacent: each uses a fresh
   seed, and the C side seeds a fresh `FarmRng` with the same seed rather
   than replaying raw draws, relying on Phase 0's already-proven bit-exact
   RNG. `py_round_ndigits` (weather.py's `round(x, ndigits)`) was
   additionally stress-tested standalone against 200,000 random values
   across `round(x, 2)`/`round(x, 3)` with 0 mismatches, beyond what the
   fixture set alone happens to exercise.
6. `src/actions.c`/`src/inventory.c`/`src/markets.c`/`src/processing.c`/
   `src/contracts.c`'s Phase 2 mutators are verified the same way, against
   the real Python modules as the oracle, but with a broader fixture shape
   than Phases 0-1 use: `tools/export_mutation_fixtures.py` builds one
   shared synthetic world (2 crops, a product, a recipe, 2 upgrades, 2
   channels, 2 buyers) and 51 scenarios across the 22 ported functions
   (success and rejection paths, FEFO tie-breaks, expired/insufficient/
   over-capacity cases, contract accept/deliver/expiry, ...), and for each
   one records a *full snapshot* of every mutable `PlayerState` field
   before and after calling the real function -- not just that function's
   own documented return value -- so `tests/test_mutation.c` catches an
   incidental extra mutation the C port gets wrong, not only the ones a
   docstring happened to think to check. Currently **4552 checks, 0
   failed**. Two real bugs were caught and fixed this way before the suite
   went green: `channel.get("daily_capacity", quantity)` in `sell` treats
   an explicit `None` differently from an absent key (a Python gotcha, not
   a port bug -- fixed in the fixture, not the C), and the test harness's
   own `scatter_double` helper was reading `cJSON`'s `valuedouble` on
   `float.hex()`-encoded *string* nodes instead of parsing `valuestring`
   with `strtod` (a fixture-loader bug, not a `src/` one -- caught because
   the snapshot diff surfaced a stale `market_supply`/`buyer_relationships`
   read that a return-value-only check would have missed entirely).

## Known simplifications / follow-ups

- `RandomAgent`'s repr-formatting helper (`src/rng_hash.c`) implements
  Python's string-repr quoting algorithm in full, but is not a general
  `repr()` -- it only needs to (and only claims to) reproduce the tuple
  shapes `agents/random_agent.py`'s four call sites actually pass.
- `py_round_ndigits` (`src/pyfloat.c`) relies on the host libc's `%f`
  formatting and `strtod` both being correctly-rounded, rather than porting
  CPython's dtoa outright. Verified on this repo's development platform
  (see Verification above); has not been checked against a libc that isn't
  correctly-rounded for decimal conversion, which would be a real
  platform-portability gap if this port is ever built somewhere else.
