/* See include/warnings.h for scope. Ported line-for-line from
 * ../metrics/warnings.py:evaluate_warnings and runaway_money_multiple. */
#include "warnings.h"

#include <stdio.h>

const WarningThresholds WARNING_DEFAULT_THRESHOLDS = {
    .dominant_crop_pct = 70,
    .dead_crop_pct = 5,
    .high_bankruptcy_pct = 20,
    .upgrade_too_fast_day = 5,
    .upgrade_too_slow_fraction = 0.9,
    .runaway_money_multiple = 20,
    .runaway_reference_days = 30,
    .high_crop_loss_rate_pct = 30,
};

double warnings_runaway_money_multiple(int total_days, const WarningThresholds *thresholds) {
    int reference_days = thresholds->runaway_reference_days != 0
                             ? thresholds->runaway_reference_days : 1;
    int days = total_days != 0 ? total_days : reference_days;
    int clamped_days = days > 1 ? days : 1;
    return thresholds->runaway_money_multiple * (double)clamped_days / (double)reference_days;
}

void warnings_evaluate_strategy(const char *strategy, const StrategyWarningStats *stats,
                                int total_days, double start_money,
                                const WarningThresholds *thresholds,
                                void (*emit)(const char *line, void *context), void *context) {
    char line[512];
    double runaway_multiple = warnings_runaway_money_multiple(total_days, thresholds);

    if (!stats->crop_usage_observed) {
        if (stats->num_runs > 0) {
            snprintf(line, sizeof(line), "[%s] No crops were planted in any run.", strategy);
            emit(line, context);
        }
    } else {
        for (size_t i = 0; i < stats->crop_count; i++) {
            double pct = stats->crop_usage_pct[i];
            if (pct > thresholds->dominant_crop_pct) {
                snprintf(line, sizeof(line),
                        "[%s] Dominant crop: '%s' is %.2f%% of plantings (> %g%%).",
                        strategy, stats->crop_ids[i], pct, thresholds->dominant_crop_pct);
                emit(line, context);
            } else if (pct < thresholds->dead_crop_pct) {
                snprintf(line, sizeof(line),
                        "[%s] Dead crop: '%s' is only %.2f%% of plantings (< %g%%).",
                        strategy, stats->crop_ids[i], pct, thresholds->dead_crop_pct);
                emit(line, context);
            }
        }
    }

    if (stats->bankruptcy_rate > thresholds->high_bankruptcy_pct) {
        snprintf(line, sizeof(line), "[%s] High bankruptcy rate: %.2f%% (> %g%%).", strategy,
                stats->bankruptcy_rate, thresholds->high_bankruptcy_pct);
        emit(line, context);
    }

    if (stats->has_first_upgrade_day &&
        stats->avg_first_upgrade_day <= thresholds->upgrade_too_fast_day) {
        snprintf(line, sizeof(line),
                "[%s] First upgrade purchased very early on average (day %.2f).", strategy,
                stats->avg_first_upgrade_day);
        emit(line, context);
    }
    double max_unreached_rate = thresholds->upgrade_too_slow_fraction * 100.0;
    if (stats->first_upgrade_rate <= 100.0 - max_unreached_rate) {
        snprintf(line, sizeof(line),
                "[%s] First upgrade is rarely reached: %.2f%% of runs purchased one.", strategy,
                stats->first_upgrade_rate);
        emit(line, context);
    }

    if (stats->avg_final_money > start_money * runaway_multiple) {
        snprintf(line, sizeof(line),
                "[%s] Possible runaway economy: avg final money %.2f is %.1fx+ starting money "
                "over %d days.",
                strategy, stats->avg_final_money, runaway_multiple, total_days);
        emit(line, context);
    }

    if (stats->has_crop_loss_rate && stats->avg_crop_loss_rate > thresholds->high_crop_loss_rate_pct) {
        /* avg_watering_rate is formatted with a fixed 2 decimals rather than
         * Python's bare-float display (which drops trailing zeros, e.g.
         * "25.6" instead of "25.60") -- a cosmetic difference only, never
         * ambiguous or wrong, and consistent with every other rounded
         * percentage in this message set. */
        snprintf(line, sizeof(line),
                "[%s] High crop loss rate: %.2f%% of matured crops are lost (> %g%%). Watering "
                "diligence for this strategy averages %.2f%% of plot-days.",
                strategy, stats->avg_crop_loss_rate, thresholds->high_crop_loss_rate_pct,
                stats->avg_watering_rate);
        emit(line, context);
    }
}
