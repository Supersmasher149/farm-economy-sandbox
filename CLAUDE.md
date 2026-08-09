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

`batch` writes `reports/run_results.csv`, `reports/config_snapshot.json`, and
`reports/summary_report.md` (per-strategy stats, cash-flow diagnostics,
economics audit, automated balance warnings). Pass `--seed` to make an entire
batch — including every agent's per-run seeds — reproducible, useful for
A/B-testing a config or code change under identical simulated conditions.
Report artifacts are published atomically (staged in a temp dir, then
swapped in together, restoring the previous files if anything fails
partway — see `_publish_report_artifacts` in `main.py`).

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
directives in that script's docstring (with `annotation_typing` left at its
`True` default it silently coerces this codebase's `float` annotations to C
doubles) and the opt-in, out-of-tree loading in `simulation/_compiled.py`.
**Never build Cython output in place**: an extension module beside its own
`.py` shadows it silently, edits stop taking effect, and it defeats the
manifest's staleness check. `_compiled.in_tree_artifacts()` warns about this
at import and `tests/test_compiled_shim.py` fails on it.

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

## Balance-testing workflow

1. `python3 main.py batch --runs 1000 --seed <fixed-seed>`
2. Read `reports/summary_report.md`, starting with `## Warnings`.
3. Adjust `config/*.json` (or fix the agent, if a warning traces back to its
   decision logic contradicting its own documented behavior).
4. Re-run with the **same seed** to isolate the effect of the change from
   run-to-run noise, then drop `--seed` for the final report.

`tools/auto_balance.py` automates the search step of this workflow (step 3)
by hill-climbing over config knobs locally, but keeps the same human-in-
the-loop boundary as `.claude/skills/balance-check`: it never edits
`config/*.json` itself, only proposes ranked diffs for a human to apply.
