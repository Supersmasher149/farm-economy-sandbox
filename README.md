# Farm Economy Sandbox

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
- `pytest` for the test suite

No third-party dependencies are required to run the simulator itself.

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
docs/superpowers/specs/ Design notes for the simulation architecture
```

## How the simulation works

Each simulated day runs through a fixed order (see
`docs/superpowers/specs/2026-08-04-full-crop-market-simulation-design.md`
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
