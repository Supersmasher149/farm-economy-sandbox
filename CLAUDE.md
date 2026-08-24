# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A headless, deterministic farm-economy simulator used for balance testing, not
a playable game. Scripted agents each probe one deliberate strategy; a batch
runner plays every strategy thousands of times to surface how the economy
behaves in aggregate (dominant crops, exploitable upgrades, bankruptcy traps).
Every run is seeded and exactly reproducible from that seed.

Python 3.12+ (3.11 changes simulation results — see Performance), no
third-party dependencies to run the simulator. `pytest` for
tests.

## Commands

```bash
# Play one strategy once, with full daily history
python3 main.py single --strategy profit_optimizer --seed 42 --verbose

# Reproduce a specific recorded run exactly
python3 main.py replay --strategy fast_seller --seed 123456789

# Run every strategy N times each and generate a report in reports/
python3 main.py batch --runs 1000

# Diagnostic scenario without editing config files
python3 main.py batch --runs 100 --days 30 --start-money 300

# batch defaults to a process pool (one worker per core); force sequential
python3 main.py batch --runs 1000 --workers 1

# tune worker count (default is os.cpu_count(); oversubscribing does not
# pay on large batches -- see Performance below)
python3 main.py batch --runs 1000 --workers 14

# batch draws a progress line (bar, %, done/total, sim/s, elapsed, ETA) on
# stderr when stderr is a terminal; force it on or off
python3 main.py batch --runs 1000 --progress
python3 main.py batch --runs 1000 --no-progress

# batch also renders reports/dashboard.html (needs matplotlib, see
# requirements-viz.txt) by default; skip it for a faster/dependency-free run
python3 main.py batch --runs 1000 --no-charts

# Quick strategy-comparison table for the latest batch, in the terminal --
# no third-party deps, reads reports/summary.json
python3 main.py view
python3 main.py view --sort bankruptcy_rate --top 5
python3 main.py view --diff latest-1 latest    # what changed since the last batch
python3 main.py view --list                    # published runs available to reference
python3 main.py view --intervals                # confidence intervals per estimand
python3 main.py view --convergence --estimand bankruptcy_probability

# Statistical layer: intervals ride on every batch; these tune them
python3 main.py batch --runs 1000 --confidence 0.99
python3 main.py batch --runs 1000 --sampling-plan paired --baseline profit_optimizer
python3 main.py batch --runs 1000 --correction bonferroni --bootstrap-replications 4000
python3 main.py batch --runs 1000 --no-distributions --no-comparisons   # skip the extra passes

# Adaptive sampling: stop on precision instead of a fixed run count
python3 main.py batch --min-runs 200 --max-runs 5000 --checkpoint-runs 200 \
    --stop-estimand expected_final_money --target-relative-half-width 0.02
python3 main.py batch --min-runs 500 --max-runs 20000 --checkpoint-runs 500 \
    --bankruptcy-half-width 0.01 --min-bankruptcies 30 --alpha-spending obrien_fleming

# Re-analyze a published run or any CSV -- including one produced by farm-c
python3 main.py analyze --run latest
python3 main.py analyze --csv farm-c/reports/run_results.csv

# Propagate declared parameter uncertainty through the simulator
python3 main.py uncertainty --spec experiments/specs/example-uncertainty.json \
    --method oat --replicates 50
python3 main.py uncertainty --spec experiments/specs/example-uncertainty.json \
    --method sobol --samples 64 --replicates 20    # N(k+2) configurations

# Full test suite
python3 -m pytest

# Single test file / single test
python3 -m pytest tests/test_engine.py
python3 -m pytest tests/test_engine.py::test_same_seed_produces_identical_results

# Lint (ruff) -- config lives in pyproject.toml
ruff check
ruff format --check

# Optional C accelerator (~1.15x); the simulator runs fine without it
python3 tools/build_fastplot.py
python3 tools/build_fastplot.py --clean

# Optional Cython build of simulation/ + agents/ (~1.17x on top of the above).
# Needs `pip install cython`; ignored entirely unless FARM_COMPILED is set.
python3 tools/build_cython.py
FARM_COMPILED=1 python3 main.py batch --runs 1000
FARM_COMPILED=strict python3 -m pytest   # stale/missing artifact = error, not fallback
python3 tools/build_cython.py --clean

# Where time actually goes (statistical sampler, not cProfile -- see below)
python3 tools/sample_profile.py --runs 200

# Search config/*.json's numeric knobs for changes that reduce balance
# warnings (coordinate hill-climbing, using the simulator itself as the
# fitness function). Local and offline -- no network calls, no LLM.
# Propose-only: never writes config/*.json; reports ranked candidate diffs
# to reports/auto_balance/ for a human to apply by hand and re-check via the
# balance-testing workflow below.
python3 tools/auto_balance.py --iterations 40 --seed 42
```

`batch` writes `reports/run_results.csv`, `reports/config_snapshot.json`,
`reports/summary_report.md` (per-strategy stats, cash-flow diagnostics,
economics audit, automated balance warnings), `reports/summary.json` (the
same per-strategy stats, machine-readable — what `main.py view` reads, so
there is exactly one source of truth for a number's value independent of how
it gets displayed), and `reports/dashboard.html` (every chart from
`metrics.visualize`, bundled into one self-contained page via
`metrics/dashboard.py`; a short placeholder page if matplotlib isn't
installed or `--no-charts` was passed, so the artifact always exists and is
never a broken symlink). It also writes four analysis artifacts —
`reports/distributions.json` (exact quantiles/histograms/ECDF/tails/survival
from the CSV), `reports/comparisons.json` (every strategy pair, corrected for
multiplicity), `reports/convergence.json` (the checkpoint history behind the
estimates) and `reports/analysis_metadata.json` (seeds, plan, methods, git
provenance). An analysis that was switched off writes
`{"skipped": true, "reason": ...}` rather than nothing, so no published
symlink ever dangles. Pass `--seed` to make an entire batch — including
every agent's per-run seeds — reproducible, useful for A/B-testing a config
or code change under identical simulated conditions.
Report artifacts are published atomically as a **set**, via a single pointer
switch (`_publish_report_artifacts` in `main.py`): a batch stages into
`reports/.batch-*/`, that directory is renamed to `reports/runs/<id>/` (one
atomic rename), and `reports/latest` is repointed at it (one atomic symlink
swap) under an inter-process lock. The nine paths above (`ARTIFACT_NAMES` in
`main.py`) are stable symlinks through `latest`, so that one swap moves all
nine together — a reader can never pair a CSV from one batch with a summary
from another. Adding an artifact means adding it to `ARTIFACT_NAMES` *and*
writing it (or its skip stub) into the staging directory; a name in the list
with no file breaks publication for every artifact, not just that one.
Published run directories are immutable and the last `RUNS_RETAINED` are
kept, so resolving `reports/latest` once gives a consistent snapshot even
while a later batch publishes, and `main.py view --run <id>` /
`--diff <a> <b>` can reference any of the retained ones by id or by
`latest-N`. Three independent `os.replace` calls, which this replaced, could
not offer that.

`metrics/view.py` is the stdlib-only counterpart to `summary_report.md`: it
reads `summary.json` (never the CSV or the markdown — one source of truth)
and renders a sortable/filterable table or a before/after diff between two
published runs, entirely in the terminal. It has no third-party
dependencies, matching the rest of the simulator; `metrics/dashboard.py`
(the chart bundling) is the one piece of the reporting path that still needs
matplotlib, same as `metrics/visualize.py` always has.

`runner/progress.py` renders the batch progress line. It wraps the
`run_batch` result stream as a lazy pass-through (counting only, stderr
only), which is what keeps progress reporting outside the determinism and
bounded-memory guarantees the batch runner makes — keep it that way.

## Performance

**The profile is flat — there is no hotspot to fix.** Measured with
`tools/sample_profile.py`: the hottest single function is ~10% of self time.
Without the C accelerator the runtime splits roughly 45% numeric kernel / 44%
engine glue / 6% agent decision logic; *with* it built the split is 55% engine
glue / 32% numeric kernel / 8% agents, and the whole fused kernel is down to
10.1% of runtime. Report generation is irrelevant (~0.05% of a batch).

**What that leaves.** Since the kernel is 10% of runtime, making it infinitely
fast is a 1.11x ceiling — hand-written C is close to exhausted here. The only
bucket with room is the 55% engine glue, which is dict/allocation churn rather
than arithmetic, so it needs a whole-module compiler rather than more `.c`.
Measured bound on that: 161 Python-level calls per sim-day against a 41,833
ns/sim-day budget (259 ns of work per call), so removing call overhead
entirely is worth roughly 1.06–1.18x. Options already measured and rejected:
CPython 3.14's JIT (`PYTHON_JIT=1`) is **5% slower** here; `gc.disable()` /
`gc.freeze()` is noise; upgrading the interpreter is already banked (3.11 →
3.14 was 1.16x).

**The Cython build (`tools/build_cython.py`) is what took that bucket**, at
**1.17x** on a 1000-run batch, interleaved min-of-5 against a pristine tree
with `_fastplot` built on both sides. Scope matters: five glue modules alone
measured only 1.122x, the whole `simulation/` package 1.157x, and
`simulation/` + `agents/` 1.174x. Zero source changes; the `.py` files stay
the reference. Two things make it safe and neither is optional — the Cython
directives (with `annotation_typing` left at its `True` default it silently
coerces this codebase's `float` annotations to C doubles) and the opt-in,
out-of-tree loading in `simulation/_compiled.py`. Both the directives and the
float-critical compiler flags are **defined in `simulation/_compiled.py`**,
hashed into the manifest as a build recipe, and recomputed at load time, so an
artifact built with different ones is rejected rather than trusted — put new
build knobs there, not in `tools/`, or they will not be verified.
**Never build Cython output in place**: an extension module beside its own
`.py` shadows it silently, edits stop taking effect, and it defeats the
manifest's staleness check. `_compiled.in_tree_artifacts()` warns about this
at import and `tests/test_compiled_shim.py` fails on it.

Both accelerator builds are **transactional** — stage, verify by loading the
result in a subprocess, then swap — so an interrupted build cannot leave a
half-written artifact where the previous working one was. `FARM_COMPILED_DIR`
exists for that pre-publish verification step (it points the loader at a
staged directory); production leaves it unset.

**Worker count: leave the default alone.** `batch` defaults to
`os.cpu_count()` (`runner/batch_run.py`). Oversubscribing past the core count
looks like a free win on a mid-size batch and isn't: measured on a 6P+2E
machine, 14 workers beat 8 by 1.05x at `--runs 300` but only 1.02x at 100 and
1.007x at 1000. The straggler effect it exploits amortizes away once each
worker has enough runs queued, so it evaporates at exactly the batch sizes
worth optimizing. Output is byte-identical at every worker count —
`batch_run.py` mints per-run seeds single-threaded before dispatch precisely
so that holds — so it is safe to tune, just not worth it.

Two measurement traps, both hit before:

- **`cProfile` inflates this codebase ~4x and unevenly**, because a batch
  makes ~85M calls; functions with many cheap calls look far hotter than
  they are. Use `tools/sample_profile.py` (statistical, semantics-preserving).
- **Self time in a leaf function is mostly its own arithmetic, not call
  overhead.** Inlining it *relocates* the cost rather than removing it —
  inlining `crop_growth._clamp` moved ~3.8% of self time straight into
  `harvest_multipliers` for no net gain. Only changes that *eliminate work*
  pay. Quote before/after wall clock against a pristine `git worktree`, never
  a profiler self-time delta.

`simulation/_fastplotmodule.c` is an **optional** compiled kernel for the
per-plot daily physics (`weather.apply_weather`'s loop fused with
`crop_growth.update_crop_stress`), worth ~1.15x. It is not built by default
and is not required: `simulation/weather.py` falls back to the pure-Python
loop, which stays the reference implementation. That loop is what
`tests/test_fastplot_equivalence.py` compares the C against, by `float.hex()`
rather than `==` (`0.0 == -0.0` is True, so `==` cannot see the signed-zero
difference the literal `max`/`min` forms exist to preserve) — if the two
disagree, the C is wrong. Anything touching
either implementation must keep them in lockstep, must preserve the hand-
rolled Neumaier summation and literal `max`/`min` forms the header comment
explains, and must be compiled with `-ffp-contract=off`. Rebuild and re-run
the replay guard after editing it; a stale `.so` is rejected automatically
via the `PROFILE_LAYOUT` constant rather than silently misreading fields.

## Architecture

**Strict separation: agents decide, the engine mutates.** Agents
(`agents/*.py`, subclassing `agents/base.py:Agent`) never touch `PlayerState`
directly — they only answer decision questions (`choose_crop`,
`should_buy_upgrade`, `should_water`, `should_fertilize`, `choose_contracts`,
`choose_contract_deliveries`, `choose_processing`, `choose_sales`,
`should_use_fertilizer`). `simulation/engine.py:run_day` is the only code
that applies those decisions to state, via `simulation/actions.py`,
`simulation/contracts.py`, `simulation/processing.py`, and
`simulation/markets.py`. When adding agent behavior, add a decision method to
`Agent` and read it from the engine — don't reach into state from an agent.

**Fixed daily order** (`simulation/engine.py:run_day`): weather generates and
applies stress → storage liability captured → mature crops harvest → storage
ages/spoils → processing jobs complete → market prices update → contract
offers generate → agent accepts contracts/deliveries → agent starts
processing jobs → agent sells → agent buys upgrades → agent
waters/fertilizes planted crops → open slots get planted → expired contracts
resolve → storage liability collected → day finishes (bankruptcy check).
Changing this order changes simulated outcomes for every existing recorded
seed — treat it as a breaking change requiring explicit justification, not a
casual refactor. The full design rationale is in
`docs/design/2026-08-04-full-crop-market-simulation-design.md`;
later dated specs in the same directory record subsequent design decisions
(soil/regen fixes, fertilizer atomicity, issue-board bug fixes, strategy
control agents) — check there before assuming a behavior is accidental.

**Determinism is load-bearing.** `simulation/random_events.py:RandomEvents`
wraps a single `random.Random(seed)` and is the only source of randomness
threaded through a run (weather, price variation, yields, contract offers,
...). The same seed must always reproduce the same day-by-day outcome —
`main.py replay` and `tests/test_engine.py::test_same_seed_produces_identical_results`
exist specifically to check this, and the `replay-guard` skill is the real
gate: a committed bit-exact baseline (hex floats, plus a per-day trajectory
digest) across every strategy and four seeds. Run
`python3 .claude/skills/replay-guard/scripts/golden_replay.py check` after
touching anything in `simulation/`; it takes under a second. Any change to simulation logic that
consumes randomness (or changes call order relative to `rng`) changes replay
output for old seeds; if you touch anything in `simulation/`, run the full
test suite and reason about whether existing recorded seeds still mean the
same thing before considering the change complete. Batch runs in parallel
workers must also stay byte-for-byte identical to a sequential run for the
same `--seed` (`runner/batch_run.py` mints per-run seeds single-threaded and
sequentially before dispatching to the pool, specifically to preserve this).

**Config is data, not code.** All tunable game data — crops, upgrades,
fertilizer, watering, soil, weather, markets, buyers/contracts, storage,
processing (`config/*.json`) — is validated at load time
(`simulation/configuration.py:validate` / `validate_simulation_config`).
Rebalancing the economy means editing that JSON, not the simulation code.
`simulation/derived.py` builds indexes (items/recipes/channels by id,
effective storage/processing capacity given owned upgrades) once per
`(world, crops)` pair rather than per simulated day — keep new derived
lookups there rather than recomputing per-day.

**The agent roster is a set of balance probes, not competitors.** Each
agent's docstring states the specific question it exists to answer (e.g.
`fast_seller` tests whether rapid reinvestment in the shortest-growth crop
dominates; `risk_averse_grower` tests whether cautious play is unfairly
punished). If an agent's behavior contradicts its own docstring, that's a
bug in the agent, not a modeling choice — see the README's "Balance-testing
workflow" section for a worked example. New strategies get registered in
`AGENT_REGISTRY` in `main.py`.

`metrics/warnings.py` turns batch results into automated flags (dominant/dead
crops, bankruptcy rates above threshold, rarely-reached upgrades) so a
balance regression shows up in `summary_report.md` without manually
eyeballing every strategy's numbers.

## Statistical analysis layer

Added in `docs/design/2026-08-21-statistical-analysis-framework-plan.md`;
delivery status and what was deliberately left out are recorded in
`docs/design/2026-08-22-statistical-analysis-validation-status.md`. The layer
wraps the simulator and must never reach into it.

**The analysis consumes no simulation RNG draws.** Bootstrap resampling,
adaptive stopping and uncertainty sampling all use their own generators,
seeded via `derive_analysis_seed` (`metrics/inference.py`), which is
`blake2b` over the base seed and a label — never `hash()`, which is
`PYTHONHASHSEED`-randomized and would make a bootstrap interval
irreproducible across processes. `tests/test_analysis_integration.py`
falsifies the claim directly: it runs a batch, does the whole analysis, runs
the identical batch again, and asserts the results are identical.

**One place computes an interval:** `metrics/inference.py`. Student-t for
means, Wilson/Clopper-Pearson for proportions, deterministic percentile
bootstrap for quantiles and non-analytic differences. `metrics/estimands.py`
is the registry that says what is being estimated (unit of analysis, cohort,
extraction, missing-value policy, whether it supports adaptive stopping);
add an estimand there, not in a report renderer.

**Published numbers are quantized to `PUBLISHED_PRECISION_DIGITS` (12).**
Welford's M2 is order-dependent in the last ULP, so an unquantized standard
deviation differs between a 1-worker and an 8-worker batch. Live
accumulators keep full precision — the quantization is at `Estimate.to_dict`
only, so stopping rules are not affected by it.

**`runner/sampling_plan.py` versions the seed schedule, and
`legacy-mt19937-v1` is frozen.** It reproduces
`random.Random(base_seed).randrange(2**32)` agent-major, which is what every
recorded seed, `replay-guard` and `farm-c`'s parity harness depend on;
`runner/batch_run.py` uses it whenever no plan is named, so the default path
is bit-identical to what it always was. `independent-hashed-v1` addresses a
seed by `(strategy, replicate)` rather than by position — that is what makes
adaptive blocks extendable and roster-invariant, and it is required for
adaptive mode (which rejects the legacy plan rather than silently
renumbering). `shared-initial-seed-v1` shares one seed per replicate across
strategies for paired comparison; its own `describe()` records
`pairing_strength: "weak"`, because agents diverge and then consume the
shared stream differently. **It is not a default win** — measured at 400
replicates, median correlation across pairs is 0.069 and the paired
difference interval is a median 1.006x the independent one, i.e. slightly
*wider*. Only agents that behave like the baseline benefit (a
`ProfitOptimizer` subclass that overrides little: ~18-20%). Numbers, and why
real CRN would need Section 7 stages 4-6 (not implemented, because they move
`RandomEvents` and break both replay baselines and farm-c parity), are in
`docs/design/2026-08-22-statistical-analysis-validation-status.md`.

**Adaptive stopping evaluates only on complete blocks** (`runner/adaptive.py`),
at a checkpoint schedule declared before the first run, with Lan-DeMets alpha
spending across looks. Peeking at a partial block would make the realized run
count depend on worker timing, and the whole layer's worker-count invariance
with it.

**`experiments/` holds config uncertainty, and it stays out of `config/`.**
A `farm-uncertainty-v1` spec declares distributions over config *paths*;
sampled configurations are deep-copied and pushed through the real
`validate_simulation_config` before running, so a sampler can never smuggle
an invalid economy past the loader. Only the Monte Carlo design honours
declared correlation groups — OAT, LHS, Morris and Sobol reject a correlated
spec rather than quietly ignoring the correlation. `sobol_design` builds
`AB_i` as *A with column i from B*; the mirrored convention reproduces
neither S1 nor ST, which is what the Ishigami benchmark in
`tests/test_uncertainty.py` exists to catch.

**Python and C boundary.** `farm-c` stays a deterministic raw-data producer;
statistics are not duplicated there. `metrics/distributions.load_observations`
tolerates farm-c's CSV column set (0/1 bankruptcy flags, `lowest_money` for
`minimum_cash_balance`, a recomputed `avg_profit_per_day`), so
`python3 main.py analyze --csv farm-c/reports/run_results.csv` runs the same
inference over C output.

## Balance-testing workflow

1. `python3 main.py batch --runs 1000 --seed <fixed-seed>`
2. Read `reports/summary_report.md`, starting with `## Warnings` — or
   `python3 main.py view` for the same warnings plus a sortable table,
   without scrolling a ~500-line report.
3. Adjust `config/*.json` (or fix the agent, if a warning traces back to its
   decision logic contradicting its own documented behavior).
4. Re-run with the **same seed** to isolate the effect of the change from
   run-to-run noise — `python3 main.py view --diff latest-1 latest` shows
   exactly what moved, per strategy per field, sorted by size of change —
   then drop `--seed` for the final report.

`tools/auto_balance.py` automates the search step of this workflow (step 3)
by hill-climbing over config knobs locally, but keeps the same human-in-
the-loop boundary as `.claude/skills/balance-check`: it never edits
`config/*.json` itself, only proposes ranked diffs for a human to apply.
