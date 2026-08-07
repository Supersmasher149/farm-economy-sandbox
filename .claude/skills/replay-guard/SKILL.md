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

# Bisect a failure to the first simulated day that diverged.
python3 .claude/skills/replay-guard/scripts/golden_replay.py trace <strategy> <seed>
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

## What it compares, and why that shape

Every strategy in `AGENT_REGISTRY` against a small fixed seed set, on ~30
end-of-run `PlayerState` fields **plus** a per-day trajectory digest.

Two properties are load-bearing and easy to break by "simplifying":

- **Floats are recorded as `float.hex()`, never rounded.** The earlier version
  stored `round(money, 6)`, which on a five-figure money value tolerates
  roughly 275,000 ulps — it would pass a build whose arithmetic had already
  drifted, right up until the drift crossed an affordability `>=` or a
  quality-grade threshold and flipped a discrete outcome. Hex also
  distinguishes `+0.0` from `-0.0`, which `==` does not.
- **The `trajectory` digest covers every simulated day, not just the final
  tally.** A run can end on the same money having taken a different route.
  Injecting one ulp of soil-moisture drift moves the trajectory digest on all
  44 combos while leaving every end-of-run field identical — so without it the
  guard reports PASS on a genuinely divergent build.

Baselines are portable across CPython 3.12–3.14 (verified). They are **not**
portable to 3.11, which lacks compensated summation in `sum()` and moves one
combo; that is why `pyproject.toml` sets `requires-python = ">=3.12"`.

Capture from the **pure-Python reference**, i.e. with the optional C kernel
removed (`python3 tools/build_fastplot.py --clean`), then rebuild and `check`
to assert the C reproduces it bit for bit. `capture` warns if you do it the
other way round, and records the interpreter, platform and whether `_fastplot`
was active in the baseline's `_meta` block.

## Files

- `scripts/golden_replay.py` — `capture` / `check` / `trace` subcommands.
- `golden_baseline.json` — committed baseline data (created by `capture`,
  read by `check`). Commit this file; it's meant to be reviewed like any
  other test fixture.
