/* Bit-for-bit port of ../metrics/warnings.py's threshold rules -- the
 * balance-testing signal `main.py batch`'s report leads with (see
 * CLAUDE.md's "Balance-testing workflow"). Deliberately narrower than the
 * Python module: only the rules `evaluate_warnings` actually contains, and
 * only over the stats those rules read -- not the rest of
 * metrics/aggregate_results.py's ~50-field summary (crop_percentages,
 * expenses_by_category, quality_harvested, revenue_by_channel,
 * crop_decision_observations, medians, and the JSON/markdown/view/dashboard
 * layers built on top of them stay out of scope; see the farm-c README for
 * the reporting-pipeline scope decision).
 *
 * `main.py batch` always calls `evaluate_warnings(summary, config)` with no
 * threshold override, so this only ports DEFAULT_THRESHOLDS -- there is no
 * CLI-exposed way to override them on the Python side to port either.
 */
#ifndef FARM_WARNINGS_H
#define FARM_WARNINGS_H

#include <stdbool.h>
#include <stddef.h>

typedef struct {
    double dominant_crop_pct;         /* default 70 */
    double dead_crop_pct;             /* default 5 */
    double high_bankruptcy_pct;       /* default 20 */
    int upgrade_too_fast_day;         /* default 5 */
    double upgrade_too_slow_fraction; /* default 0.9 */
    double runaway_money_multiple;    /* default 20 */
    int runaway_reference_days;       /* default 30 */
    double high_crop_loss_rate_pct;   /* default 30 */
} WarningThresholds;

extern const WarningThresholds WARNING_DEFAULT_THRESHOLDS;

/* metrics/warnings.py:runaway_money_multiple. */
double warnings_runaway_money_multiple(int total_days, const WarningThresholds *thresholds);

/* The scalar/dict-lite subset of metrics/aggregate_results.py's finalize()
 * output that evaluate_warnings's rules actually read, for one strategy's
 * cohort. crop_ids/crop_usage_pct are parallel arrays of length crop_count
 * (crop_usage_pct only meaningful when crop_usage_observed is true); both
 * are borrowed -- valid only for the duration of the
 * warnings_evaluate_strategy call that receives them. */
typedef struct {
    long num_runs;
    double bankruptcy_rate; /* percent, unrounded */
    double avg_final_money;

    bool crop_usage_observed;
    const char *const *crop_ids;
    const double *crop_usage_pct; /* percent, unrounded */
    size_t crop_count;

    bool has_first_upgrade_day;
    double avg_first_upgrade_day;
    double first_upgrade_rate; /* percent */

    bool has_crop_loss_rate;
    double avg_crop_loss_rate; /* percent, unrounded */
    double avg_watering_rate;  /* percent, already rounded to 2dp (message text only) */
} StrategyWarningStats;

/* Evaluates every rule for one strategy's cohort, in the same order
 * evaluate_warnings's per-strategy loop body does, calling emit(line,
 * context) once per triggered warning. `line` is valid only for the
 * duration of that one emit call -- copy it if the caller needs to keep it.
 * `strategy` is used only for the "[strategy] ..." message prefix. */
void warnings_evaluate_strategy(const char *strategy, const StrategyWarningStats *stats,
                                int total_days, double start_money,
                                const WarningThresholds *thresholds,
                                void (*emit)(const char *line, void *context), void *context);

#endif /* FARM_WARNINGS_H */
