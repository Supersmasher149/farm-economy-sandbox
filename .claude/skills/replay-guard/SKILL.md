---
name: replay-guard
description: Golden-replay determinism check for farm-economy-sandbox. Use before/after touching anything under simulation/, or whenever asked to verify determinism, replay safety, or "did this change replay output" for a recorded seed. Runs every registered strategy against a fixed seed set and diffs key outcome stats against a committed baseline.
---

# replay-guard

CLAUDE.md states determinism is load-bearing: "Any change that consumes
randomness (or changes call order relative to `rng`) changes replay output
for old seeds; if you touch anything in `simulation/`, run the full test
suite and reason about whether existing recorded seeds still mean the same
thing." `pytest` only checks two hardcoded seeds
(`test_same_seed_produces_identical_results` in `tests/test_engine.py`).
This skill is the "golden harness" that check calls for: a wider,
committed baseline across every strategy and several seeds, so a
determinism break shows up as a diff instead of a judgment call.

This skill never edits simulation code or config itself — it only runs the
existing `run_single` entry point (the same one `main.py replay` uses) and
compares outputs. Treating a diff as "expected" or "unexpected" is the
human/agent's call, not the script's.

## When to use it

- Before starting a change anywhere under `simulation/` (to make sure a
  baseline exists and is current).
- After finishing that change, before calling it done.
- Any time asked to "verify replay safety" or similar.

## Usage

```bash
# Establish/refresh the committed baseline (run this on a repo state you
# trust — e.g. right after `main` passes tests, or right after landing an
# *intentional* behavior change you want the new baseline to reflect).
python3 .claude/skills/replay-guard/scripts/golden_replay.py capture

# Check the current working tree against the committed baseline.
python3 .claude/skills/replay-guard/scripts/golden_replay.py check
```

`check` exits non-zero and prints a per-(strategy, seed) diff if anything
differs. Interpret the result:

- **No changes were intended** (e.g. a refactor, a report-formatting change,
  an agent behavior change that doesn't touch `simulation/`) → any FAIL is a
  real determinism bug. Fix it, don't touch the baseline.
- **A change to `simulation/` intentionally alters outcomes** (a balance fix,
  a new mechanic) → confirm the diffs look like the intended effect, then
  re-run `capture` and commit `golden_baseline.json` alongside the code
  change in the same commit/PR, so reviewers see the before/after delta.

The seed set and metrics mirror what
`tests/test_engine.py::test_same_seed_produces_identical_results` already
asserts on (`money`, `total_revenue`, `crop_plant_counts`, `bankrupt`,
`total_crops_lost`), just spread across every strategy in `AGENT_REGISTRY`
and a small fixed set of seeds instead of one.

## Files

- `scripts/golden_replay.py` — `capture` / `check` subcommands.
- `golden_baseline.json` — committed baseline data (created by `capture`,
  read by `check`). Commit this file; it's meant to be reviewed like any
  other test fixture.
