---
name: c-parity
description: Cross-language parity check for the farm-c port - runs the same minted seeds through farm-c and the Python simulator and diffs every run bit-for-bit, and verifies farm-c's committed golden baseline (including per-day trajectory digests) against live Python. Use before/after touching anything under farm-c/src or farm-c/include, when asked whether the C port still matches Python, after re-capturing the C golden baseline, to verify seed minting, or to localize a known C-vs-Python divergence to its first simulated day.
---

# c-parity

`farm-c/` targets the *bit-compatible* column of `docs/c-port-plan.md`
Section 7: same seeds, same RNG stream, same floating-point operations, same
trajectory. Its own test suite verifies that **per layer** — fixtures replay
recorded Python output for the RNG, the physics, the mutators — but nothing
verified the composition end to end. `farm-c/README.md` says the whole-run
cross-check was done *by hand* for `fast_seller` at base seed 42 during
development. This skill is that check, automated and widened.

It is the farm-c analogue of `replay-guard`, and it fills a gap that skill
cannot reach: `replay-guard` pins `simulation/` against its own committed
baseline, so it says nothing about whether the C reproduces it.

farm-c now has a committed baseline of its own
(`farm-c/tests/golden_baseline.json`, checked by `make test` with no Python
involved). That baseline and this skill are two halves of one gate, and the
half this skill owns is the one that stops the other from going circular: a
baseline captured from an already-drifted C would be internally consistent
forever after. `baseline` re-derives every recorded number — the chained
per-day trajectory digest included — from the live Python modules.

Like the other two skills here, this one only **runs and reports** — it never
edits `farm-c/src`, `simulation/`, or `config/`. Deciding whether a diff is a
port bug or an intended difference is the human/agent's call.

## When to use it

- Before starting a change under `farm-c/src/` or `farm-c/include/` (confirm
  the tree is parity-clean first, so a pre-existing diff isn't blamed on you).
- After finishing that change, before calling it done.
- After any change to `simulation/`, `agents/`, or `config/` — the C port is
  a mirror, and moving the reference moves what the mirror must match.
- When asked to verify seed minting, "does the C still match Python", or to
  chase a known divergence.

## Usage

```bash
# Build the binary first (from farm-c/); the harness will not build for you.
cd farm-c && make farm-c && cd ..

# Full roster, 5 runs per strategy (55 runs, ~1.5s)
python3 .claude/skills/c-parity/scripts/c_parity.py check

# Wider, or narrowed to one strategy while chasing something
python3 .claude/skills/c-parity/scripts/c_parity.py check --runs 50 --seed 42
python3 .claude/skills/c-parity/scripts/c_parity.py check --strategy progression_player --runs 20

# Diagnostic horizons -- applied to BOTH sides
python3 .claude/skills/c-parity/scripts/c_parity.py check --days 30 --start-money 300

# Seed minting only: no Python simulations, just the mint order
python3 .claude/skills/c-parity/scripts/c_parity.py seeds --runs 5

# Localize a failing pair to its first divergent day
python3 .claude/skills/c-parity/scripts/c_parity.py trace progression_player 127978094

# Verify farm-c's committed golden baseline against Python (trajectory
# digests included). Run this after any `make golden-capture`.
python3 .claude/skills/c-parity/scripts/c_parity.py baseline

# Print Python's exact hashed bytes for one day, to diff against
# `./farm-c golden payload STRATEGY SEED --day N`
python3 .claude/skills/c-parity/scripts/c_parity.py payload fast_seller 1 --day 17
```

`check` and `baseline` exit non-zero on any mismatch and print the offending
fields with both sides in hex.

## `check` vs `baseline`

Both compare the C against Python; they differ in *what* and *how much*.

| | `check` | `baseline` |
|---|---|---|
| Seeds | minted from `--seed`, any number of runs | the four fixed golden seeds |
| C side | a live `farm-c batch`, read from its CSV | the committed `golden_baseline.json` |
| Compares | 20 end-of-run scalars | 24 fields **plus the per-day trajectory digest** |
| Catches | seed minting, final-tally drift | the above, plus a divergence that cancels out before the last day |

Use `check` for breadth while developing (it scales to any seed count) and
`baseline` whenever the committed file is or might be stale. A run that passes
`check` can still fail `baseline`: identical final money reached by a
different route is exactly what the trajectory digest exists to catch.

**If only `trajectory` differs**, the two agree on every end-of-run number but
took different daily routes. Bisect it:

```bash
cd farm-c && ./farm-c golden trace fast_seller 1     # first differing day, say 17
./farm-c golden payload fast_seller 1 --day 17 > /tmp/c.txt
cd .. && python3 .claude/skills/c-parity/scripts/c_parity.py payload fast_seller 1 --day 17 > /tmp/py.txt
diff /tmp/c.txt /tmp/py.txt
```

If the payloads are identical on *every* day and only the digest differs, the
bug is in the chaining, not the simulation — the payload format is a
cross-language contract shared by `farm-c/src/trajectory.c` and this script's
`_day_payload`, and both must also chain from the same starting prefix.

## Interpreting a result

- **`PARITY: OK`** — every compared field on every run is bit-identical.
- **A field diff on many runs, same field every time** → almost always a
  single systematic port bug, not drift. Read the field name first; the
  narrowest failure is usually the most diagnostic.
- **One run diverging on nearly every field** → a trajectory split. Run
  `trace` on it: identical days followed by one divergent day localizes the
  decision or physics call that parted company, which is far cheaper to chase
  than a final-tally diff.
- **`SEED MINTING: FAIL`** → `src/rng.c:rng_randrange_2_32` or `src/batch.c`'s
  traversal order has drifted from `runner/batch_run.py`. The per-run
  comparison still runs and is still valid (seeds are read from the C's own
  output, not re-derived), but every run is now a different pairing than a
  Python batch would produce, so fix this before reading anything else.
- **Changed `simulation/` deliberately?** Then a C diff is expected until the
  port is updated to match. Confirm the diffs look like the intended effect
  and say so — don't silently narrow the comparison to make it green.

## What it compares, and why that shape

The 20 non-key columns of `farm-c`'s batch CSV (`main.c:write_csv_row`) —
the scalar subset of `metrics/run_results.py`'s `RunResult`. `BatchRunResult`
deliberately drops the crop-count and percentage dicts, so those are **not**
covered; the skill reports the field count so it can't appear to check more
than it does.

Three properties are load-bearing and easy to break by "simplifying":

- **The Python side calls `run_single` in-process and reads raw
  `PlayerState` attributes — it does not read `reports/run_results.csv`.**
  `metrics/run_results.py:_money` cent-rounds every monetary field
  (`Decimal`, `ROUND_HALF_UP`) on its way into that CSV, while farm-c writes
  raw doubles at `%.17g`. Diffing the two CSVs would compare a cent-rounded
  number against a full-precision one; re-rounding the C side to match would
  then pass any drift under a cent — the same failure `replay-guard`'s
  SKILL.md documents for `round(money, 6)`. Going in-process keeps both sides
  at full precision.
- **Floats compare by `float.hex()`, never `==` or an epsilon.** Hex is exact
  and distinguishes `+0.0` from `-0.0`, which `==` does not. `%.17g` →
  `float()` round-trips to the identical double, so this is a true bit
  comparison, not a tolerance.
- **Seeds are read out of the C's own CSV, not re-derived.** The field
  comparison therefore does not depend on the minting property holding —
  minting is checked *separately*, against `runner/batch_run.py`'s own
  `random.Random(base).randrange(2**32)` agent-major loop, and reported as
  its own line.

Both sides read `<repo>/config` by construction: `main.py:load_config()` has
no way to point elsewhere, so the harness pins the C's `--config` to the same
directory rather than exposing a flag that could desync the two.

Note that `--runs N` mints a *different* seed sequence than `--runs M` for the
same base seed (the generator is consumed per job, agent-major), and a
`--strategy` subset changes the assignment too. Both sides always use the
same seeds as each other — but a seed that failed at one `--runs` value will
not reappear at another. Chase a failure with the exact `--runs`/`--seed`/
`--strategy` combination that produced it, or with `trace`, which takes the
seed directly.

`trace` is deliberately coarser than `check`: `main.c:print_day` prints money
at `%.2f` and rainfall at `%.3f`, so the per-day scan compares what that line
shows. It answers "which day did they part company", not "are they
bit-identical" — `check` is the exact gate. A pair can trace clean and still
fail `check` when drift stays under the printed precision every day; that is
itself a signal. `trace` ends with an exact (`%.17g`) comparison of the final
summary, which `farm-c single` already prints at full precision.

## Known open divergences (as of 2026-08-21)

**None.** `check` passes at every run count tried, including 2200 runs
(`--runs 200 --seed 7`). Keep this section current: if a divergence is found
and not immediately fixed, record it here with the exact
`--runs`/`--seed`/`--strategy` that reproduces it, so a stale "expected
failure" never masks a new one.

Three divergences have been found and fixed via this harness, all in
`farm-c` rather than the Python reference:

1. **`highest_money` never seeded with the opening balance**
   (`src/runner.c`). `runner/single_run.py:47` seeds it before the day loop;
   the C left it unset until the first in-day `track_peak_cash`, so any run
   that never rose above its start reported too low a peak.
2. **The water/fertilize pass was split in two** (`src/engine.c` step 17).
   `simulation/engine.py:176-183` handles both in one pass per crop. Both
   actions spend money, so two passes reorder the debits: every later balance
   rounded differently (1-256 ulps in `final_money`/`lowest_money`) and, when
   cash was tight, a different set of actions was affordable at all.
3. That second bug also produced what looked like a **separate mid-run
   `progression_player` divergence** (one extra planting on day 55). Worth
   remembering as a diagnostic lesson: a handful of runs diverging on every
   field and a scattering of others diverging by 1 ulp had one root cause.
   Fix the systematic-looking one first and re-measure before chasing the
   rest.

## Files

- `scripts/c_parity.py` — `check` / `seeds` / `trace` subcommands.
