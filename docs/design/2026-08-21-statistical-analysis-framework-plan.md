# Statistical Analysis Framework Plan

## Purpose

The project already provides deterministic simulation, repeated sampling,
streaming aggregation, raw-run persistence, and reproducible reporting. This
plan adds formal statistical inference around those results without changing
the existing simulation semantics.

The central design rule is to keep statistical analysis outside the simulation
engine. Existing replay behavior and the current batch seed schedule remain
available as `legacy-mt19937-v1`. Paired experiments, common random numbers,
and parameter-uncertainty studies use explicit, versioned sampling modes.

## Proposed Architecture

- `metrics/estimands.py`: formal metric definitions and extraction rules.
- `metrics/inference.py`: moments, standard errors, and confidence intervals.
- `metrics/distributions.py`: quantiles, ECDFs, tails, and diagnostics.
- `metrics/comparisons.py`: paired and independent strategy comparisons.
- `runner/sampling_plan.py`: fixed, paired, and adaptive sampling schedules.
- `runner/adaptive.py`: convergence checkpoints and stopping decisions.
- `experiments/uncertainty.py`: epistemic parameter sampling and sensitivity
  analysis.

These components consume the existing `RunResult` stream from
`metrics/run_results.py`. `metrics/report.py`, `metrics/view.py`, and dashboard
code remain renderers of canonical results rather than independent calculation
paths.

## 1. Explicit Estimands

### Work

Create an estimand registry defining:

- Stable ID and display name.
- Unit of analysis.
- Population or conditional cohort.
- Per-run extraction function.
- Missing-value policy.
- Weighting convention.
- Supported confidence-interval method.
- Whether the metric supports adaptive stopping and strategy comparisons.

Initial definitions:

| Estimand | Formal definition |
| --- | --- |
| Expected final money | `E[M_T]`, including bankrupt and surviving runs, measured from canonical cent-rounded `RunResult.final_money` |
| Bankruptcy probability | `P(B_T = 1)`, bankruptcy by the configured simulation horizon |
| Final-money quantile | `Q_p(M_T) = inf {m : F(m) >= p}`, including all runs |
| Expected profit per day | `E[(M_T - M_0) / D]`, the mean of per-run ratios, matching current behavior |
| Strategy win probability | `P(Y_A > Y_B)`, with tie probability reported separately |
| Conditional bankruptcy day | `E[D_B | B_T = 1]`, explicitly conditional and not a survival estimate |

Document these existing distinctions:

- Mean of ratios versus ratio of pooled totals.
- Run-weighted versus planting-weighted metrics.
- Survivor-only versus all-run metrics.
- Undefined observations versus observed zero.
- Conditional bankruptcy day versus censored time-to-bankruptcy analysis.

### Outputs

Add estimand metadata to `summary.json`:

```json
{
  "estimands": {
    "expected_final_money": {
      "unit": "currency",
      "population": "all_runs",
      "definition": "E[final_money]",
      "missing_policy": "none"
    }
  }
}
```

### Acceptance Criteria

- Every inferential result references an estimand ID.
- Effective sample count is reported for every estimate.
- Reports do not use ambiguous labels without defining the denominator.
- Existing descriptive values remain unchanged.

## 2. Confidence Intervals

### Means

Extend `_MeanAccumulator` in `metrics/aggregate_results.py`, or introduce a
separate streaming moments accumulator, containing:

- Count.
- Mean.
- M2.
- Sample variance.
- Standard deviation.
- Standard error.

Use a Student-t interval by default. Make the interval undefined for fewer than
two observations. A normal interval can be available as an explicit method,
not the default.

### Bankruptcy Rates

Add a Bernoulli accumulator containing successes and trials.

- Use a Wilson score interval by default.
- Provide an optional exact Clopper-Pearson interval for audit-sensitive use.
- Handle `0/n` and `n/n` correctly.

### Quantiles

Do not use the current 1,024-observation reservoir for formal quantile
inference.

- Initially load exact observations from `run_results.csv`.
- Use a deterministic percentile bootstrap.
- Derive a separate analysis seed from the base seed and estimand ID.
- Report the bootstrap replication count and empirical quantile convention.

The reservoir can remain for low-cost dashboard medians but must be labeled
approximate.

### Strategy Differences

Support two paths:

- Independent bootstrap for legacy batches with disjoint strategy seeds.
- Paired bootstrap over replicate IDs for paired experiments.

Report:

- Point difference.
- Relative difference when the reference denominator is valid.
- Confidence bounds.
- Effective pair count.
- Pairing method.
- Bootstrap method and replication count.

### Acceptance Criteria

- Synthetic tests match `statistics.variance` and hand-calculated Wilson
  intervals.
- Intervals handle empty, one-observation, all-success, and all-failure
  cohorts.
- Bootstrap output is deterministic for a fixed analysis seed.
- Outcome standard deviation and uncertainty in the estimated mean are
  displayed separately.

## 3. Sample Size and Stopping Rules

### CLI

Retain `--runs N` and add an adaptive mode such as:

```bash
python3 main.py batch \
  --min-runs 500 \
  --max-runs 20000 \
  --checkpoint-runs 250 \
  --stop-estimand expected_final_money \
  --target-half-width 5 \
  --confidence 0.95
```

Additional rules:

- `--bankruptcy-half-width 0.01`
- `--min-bankruptcies 20`
- `--min-survivals 20`
- `--stopping-mode all|any`
- `--analysis-seed`
- `--sampling-plan legacy|paired`

### Statistical Design

Do not repeatedly inspect ordinary fixed-sample 95% intervals and claim 95%
coverage. Use one of:

1. Predeclared checkpoints with alpha spending.
2. Confidence sequences.
3. Two-stage pilot and confirmation sampling.

The first implementation should use fixed checkpoints with a documented alpha
spending rule.

### Execution

The adaptive controller should:

- Run equal-sized blocks for every strategy.
- Evaluate stopping only after complete blocks.
- Enforce minimum and maximum sample sizes.
- Require minimum cohort counts for conditional or rare-event metrics.
- Record why sampling stopped.
- Record criteria that remained unmet at the maximum.
- Preserve deterministic results across worker counts.

Rare-event rules must not run indefinitely. If minimum bankruptcy events are
not observed by `--max-runs`, publish the result as precision target unmet.

### Acceptance Criteria

- Fixed `--runs` mode produces unchanged seeds and outputs.
- Adaptive mode is invariant to worker count and dispatch window.
- Stopping occurs only at declared checkpoints.
- The report distinguishes `precision_reached`, `max_runs_reached`, and
  `rare_event_minimum_unmet`.

## 4. Convergence Diagnostics

### Checkpoint State

Add a non-destructive `snapshot()` method to the inference accumulator. At each
checkpoint, persist:

- Sample count.
- Estimate.
- Standard deviation.
- Standard error.
- Confidence bounds.
- Absolute and relative half-width.
- Change from the previous checkpoint.
- Change across a configurable checkpoint window.
- Quantile estimate and bootstrap width where enabled.

### Stability Measures

Add:

- Running mean stability.
- Running standard-deviation stability.
- Confidence-interval width trend.
- Batch-to-batch estimate change.
- Quantile drift.
- Rare-event count growth.
- Stop-rule state.

Avoid treating relative change as meaningful when the estimate is near zero.

### Artifact

Create `convergence.json` or embed a versioned `convergence` section in
`summary.json`. If a new artifact is used, add it to `ARTIFACT_NAMES` in
`main.py` so publication remains atomic.

### Charts

Add:

- Estimate versus runs.
- Confidence-interval half-width versus runs.
- Bankruptcy estimate and Wilson interval versus runs.
- Quantile versus runs.
- Checkpoint-to-checkpoint change.

### Acceptance Criteria

- Checkpoint history can reproduce the final estimate.
- Charts read canonical checkpoint data rather than recomputing statistics.
- Convergence diagnostics do not consume simulation RNG draws.

## 5. Distribution Analysis

### Exact Analysis

Use completed `run_results.csv` for formal distribution analysis:

- Histogram with documented binning, preferably Freedman-Diaconis with a
  fallback for constant data.
- ECDF.
- p5, p25, p50, p75, and p95.
- Configurable tail probabilities such as `P(final_money < 0)` or
  `P(final_money > X)`.
- Bankruptcy-day histogram and ECDF.
- Survivor and bankrupt final-money distributions.

### Diagnostics

Add:

- Sample skewness with its exact convention documented.
- Interquartile range.
- Median absolute deviation.
- Tukey outlier counts.
- Extreme-tail counts.
- Mean-to-median difference.
- Optional censored survival curve for time to bankruptcy.

Do not automatically remove outliers. Flag and describe them.

### Storage Policy

Use two tiers:

- Formal inference: exact raw observations from CSV.
- Streaming dashboard: bounded reservoirs or sketches, clearly marked
  approximate.

### Acceptance Criteria

- Quantiles match a documented empirical quantile convention.
- Exact formal results do not depend on the 1,024-value reservoir.
- Degenerate and very small samples render correctly.
- Distribution charts identify conditional cohorts.

## 6. Strategy Comparisons

### Initial Independent Comparisons

The current batch schedule assigns different seeds to strategies. First
support independent comparisons without changing sampling:

- Difference in expected final money.
- Difference in expected profit per day.
- Difference in bankruptcy probability.
- Relative improvement.
- Probability that a randomly selected strategy A outcome exceeds a randomly
  selected strategy B outcome.

For unpaired win probability, use the Mann-Whitney interpretation:

```text
P(Y_A > Y_B) + 0.5 P(Y_A = Y_B)
```

Report strict wins and ties separately when practical.

### Paired Comparisons

Add `replicate_id` and a paired sampling plan:

```text
replicate 1: strategies A, B, and C share environment root 1
replicate 2: strategies A, B, and C share environment root 2
```

Compute per-replicate differences before aggregation. Report:

- Mean paired difference.
- Paired standard error or bootstrap interval.
- Win, loss, and tie probabilities.
- Pair count.
- Correlation between paired outcomes.
- Variance reduction relative to an independent estimate.

### Multiple Comparisons

For 11 strategies there are 55 pairs per estimand. Use this default policy:

- Bonferroni-adjusted simultaneous confidence intervals for formal all-pairs
  reporting.
- Holm-adjusted p-values if hypothesis tests are added.
- Benjamini-Hochberg only as an explicitly exploratory option.
- Prefer comparisons against one selected baseline when all-pairs output is
  unnecessary.

### Acceptance Criteria

- Independent and paired results cannot be confused in output.
- Missing pairs are reported and excluded consistently.
- Pair ordering does not change results.
- Multiplicity family and correction method are recorded.

## 7. Common Random Numbers

### Constraint

Using the same legacy seed for two strategies is only weak pairing. Strategy
decisions change RNG draw counts, which shifts subsequent weather, yield,
market, and contract draws.

Do not modify the current `RandomEvents` stream. Doing so would invalidate
replay baselines and C parity.

### Staged Plan

1. Add an opt-in `shared-initial-seed-v1` experiment plan.
2. Give every strategy the same legacy run seed for replicate N.
3. Measure paired covariance and actual variance reduction.
4. Introduce separate environment and policy seed roots.
5. Split random domains into weather, market, harvest, contracts, and policy.
6. Eventually use event-addressed random values with stable semantic keys.

Example keys:

```text
(replicate, day, "temperature")
(replicate, day, item_id, "market_innovation")
(replicate, plot_id, planting_generation, "yield")
(replicate, buyer_id, offer_day, "contract_quantity")
(replicate, strategy_id, day, plot_id, "water_compliance")
```

### Acceptance Criteria

- Legacy replay remains unchanged.
- Adding or reordering strategies does not remap paired experiment seeds.
- Event keys use stable canonical serialization, never Python `hash()` or
  arbitrary object `repr()`.
- Worker count and execution order do not change paired outcomes.
- New RNG semantics receive a separate replay fixture suite.

## 8. Input and Parameter Uncertainty

### Uncertainty Specification

Keep epistemic metadata outside the existing runtime config JSON. Add a
separate specification:

```json
{
  "schema": "farm-uncertainty-v1",
  "parameters": [
    {
      "path": "crops[id=quickweed].loss_chance",
      "distribution": "beta",
      "parameters": {"alpha": 19, "beta": 81},
      "source": "field-study-2026"
    }
  ]
}
```

Each parameter should define:

- Stable ID-based path.
- Distribution and parameters.
- Unit and transform.
- Provenance.
- Aleatory or epistemic classification.
- Correlation group.
- Admissible constraints.

Every sampled configuration must be deep-copied and passed through existing
configuration validation.

### Scenario Sampling

Implement nested sampling:

```text
epistemic configuration sample
    -> multiple aleatory simulation replicates
        -> all selected strategies
```

This separates:

- Between-configuration epistemic variance.
- Within-configuration aleatory variance.
- Strategy effects.
- Configuration-by-strategy interactions.

### Sensitivity Methods

Implement in this order:

1. One-at-a-time low/base/high analysis.
2. Scenario robustness tables.
3. Latin hypercube or quasi-random parameter sampling.
4. Morris screening for many uncertain inputs.
5. Sobol indices for the reduced parameter set.

Sobol analysis should not be the first global method because it requires many
evaluations and strict sample-matrix bookkeeping.

### Acceptance Criteria

- Every result identifies configuration sample and replicate.
- Invalid sampled configurations are rejected or resampled according to a
  documented rule.
- The sampled parameter matrix is persisted.
- Aleatory and epistemic uncertainty are reported separately.
- Correlated parameters are not silently sampled independently.

## 9. Reproducibility and Auditability

Persist:

- Schema version.
- Base seed and seed-plan version.
- Per-run seed or deterministic derivation rule.
- Replicate and configuration-sample IDs.
- RNG algorithm and mode.
- Analysis/bootstrap seed.
- Requested and realized run counts.
- Sampling and stopping methods.
- Stop reason.
- Confidence level and interval methods.
- Bootstrap replication count.
- Multiple-comparison method.
- Estimand registry version.
- Full configuration snapshot.
- Uncertainty specification and sampled parameter matrix.
- Python version and platform.
- Git commit and dirty-tree state.
- Optional accelerator status.
- Command invocation.
- Start time, end time, and duration.
- Intermediate accumulator and convergence state.

Use a versioned metadata structure in `summary.json` or a new atomically
published `analysis_metadata.json`.

Accumulator state should contain sufficient statistics and method versions,
not pickled Python objects.

## 10. Validation

### Statistical Unit Tests

Add deterministic reference tests for:

- Welford or mergeable sample variance.
- Student-t and normal intervals.
- Wilson and exact binomial intervals.
- Exact empirical quantiles.
- Deterministic bootstrap.
- Paired and independent differences.
- Win probabilities with ties.
- Multiple-comparison correction.
- Adaptive checkpoint behavior.
- Rare-event minimum handling.

### Simulation Integration Tests

Verify:

- Existing fixed batches remain byte-identical.
- Statistical analysis consumes no simulation RNG draws.
- Parallel and sequential inference agree.
- Pair assignments are invariant to worker count.
- `summary.json`, Markdown, terminal view, and charts display the same
  canonical estimates.

### Method Validation

Use synthetic distributions where the true answer is known:

- Normal outcomes for mean interval coverage.
- Bernoulli outcomes for bankruptcy intervals.
- Skewed distributions for quantiles.
- Correlated paired samples for variance-reduction tests.

Formal real-world validation should later compare simulated ranges,
bankruptcy behavior, crop economics, and sensitivity directions against
domain evidence or expert expectations.

## Delivery Sequence

### Phase 1: Fixed-Sample Inference

- Estimand registry.
- Streaming sample variance and standard error.
- Student-t mean intervals.
- Wilson bankruptcy intervals.
- Versioned inference metadata.
- Report and terminal rendering.

This phase makes no simulation or seed changes.

### Phase 2: Exact Distributions

- Formal quantiles from CSV.
- Bootstrap quantile intervals.
- Histograms, ECDFs, tails, and skew diagnostics.
- Separate exact and reservoir-based outputs.

### Phase 3: Convergence and Adaptive Sampling

- Checkpoint snapshots.
- Convergence artifact and plots.
- Minimum and maximum runs.
- Sequentially valid stopping rules.
- Rare-event minimums.

### Phase 4: Strategy Comparisons

- Independent comparisons for legacy batches.
- Multiplicity correction.
- Paired sampling plan and replicate IDs.
- Shared-initial-seed evaluation.

### Phase 5: Strong Common Random Numbers

- Environment and policy RNG separation.
- Domain-specific streams.
- Event-addressed shocks.
- Separate replay and parity contracts.

### Phase 6: Epistemic Uncertainty

- External uncertainty specification.
- Scenario and nested sampling.
- One-at-a-time analysis.
- Morris screening and Sobol indices.
- Robustness reports.

### Phase 7: Domain Validation

- Calibrate uncertainty distributions from evidence.
- Compare expected behavior with real-world or expert ranges.
- Document model limitations and validation status.

## Python and C Boundary

Implement inference in Python first. Keep farm-c as a deterministic raw-data
producer.

Initially mirror in C only:

- Additional raw per-run fields needed by analysis.
- Counts, means, and possibly variance if useful for standalone summaries.
- Sampling-plan and RNG metadata.

Do not initially duplicate bootstrap, multiple-comparison, sensitivity, or
rich reporting code in C. Consume farm-c CSV output through the same Python
inference layer instead.
