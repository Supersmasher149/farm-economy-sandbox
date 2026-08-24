/* See include/aggregate.h for scope and for why the derived values live
 * here rather than in each renderer. */
#include "aggregate.h"

#include <string.h>

#include "farm_types.h"

void aggregate_add_run(StrategyAgg *agg, const BatchRunResult *result, size_t crop_count) {
    agg->runs++;
    if (result->bankrupt) agg->bankrupt_count++;
    agg->sum_final_money += result->final_money;
    agg->sum_net_profit += result->net_profit;
    agg->sum_revenue += result->total_revenue;
    agg->sum_expenses += result->total_expenses;
    agg->sum_days_simulated += result->days_simulated;
    agg->sum_idle_days += result->idle_days;
    /* Mean of the per-run ratios, matching metrics/run_results.py's
     * per-run avg_profit_per_day field being averaged by the aggregator --
     * not net_profit/days of the sums, which is a different statistic. */
    if (result->days_simulated > 0)
        agg->sum_profit_per_day += result->net_profit / (double)result->days_simulated;

    for (size_t c = 0; c < crop_count; c++) agg->crop_totals[c] += result->crop_plant_counts[c];
    agg->planted_total += result->total_planted;

    /* metrics/run_results.py's crop_loss_rate/watering_rate: the former is
     * undefined (not 0%) when no crop ever matured this run, so it's
     * averaged only over runs that observed it -- same None-skip-mean
     * semantics as metrics/aggregate_results.py's _MeanAccumulator, done
     * here as a plain running sum/count rather than porting its
     * Neumaier-compensated accumulator (unnecessary precision at the batch
     * sizes this matters for; see the reporting-pipeline scope note in
     * include/warnings.h). */
    if (result->total_harvest_events > 0) {
        agg->sum_crop_loss_rate +=
            100.0 * (double)result->total_crops_lost / (double)result->total_harvest_events;
        agg->crop_loss_rate_count++;
    }
    agg->sum_watering_rate +=
        result->slot_days > 0
            ? 100.0 * (double)result->total_waterings / (double)result->slot_days
            : 0.0;
    if (result->first_upgrade_day != INVALID_DAY) {
        agg->sum_first_upgrade_day += result->first_upgrade_day;
        agg->first_upgrade_count++;
    }
}

void aggregate_finalize(const StrategyAgg *agg, size_t crop_count, double *out_crop_usage_pct,
                        StrategySummary *out) {
    memset(out, 0, sizeof(*out));
    if (out_crop_usage_pct != NULL)
        memset(out_crop_usage_pct, 0, crop_count * sizeof(*out_crop_usage_pct));

    out->runs = agg->runs;
    out->bankrupt_count = agg->bankrupt_count;
    if (agg->runs <= 0) return;

    double runs = (double)agg->runs;
    out->bankruptcy_rate = 100.0 * (double)agg->bankrupt_count / runs;
    out->avg_final_money = agg->sum_final_money / runs;
    out->avg_net_profit = agg->sum_net_profit / runs;
    out->avg_revenue = agg->sum_revenue / runs;
    out->avg_expenses = agg->sum_expenses / runs;
    out->avg_days_simulated = agg->sum_days_simulated / runs;
    out->avg_idle_days = agg->sum_idle_days / runs;
    out->avg_profit_per_day = agg->sum_profit_per_day / runs;
    out->avg_watering_rate = agg->sum_watering_rate / runs;

    out->crop_usage_observed = agg->planted_total > 0;
    if (out->crop_usage_observed && out_crop_usage_pct != NULL) {
        for (size_t c = 0; c < crop_count; c++)
            out_crop_usage_pct[c] = 100.0 * (double)agg->crop_totals[c] / (double)agg->planted_total;
    }

    out->has_crop_loss_rate = agg->crop_loss_rate_count > 0;
    if (out->has_crop_loss_rate)
        out->avg_crop_loss_rate = agg->sum_crop_loss_rate / (double)agg->crop_loss_rate_count;

    out->has_first_upgrade_day = agg->first_upgrade_count > 0;
    if (out->has_first_upgrade_day)
        out->avg_first_upgrade_day = agg->sum_first_upgrade_day / (double)agg->first_upgrade_count;
    out->first_upgrade_rate = 100.0 * (double)agg->first_upgrade_count / runs;
}

void aggregate_to_warning_stats(const StrategySummary *summary, const char *const *crop_ids,
                                const double *crop_usage_pct, size_t crop_count,
                                StrategyWarningStats *out) {
    out->num_runs = summary->runs;
    out->bankruptcy_rate = summary->bankruptcy_rate;
    out->avg_final_money = summary->avg_final_money;

    out->crop_usage_observed = summary->crop_usage_observed;
    out->crop_ids = crop_ids;
    out->crop_usage_pct = crop_usage_pct;
    out->crop_count = crop_count;

    out->has_first_upgrade_day = summary->has_first_upgrade_day;
    out->avg_first_upgrade_day = summary->avg_first_upgrade_day;
    out->first_upgrade_rate = summary->first_upgrade_rate;

    out->has_crop_loss_rate = summary->has_crop_loss_rate;
    out->avg_crop_loss_rate = summary->avg_crop_loss_rate;
    out->avg_watering_rate = summary->avg_watering_rate;
}
