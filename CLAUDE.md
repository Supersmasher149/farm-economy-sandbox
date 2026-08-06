# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A headless, deterministic farm-economy simulator used for balance testing, not
a playable game. Scripted agents each probe one deliberate strategy; a batch
runner plays every strategy thousands of times to surface how the economy
behaves in aggregate (dominant crops, exploitable upgrades, bankruptcy traps).
Every run is seeded and exactly reproducible from that seed.

Python 3.11+, no third-party dependencies to run the simulator. `pytest` for
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

# Full test suite
python3 -m pytest

# Single test file / single test
python3 -m pytest tests/test_engine.py
python3 -m pytest tests/test_engine.py::test_same_seed_produces_identical_results
```

`batch` writes `reports/run_results.csv`, `reports/config_snapshot.json`, and
`reports/summary_report.md` (per-strategy stats, cash-flow diagnostics,
economics audit, automated balance warnings). Pass `--seed` to make an entire
batch — including every agent's per-run seeds — reproducible, useful for
A/B-testing a config or code change under identical simulated conditions.
Report artifacts are published atomically (staged in a temp dir, then
swapped in together, restoring the previous files if anything fails
partway — see `_publish_report_artifacts` in `main.py`).

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
`docs/superpowers/specs/2026-08-04-full-crop-market-simulation-design.md`;
later dated specs in the same directory record subsequent design decisions
(soil/regen fixes, fertilizer atomicity, issue-board bug fixes, strategy
control agents) — check there before assuming a behavior is accidental.

**Determinism is load-bearing.** `simulation/random_events.py:RandomEvents`
wraps a single `random.Random(seed)` and is the only source of randomness
threaded through a run (weather, price variation, yields, contract offers,
...). The same seed must always reproduce the same day-by-day outcome —
`main.py replay` and `tests/test_engine.py::test_same_seed_produces_identical_results`
exist specifically to check this. Any change to simulation logic that
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
