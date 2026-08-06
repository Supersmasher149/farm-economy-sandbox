# Contributing

## Setup

```bash
pip install -r requirements-dev.txt
python3 -m pytest
ruff check
ruff format --check
```

All three must pass before a change is done. No third-party dependencies are
needed to run the simulator itself -- `requirements-dev.txt` only covers
the test/lint tooling.

## Before you change anything

Read `CLAUDE.md` first -- it states the architectural rules that matter most
here:

- **Agents decide, the engine mutates.** Agent code (`agents/*.py`) only
  answers decision questions; only `simulation/engine.py` (via
  `simulation/actions.py`, `contracts.py`, `processing.py`, `markets.py`)
  applies those decisions to state.
- **Determinism is load-bearing.** The same seed must always reproduce the
  same day-by-day outcome. If you touch anything under `simulation/`, run
  the full test suite and reason about whether existing recorded seeds
  still mean the same thing -- see `tests/test_engine.py::test_same_seed_produces_identical_results`.
- **Config is data, not code.** Rebalancing the economy means editing
  `config/*.json`, not simulation logic.

`docs/design/` records the rationale behind non-obvious past decisions
(soil/regen fixes, fertilizer atomicity, contract feasibility, ...) -- check
there before assuming a piece of behavior is accidental rather than
deliberate.

## Balance changes

If your change touches `config/*.json` or agent decision logic, follow the
"Balance-testing workflow" in `CLAUDE.md`/`README.md`: run a seeded batch
before and after, and note what changed in `## Warnings` and the economics
audit in your PR description.

## Pull requests

Keep PRs scoped to one change. Include the test/lint output (or note that
CI is green) and, for anything under `simulation/`, a note on what you
verified about replay safety.
