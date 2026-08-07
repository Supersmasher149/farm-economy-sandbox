# Farm Economy Sandbox

[![CI](https://github.com/Supersmasher149/farm-economy-sandbox/actions/workflows/ci.yml/badge.svg)](https://github.com/Supersmasher149/farm-economy-sandbox/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

A headless, deterministic farm-economy simulator built for balance testing.
Instead of a playable game, it ships a roster of scripted agents that each
play one deliberate strategy (optimal, reckless, neglectful, risk-averse,
random, ...), and a batch runner that plays every strategy hundreds or
thousands of times to surface how the economy actually behaves in
aggregate -- dominant crops, exploitable upgrades, bankruptcy traps, and so
on.

Every run is seeded and reproducible: replaying a recorded seed reproduces
the exact same day-by-day outcome.

## Requirements

- Python 3.11+ (uses `match`-free but relies on `dict | None` type hints)
- `pytest` and `ruff` for the test suite and lint checks:
  `pip install -r requirements-dev.txt`

No third-party dependencies are required to run the simulator itself.

### Optional: the C accelerator

A compiled kernel for the per-plot daily physics is available and makes
batches roughly 1.15x faster. It is **off by default and entirely optional** —
building it needs only a C compiler and the CPython headers, no third-party
packages:

```bash
python3 tools/build_fastplot.py         # build in place
python3 tools/build_fastplot.py --clean # remove it again
```

Without it, `simulation/weather.py` uses the pure-Python plot loop, which
remains the reference implementation. The two are held to **bit-identical**
output by `tests/test_fastplot_equivalence.py`, so enabling the accelerator
never changes what a seed replays to.

## Quick start

```bash
# Play one strategy once, with full daily history
python3 main.py single --strategy profit_optimizer --seed 42 --verbose

# Reproduce a specific recorded run exactly
python3 main.py replay --strategy fast_seller --seed 123456789

# Run every strategy N times each and generate a report
python3 main.py batch --runs 1000

# Run a short/high-cash diagnostic scenario without editing config files
python3 main.py batch --runs 100 --days 30 --start-money 300
```

`batch` writes to `reports/`:

- `run_results.csv` -- one row per simulation run
- `config_snapshot.json` -- the exact config the batch ran with
- `summary_report.md` -- per-strategy stats, cash-flow diagnostics, economics
  audit, and automated balance warnings

Pass `--seed` to `batch` to make an entire batch (including every agent's
per-run seeds) reproducible, which is useful for A/B-testing a config or
code change against the exact same simulated conditions.

`batch` runs across a process pool by default (one process per CPU core),
since each simulated run is independent. Results are byte-for-byte
identical to a sequential run for the same `--seed`. Pass `--workers 1` to
force sequential execution, or `--workers N` to cap the pool size.

While a batch runs, a live progress line is drawn on **stderr**:

```
[████████████░░░░░░░░░░░░░░░░]  42.7% |  4,270/10,000 | 1,523 sim/s | 00:02 elapsed | 00:03 left
```

It shows a status bar, percent complete, simulations finished versus the
total, throughput, elapsed time, and estimated time remaining, and the same
elapsed/rate figures are printed with the report paths at the end. It appears
automatically when stderr is a terminal; `--progress` forces it on (useful
when piping the report on stdout to a file) and `--no-progress` turns it off.
The reporter only counts results as they stream past, so it never changes
what a given `--seed` produces.

Optional `--days N` and `--start-money N` batch arguments override those two
simulation settings for the diagnostic run only; the effective values are
recorded in the config snapshot and report.

## Project layout

```
main.py                 CLI entry point (single / replay / batch)
config/                 All tunable game data (crops, upgrades, markets, ...)
simulation/             Pure rules + the deterministic daily engine
agents/                 Scripted strategies used as balance-testing probes
runner/                 Drives one run / a batch of runs
metrics/                Aggregation, warnings, CSV + Markdown reporting
tests/                  pytest suite for the simulation and engine
docs/design/            Design notes for the simulation architecture
```

## How the simulation works

Each simulated day runs through a fixed order (see
`docs/design/2026-08-04-full-crop-market-simulation-design.md`
for the full design): weather and prices update, mature crops are
harvested and graded, storage ages and spoils, contract offers are
generated, then the agent's decisions for the day (contracts, processing,
sales, upgrades, watering, fertilizing, planting) are applied, and expired
contracts are resolved.

Agents never mutate state directly -- they only answer yes/no or
which/how-much questions (`choose_crop`, `should_buy_upgrade`,
`should_water`, `should_fertilize`, `choose_contracts`, `choose_sales`,
...). The engine (`simulation/engine.py`) is the only thing that applies
those decisions to `PlayerState`.

All config -- crops, upgrades, fertilizer, watering, soil, weather,
markets, buyers/contracts, storage, processing -- lives under `config/` as
plain JSON and is validated at load time (`simulation/configuration.py`).
Tuning the economy means editing that JSON, not the code.

## The agent roster

Each agent is a deliberate probe, not a "player" to optimize for its own
sake -- its docstring states the specific balance question it exists to
answer:

| Agent | Tests |
|---|---|
| `fast_seller` | Whether the shortest-growth crop becomes dominant through rapid reinvestment |
| `profit_optimizer` | The theoretically-optimal expected-value strategy |
| `progression_player` | The intended upgrade-progression path |
| `neglectful_grower` | How much watering neglect alone erodes an otherwise sound strategy |
| `reckless_spender` | Financial mismanagement in isolation from crop-care neglect |
| `random_agent` | The baseline floor every deliberate strategy should beat |
| `no_upgrade_player` | Whether upgrades are worth buying at all |
| `fertilizer_maximalist` | Fertilizer ROI ("always fertilize" vs. only when profitable) |
| `diversifier` | Whether monoculture is actually optimal |
| `risk_averse_grower` | Whether cautious play is unfairly punished by the economy |
| `upgrade_rusher` | Whether rushing upgrades ahead of real income is a dominant exploit |

`metrics/warnings.py` turns batch results into automated flags -- dominant
or dead crops, bankruptcy rates above 20%, upgrades rarely reached -- so a
regression in balance shows up in the report without manually eyeballing
every strategy's numbers.

## Testing

```bash
python3 -m pytest

# Lint and format checks (ruff, configured in pyproject.toml)
ruff check
ruff format --check
```

## Balance-testing workflow

1. `python3 main.py batch --runs 1000 --seed <fixed-seed>`
2. Read `reports/summary_report.md`, starting with the `## Warnings`
   section.
3. Adjust `config/*.json` (or, if a warning traces back to an agent's
   decision logic contradicting its own documented behavior, fix the
   agent).
4. Re-run with the **same seed** to isolate the effect of the change from
   run-to-run noise, then drop `--seed` for the final report.

### Example output

A trimmed, real `## Warnings` section from `reports/summary_report.md`
(a sample from one seeded batch run, not the repo's current balance state --
these are exactly the kind of findings the balance-testing workflow above
is meant to chase down):

```
- ⚠️ [profit_optimizer] Possible runaway economy: avg final money 9011.64 is 20x+ starting money.
- ⚠️ [reckless_spender] High bankruptcy rate: 62.5% (> 20%).
- ⚠️ [no_upgrade_player] Dead crop: 'purplehaze' is only 3.44% of plantings (< 5%).
- ⚠️ [no_upgrade_player] First upgrade is rarely reached: 0.0% of runs purchased one.
- ⚠️ [diversifier] High bankruptcy rate: 50.0% (> 20%).
```

Each line is auto-generated by `metrics/warnings.py` from the batch's
aggregate stats -- no manual eyeballing of per-strategy numbers required.

Two examples from this repo's own history: `risk_averse_grower`'s
docstring promised it "fertilizes for safety, not yield," but its code
fertilized unconditionally whenever affordable -- identical to the
deliberately-reckless agents. Fixing it to actually weigh the loss-chance
reduction dropped its bankruptcy rate from ~78% to under 1%. Separately,
the contract system (`config/buyers.json`) was net-negative EV for every
agent that used it (more failures than completions), because no agent
plans production around a contract deadline; loosening deadlines and
penalty rates brought completion rates from roughly 1-in-4 to roughly
1-in-3.
