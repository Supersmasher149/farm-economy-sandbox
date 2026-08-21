---
name: c-parity
description: Cross-language parity check for the farm-c port - runs the same minted seeds through farm-c and the Python simulator and diffs every run bit-for-bit. Use before/after touching anything under farm-c/src or farm-c/include, when asked whether the C port still matches Python, to verify seed minting, or to localize a known C-vs-Python divergence to its first simulated day.
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
```

`check` exits non-zero on any mismatch and prints the offending fields with
both sides in hex.

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

`check` passes at the default `--runs 5 --seed 42` but **not** at wider run
counts. Two divergences remain, both pre-existing and both distinct from the
`highest_money` seeding bug this harness first found (fixed in
`farm-c/src/runner.c`; regression test in `farm-c/tests/test_runner.c`).

1. **Sub-ulp accumulation drift on `random_agent` and
   `fertilizer_maximalist`.** At `--runs 50 --seed 42`: 11 field mismatches
   across 550 runs, all in accumulated money fields (`final_money`,
   `lowest_money`, `total_expenses`, `net_profit`), ranging from 1 ulp
   (`0x1.62a6666666668p+8` vs `...667p+8`) to ~256 ulps
   (`0x1.ddb722ec6db00p-3` vs `...6dc00p-3`). **Every integer field matches on
   these runs** — same plantings, harvests, waterings — so the trajectories
   are identical and only the last bits of the running sums differ. That
   points at summation order or a missing compensation step in an accumulator,
   not at a behavioral difference. `random_agent` carrying 6 of the 7 failing
   runs is worth a look on its own: it is the one agent driven by the
   BLAKE2b `decision_random` stream rather than the event RNG.
2. **`progression_player` diverges mid-run on some seeds.** At base seed 42
   `--runs 2`, `trace progression_player 127978094` localizes it to **day
   55**, where C has 8 crops planted to Python's 7 and money differs by
   exactly 3.00 — one extra seed purchase, i.e. a planting-affordability or
   decision-boundary difference. Days 1-54 are identical. Confirmed still
   present after the `highest_money` fix, so it is a separate root cause.

Because #1 only surfaces at wider run counts, use `--runs 50` when
investigating rather than the default, and re-check the default afterwards.

Delete or update this section as these are fixed, so a stale "expected
failure" never masks a new one.

## Files

- `scripts/c_parity.py` — `check` / `seeds` / `trace` subcommands.
