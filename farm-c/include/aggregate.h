/* Per-strategy accumulation over a batch's streamed BatchRunResults -- the
 * narrow C analogue of ../metrics/aggregate_results.py's StrategyAggregator.
 *
 * This exists as its own module rather than living in main.c because three
 * separate consumers need the same numbers: the terminal table
 * (print_batch_summary), the balance warnings (include/warnings.h), and the
 * HTML dashboard (include/dashboard.h). Deriving an average in three places
 * is how they drift, so every derived value is computed exactly once, in
 * aggregate_finalize, and the renderers only format what it returns -- the
 * same "exactly one source of truth for a number's value, independent of
 * how it gets displayed" rule ../CLAUDE.md states for the Python reporting
 * path.
 *
 * Deliberately narrower than aggregate_results.py: running sums only, no
 * medians (no bounded reservoir) and no Neumaier-compensated accumulator.
 * See include/warnings.h's scope note -- the compensation buys precision
 * that does not matter at the batch sizes this port's reporting targets,
 * and the CSV remains the exact per-run record either way.
 */
#ifndef FARM_AGGREGATE_H
#define FARM_AGGREGATE_H

#include <stdbool.h>
#include <stddef.h>

#include "batch.h"
#include "warnings.h"

/* Running sums for one strategy's cohort. Zero-initialise before first use
 * (cmd_batch memsets the whole array), then feed every run through
 * aggregate_add_run. Bounded by strategy count, never by run count, which
 * is what keeps a batch's peak memory independent of --runs the way
 * include/batch.h promises. */
typedef struct {
    long runs;
    long bankrupt_count;
    double sum_final_money;
    double sum_net_profit;
    double sum_revenue;
    double sum_expenses;
    double sum_days_simulated;
    double sum_idle_days;
    double sum_profit_per_day;

    /* crop_totals is borrowed -- it points into the caller's flat
     * allocation, is length config->crop_count, and is indexed the same way
     * BatchRunResult.crop_plant_counts is. */
    long *crop_totals;
    long planted_total;
    double sum_crop_loss_rate;
    long crop_loss_rate_count; /* runs with at least one harvest event */
    double sum_watering_rate;
    double sum_first_upgrade_day;
    long first_upgrade_count; /* runs that bought at least one upgrade */
} StrategyAgg;

/* Every derived value, computed once. `runs == 0` yields an all-zero
 * summary with the has_* flags false, so a strategy nothing ran for
 * formats as "no data" rather than dividing by zero. */
typedef struct {
    long runs;
    long bankrupt_count;
    double bankruptcy_rate; /* percent, unrounded */
    double avg_final_money;
    double avg_net_profit;
    double avg_revenue;
    double avg_expenses;
    double avg_days_simulated;
    double avg_idle_days;
    double avg_profit_per_day;

    /* False when no run in the cohort ever planted anything; the crop
     * percentages are meaningless (not zero) in that case, the same
     * distinction metrics/aggregate_results.py draws. */
    bool crop_usage_observed;

    /* Both of the following follow metrics/run_results.py's None-vs-0
     * discipline: a rate no run ever observed is *undefined*, not 0%, and
     * must not be folded into a mean as zero. */
    bool has_crop_loss_rate;
    double avg_crop_loss_rate; /* percent, unrounded */
    double avg_watering_rate;  /* percent, unrounded */

    bool has_first_upgrade_day;
    double avg_first_upgrade_day; /* over runs that bought one */
    double first_upgrade_rate;    /* percent of runs that bought one */
} StrategySummary;

/* Folds one completed run into `agg`. Call once per BatchRunResult, from
 * inside the BatchRunCallback -- `result->crop_plant_counts` is borrowed
 * and only valid for that callback's duration. */
void aggregate_add_run(StrategyAgg *agg, const BatchRunResult *result, size_t crop_count);

/* Computes every derived value. `out_crop_usage_pct`, when non-NULL, must
 * have room for `crop_count` doubles and receives each crop's share of this
 * cohort's plantings as a percent (all zero when crop_usage_observed is
 * false). */
void aggregate_finalize(const StrategyAgg *agg, size_t crop_count,
                        double *out_crop_usage_pct, StrategySummary *out);

/* Adapts a finalized summary into the struct warnings_evaluate_strategy
 * expects. `crop_ids` and `crop_usage_pct` are borrowed straight through --
 * `out` stays valid only as long as they do. */
void aggregate_to_warning_stats(const StrategySummary *summary, const char *const *crop_ids,
                                const double *crop_usage_pct, size_t crop_count,
                                StrategyWarningStats *out);

#endif /* FARM_AGGREGATE_H */
