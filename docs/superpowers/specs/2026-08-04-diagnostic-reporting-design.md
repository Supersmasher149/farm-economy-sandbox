# Farm Economy Diagnostic Reporting Design

## Goal

Make batch results explain why runs fail and whether strategy differences are
real, without changing the simulation's economic rules or existing bankruptcy
behavior.

## Scope

The existing bankruptcy rule is preserved. A run becomes bankrupt at the end
of a day when all of the following are true:

- cash is below the cheapest seed cost;
- no crops are planted;
- no crop inventory exists;
- no processing jobs are active.

The run is not forcibly assigned a zero balance. The current day completes,
the run records bankruptcy metadata, and the next daily-loop iteration stops.
`bankruptcy_day` is reported using one-based human day numbering, while the
existing internal `player.day` and upgrade days remain unchanged.

## Per-Run Metrics

`PlayerState` will track:

- `bankruptcy_day` and `bankruptcy_reason`;
- categorized cash expenses for seeds, watering, fertilizer, upgrades, and
  contract penalties;
- occupied plot-days in addition to total plot-days;
- crop decision observations, including unlocked, affordable, selected, and
  blocked counts per crop.

All cash outflows will use a shared expense-recording helper. Existing totals
remain available for compatibility and are derived consistently with the new
category totals.

The following accounting definitions are used in reports:

- **Total costs:** every recorded cash outflow.
- **Gross profit:** revenue minus seed, watering, and fertilizer costs.
- **Operating profit:** gross profit minus contract penalties.
- **Net cash change:** revenue minus all costs, including upgrade purchases.

Upgrades are treated as capital purchases: they reduce cash and total costs,
but are excluded from operating profit.

## Aggregate Report

The per-strategy report will retain existing all-run final-cash statistics and
add separate survivor and bankrupt cohorts:

- number of surviving and bankrupt runs;
- average and median final money for each cohort when non-empty;
- bankruptcy rate;
- average bankruptcy day and minimum cash balance for bankrupt runs;
- bankruptcy reason counts;
- average total costs, gross profit, operating profit, and net cash change;
- average cost categories and revenue channels.

Each CSV row will contain the exact per-run values. Markdown will contain the
cohort summaries and the aggregate cash-flow breakdown.

## Availability and Watering Diagnostics

At each planting opportunity, the engine will observe every configured crop's
unlock and affordability status, then record the agent's selected crop and any
blocked selection reason. This distinguishes a crop that an agent ignores
from one that was unavailable or unaffordable.

Watering will report both:

- coverage over all plot-days, preserving the existing metric;
- coverage over occupied plot-days, showing care applied to actual crops.

## Economics Audit

The batch report will include a deterministic configuration audit for every
crop and fertilizer option. It will show seed cost, growth duration, yield
range, base sale price, loss chance, nominal revenue, nominal profit per
cycle, nominal profit per growth day, and fertilizer marginal profit.

The audit is explicitly nominal: it uses configured base prices and the
existing expected-value formulas, and does not claim to model weather,
quality grades, market capacity, or neglect. The existing market channel
configuration remains available in the config snapshot for checking actual
sale-price behavior.

## Diagnostic Scenarios

The batch command will accept optional `--days` and `--start-money` overrides.
The command will copy the loaded simulation settings before applying them, so
normal configuration files are not modified. The effective settings will be
written to the config snapshot and report header. This supports short runs
for failure timing and higher-cash runs for upgrade reachability while
preserving deterministic seeds.

## Testing

Tests will verify:

- bankruptcy day and reason are recorded without changing the termination
  rule;
- all expense categories reconcile to total expenses;
- gross, operating, and net cash definitions are correct;
- surviving and bankrupt cohort aggregation handles empty cohorts;
- crop availability and blocked-choice counters are accurate;
- occupied-plot watering coverage differs correctly from total plot coverage;
- economics audit values match the configured formulas;
- CLI overrides affect only the effective run configuration;
- sequential and parallel batches remain deterministic.

No balance tuning or agent policy changes are part of this diagnostic feature.
