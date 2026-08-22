/* Threshold and aggregation tests for src/warnings.c + src/aggregate.c.
 *
 * Neither module had a test of its own: `test_dashboard.c` checks that the
 * page agrees with `aggregate_finalize`, which catches a renderer that
 * disagrees with the aggregator but not an aggregator that is wrong -- both
 * would move together. These pin the numbers themselves.
 *
 * The emphasis is on **boundaries and undefined-vs-zero**, because that is
 * where a port of ../metrics/warnings.py silently diverges:
 *
 *   * Every threshold comparison is either strict or non-strict in the
 *     Python source, and flipping one moves a warning by exactly one run in
 *     a thousand-run batch -- invisible unless a test sits on the boundary.
 *   * A rate no run ever observed is *undefined*, not 0%. Folding it into a
 *     mean as zero drags the average down and is the exact failure
 *     metrics/run_results.py's None discipline exists to prevent.
 */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "aggregate.h"
#include "farm_types.h"
#include "warnings.h"

static int failures;

static void report(bool ok, const char *name) {
    if (!ok) {
        failures++;
        printf("FAIL %s\n", name);
    }
}

/* --- warning collection --------------------------------------------------- */

#define MAX_LINES 16
#define MAX_LINE 512

typedef struct {
    char lines[MAX_LINES][MAX_LINE];
    size_t count;
} Emitted;

static void collect(const char *line, void *context) {
    Emitted *out = context;
    if (out->count >= MAX_LINES) return;
    snprintf(out->lines[out->count], MAX_LINE, "%s", line);
    out->count++;
}

/* Substring match: the assertions below are about *which* rule fired, not
 * about exact message wording, which is display-only. */
static bool emitted_has(const Emitted *emitted, const char *needle) {
    for (size_t i = 0; i < emitted->count; i++) {
        if (strstr(emitted->lines[i], needle) != NULL) return true;
    }
    return false;
}

/* A cohort that trips no rule, as the base for one-field perturbations.
 * Every field is deliberately mid-range so that a test which moves one
 * value is unambiguously about that value. */
static const char *const CROP_IDS[3] = {"quickweed", "sunbeet", "purplehaze"};

static void baseline_stats(StrategyWarningStats *stats, double *pct) {
    pct[0] = 40.0;
    pct[1] = 35.0;
    pct[2] = 25.0;
    stats->num_runs = 100;
    stats->bankruptcy_rate = 5.0;
    stats->avg_final_money = 500.0;
    stats->crop_usage_observed = true;
    stats->crop_ids = CROP_IDS;
    stats->crop_usage_pct = pct;
    stats->crop_count = 3;
    stats->has_first_upgrade_day = true;
    stats->avg_first_upgrade_day = 40.0;
    stats->first_upgrade_rate = 80.0;
    stats->has_crop_loss_rate = true;
    stats->avg_crop_loss_rate = 10.0;
    stats->avg_watering_rate = 50.0;
}

static Emitted evaluate(const StrategyWarningStats *stats) {
    Emitted emitted = {.count = 0};
    warnings_evaluate_strategy("probe", stats, 365, 100.0, &WARNING_DEFAULT_THRESHOLDS, collect,
                               &emitted);
    return emitted;
}

/* Evaluate and ask whether one rule fired -- the shape almost every
 * boundary assertion below wants. */
static bool fired(const StrategyWarningStats *stats, const char *needle) {
    Emitted emitted = evaluate(stats);
    return emitted_has(&emitted, needle);
}

static void test_quiet_baseline(void) {
    StrategyWarningStats stats;
    double pct[3];
    baseline_stats(&stats, pct);
    Emitted emitted = evaluate(&stats);
    if (emitted.count != 0) {
        failures++;
        printf("FAIL baseline cohort should trip no rule, got %zu:\n", emitted.count);
        for (size_t i = 0; i < emitted.count; i++) printf("     %s\n", emitted.lines[i]);
    }
}

/* Each threshold, tested on both sides of the boundary *and* exactly on it.
 * The on-the-boundary case is the one that pins strict-vs-non-strict, which
 * is the only thing distinguishing `>` from `>=` in the Python source. */
static void test_threshold_boundaries(void) {
    StrategyWarningStats stats;
    double pct[3];

    /* Dominant crop: `pct > 70` -- strictly greater. */
    baseline_stats(&stats, pct);
    pct[0] = 70.0;
    pct[1] = 20.0;
    pct[2] = 10.0;
    report(!fired(&stats, "Dominant crop"),
           "dominant crop does not fire exactly at 70%");
    pct[0] = 70.000001;
    report(fired(&stats, "Dominant crop"), "dominant crop fires just above 70%");

    /* Dead crop: `pct < 5` -- strictly less. */
    baseline_stats(&stats, pct);
    pct[0] = 90.0;
    pct[1] = 5.0;
    pct[2] = 5.0;
    report(!fired(&stats, "Dead crop"), "dead crop does not fire exactly at 5%");
    pct[1] = 4.999999;
    report(fired(&stats, "Dead crop"), "dead crop fires just below 5%");

    /* The two are an if/else-if chain in the Python source, so one crop can
     * never produce both -- and with a dominant crop present the others are
     * usually dead, which is why this pairing is worth pinning. */
    baseline_stats(&stats, pct);
    pct[0] = 96.0;
    pct[1] = 2.0;
    pct[2] = 2.0;
    Emitted both = evaluate(&stats);
    report(emitted_has(&both, "Dominant crop: 'quickweed'"), "dominant fires for the big crop");
    report(emitted_has(&both, "Dead crop: 'sunbeet'"), "dead fires for the small crop");
    report(!emitted_has(&both, "Dead crop: 'quickweed'"),
           "a crop is never both dominant and dead");

    /* Bankruptcy: `rate > 20` -- strictly greater. */
    baseline_stats(&stats, pct);
    stats.bankruptcy_rate = 20.0;
    report(!fired(&stats, "High bankruptcy"),
           "bankruptcy does not fire exactly at 20%");
    stats.bankruptcy_rate = 20.000001;
    report(fired(&stats, "High bankruptcy"), "bankruptcy fires just above 20%");

    /* First upgrade too fast: `avg_day <= 5` -- non-strict, unlike the
     * others. Getting this one backwards is entirely invisible without a
     * test sitting exactly on day 5. */
    baseline_stats(&stats, pct);
    stats.avg_first_upgrade_day = 5.0;
    report(fired(&stats, "very early"), "upgrade-too-fast fires exactly at day 5");
    stats.avg_first_upgrade_day = 5.000001;
    report(!fired(&stats, "very early"),
           "upgrade-too-fast does not fire just after day 5");

    /* Rarely reached: `first_upgrade_rate <= 100 - 0.9*100` == `<= 10`,
     * also non-strict. */
    baseline_stats(&stats, pct);
    stats.first_upgrade_rate = 10.0;
    report(fired(&stats, "rarely reached"),
           "upgrade-rarely-reached fires exactly at 10%");
    stats.first_upgrade_rate = 10.000001;
    report(!fired(&stats, "rarely reached"),
           "upgrade-rarely-reached does not fire just above 10%");

    /* Crop loss: `rate > 30` -- strictly greater. */
    baseline_stats(&stats, pct);
    stats.avg_crop_loss_rate = 30.0;
    report(!fired(&stats, "High crop loss"),
           "crop loss does not fire exactly at 30%");
    stats.avg_crop_loss_rate = 30.000001;
    report(fired(&stats, "High crop loss"), "crop loss fires just above 30%");
}

/* The runaway rule scales its multiple by the run length, so the boundary
 * is a computed value rather than a constant -- and the scaling itself has
 * two degenerate inputs the Python guards against. */
static void test_runaway_scaling_and_boundary(void) {
    const WarningThresholds *t = &WARNING_DEFAULT_THRESHOLDS;

    /* 20x over the 30-day reference window, scaled linearly by length. */
    report(warnings_runaway_money_multiple(30, t) == 20.0, "runaway multiple at the reference 30d");
    report(warnings_runaway_money_multiple(60, t) == 40.0, "runaway multiple doubles at 60d");
    report(warnings_runaway_money_multiple(365, t) == 20.0 * 365.0 / 30.0,
           "runaway multiple scales at 365d");

    /* total_days == 0 falls back to the reference window rather than
     * collapsing the multiple to zero and flagging every strategy. */
    report(warnings_runaway_money_multiple(0, t) == 20.0, "zero total_days falls back to reference");
    /* A 1-day run must not scale *below* the 1-day floor. */
    report(warnings_runaway_money_multiple(1, t) == 20.0 / 30.0, "1-day run clamps to one day");

    /* A zero reference window would divide by zero; it resolves to 1. */
    WarningThresholds zero_reference = WARNING_DEFAULT_THRESHOLDS;
    zero_reference.runaway_reference_days = 0;
    report(isfinite(warnings_runaway_money_multiple(30, &zero_reference)),
           "zero reference window does not divide by zero");

    /* The rule itself: `avg_final_money > start_money * multiple`, strict. */
    StrategyWarningStats stats;
    double pct[3];
    baseline_stats(&stats, pct);
    double multiple = warnings_runaway_money_multiple(365, t);
    stats.avg_final_money = 100.0 * multiple;
    report(!fired(&stats, "runaway"), "runaway does not fire exactly at the bound");
    stats.avg_final_money = nextafter(100.0 * multiple, 1e18);
    report(fired(&stats, "runaway"), "runaway fires one ulp above the bound");
}

/* "No crops planted" is gated on the cohort having runs at all, so an empty
 * cohort stays silent instead of reporting a finding about zero runs. */
static void test_unobserved_crop_usage(void) {
    StrategyWarningStats stats;
    double pct[3];

    baseline_stats(&stats, pct);
    stats.crop_usage_observed = false;
    report(fired(&stats, "No crops were planted"),
           "unobserved crop usage reports no plantings");
    report(!fired(&stats, "Dominant crop"),
           "unobserved crop usage skips the per-crop rules entirely");

    stats.num_runs = 0;
    report(!fired(&stats, "No crops were planted"),
           "an empty cohort reports nothing about plantings");

    /* An undefined crop-loss rate must not be treated as 0% and must not be
     * treated as a triggering value either -- it is simply not evaluated. */
    baseline_stats(&stats, pct);
    stats.has_crop_loss_rate = false;
    stats.avg_crop_loss_rate = 999.0;
    report(!fired(&stats, "High crop loss"),
           "an unobserved crop-loss rate is not evaluated");

    baseline_stats(&stats, pct);
    stats.has_first_upgrade_day = false;
    stats.avg_first_upgrade_day = 1.0;
    report(!fired(&stats, "very early"),
           "an unobserved first-upgrade day is not evaluated");
}

/* --- aggregation ---------------------------------------------------------- */

static BatchRunResult make_run(const int *crop_counts) {
    BatchRunResult run;
    memset(&run, 0, sizeof(run));
    run.strategy = "probe";
    run.days_simulated = 100;
    run.bankruptcy_day = INVALID_DAY;
    run.first_upgrade_day = INVALID_DAY;
    run.crop_plant_counts = crop_counts;
    return run;
}

static void test_empty_cohort_is_no_data_not_zero(void) {
    StrategyAgg agg;
    memset(&agg, 0, sizeof(agg));
    long crop_totals[3] = {0, 0, 0};
    agg.crop_totals = crop_totals;

    double pct[3] = {1.0, 1.0, 1.0}; /* pre-dirtied: finalize must clear it */
    StrategySummary summary;
    aggregate_finalize(&agg, 3, pct, &summary);

    report(summary.runs == 0, "empty cohort reports zero runs");
    report(summary.bankruptcy_rate == 0.0, "empty cohort does not divide by zero");
    report(!summary.crop_usage_observed, "empty cohort has no observed crop usage");
    report(!summary.has_crop_loss_rate, "empty cohort has no crop-loss rate");
    report(!summary.has_first_upgrade_day, "empty cohort has no first-upgrade day");
    report(pct[0] == 0.0 && pct[1] == 0.0 && pct[2] == 0.0,
           "finalize clears the caller's crop-percentage buffer");
}

/* The header comment on aggregate_add_run calls this out explicitly:
 * avg_profit_per_day is the mean of each run's ratio, not the ratio of the
 * summed profit to the summed days. The two differ whenever run lengths
 * differ, which is exactly what a bankruptcy cohort looks like. */
static void test_profit_per_day_is_mean_of_ratios(void) {
    StrategyAgg agg;
    memset(&agg, 0, sizeof(agg));
    long crop_totals[1] = {0};
    agg.crop_totals = crop_totals;
    int counts[1] = {0};

    BatchRunResult short_run = make_run(counts);
    short_run.days_simulated = 10;
    short_run.net_profit = 100.0; /* 10/day */
    aggregate_add_run(&agg, &short_run, 1);

    BatchRunResult long_run = make_run(counts);
    long_run.days_simulated = 100;
    long_run.net_profit = 100.0; /* 1/day */
    aggregate_add_run(&agg, &long_run, 1);

    StrategySummary summary;
    aggregate_finalize(&agg, 1, NULL, &summary);

    /* Mean of ratios: (10 + 1) / 2 == 5.5.
     * Ratio of sums would be 200/110 == 1.818..., a different statistic. */
    report(summary.avg_profit_per_day == 5.5, "avg_profit_per_day is the mean of per-run ratios");
    report(fabs(summary.avg_profit_per_day - 200.0 / 110.0) > 1e-9,
           "avg_profit_per_day is not the ratio of the sums");

    /* A zero-day run contributes nothing rather than dividing by zero. */
    BatchRunResult empty_run = make_run(counts);
    empty_run.days_simulated = 0;
    empty_run.net_profit = 50.0;
    aggregate_add_run(&agg, &empty_run, 1);
    aggregate_finalize(&agg, 1, NULL, &summary);
    report(isfinite(summary.avg_profit_per_day), "a zero-day run does not divide by zero");
}

/* An undefined rate must be averaged over the runs that observed it, not
 * over every run in the cohort. */
static void test_undefined_rates_skip_the_mean(void) {
    StrategyAgg agg;
    memset(&agg, 0, sizeof(agg));
    long crop_totals[1] = {0};
    agg.crop_totals = crop_totals;
    int counts[1] = {0};

    /* One run harvested and lost half of it; three never harvested at all. */
    BatchRunResult harvested = make_run(counts);
    harvested.total_harvest_events = 10;
    harvested.total_crops_lost = 5; /* 50% */
    aggregate_add_run(&agg, &harvested, 1);
    for (int i = 0; i < 3; i++) {
        BatchRunResult barren = make_run(counts);
        aggregate_add_run(&agg, &barren, 1);
    }

    StrategySummary summary;
    aggregate_finalize(&agg, 1, NULL, &summary);
    report(summary.has_crop_loss_rate, "crop-loss rate is observed");
    /* Averaged over 1 observing run, not diluted across 4 (which would give
     * 12.5% and hide a cohort losing half its harvest). */
    report(summary.avg_crop_loss_rate == 50.0, "crop-loss rate averages only over observing runs");

    /* Same discipline for first_upgrade_day, with its own separate count:
     * the average is over buyers, while the *rate* is over the whole cohort. */
    memset(&agg, 0, sizeof(agg));
    agg.crop_totals = crop_totals;
    BatchRunResult bought = make_run(counts);
    bought.first_upgrade_day = 20;
    aggregate_add_run(&agg, &bought, 1);
    for (int i = 0; i < 3; i++) {
        BatchRunResult never = make_run(counts);
        aggregate_add_run(&agg, &never, 1);
    }
    aggregate_finalize(&agg, 1, NULL, &summary);
    report(summary.has_first_upgrade_day, "first-upgrade day is observed");
    report(summary.avg_first_upgrade_day == 20.0, "first-upgrade day averages only over buyers");
    report(summary.first_upgrade_rate == 25.0, "first-upgrade rate is over the whole cohort");
}

static void test_crop_usage_percentages(void) {
    StrategyAgg agg;
    memset(&agg, 0, sizeof(agg));
    long crop_totals[3] = {0, 0, 0};
    agg.crop_totals = crop_totals;

    int counts_a[3] = {6, 3, 1};
    BatchRunResult run_a = make_run(counts_a);
    run_a.total_planted = 10;
    aggregate_add_run(&agg, &run_a, 3);

    int counts_b[3] = {4, 5, 1};
    BatchRunResult run_b = make_run(counts_b);
    run_b.total_planted = 10;
    aggregate_add_run(&agg, &run_b, 3);

    double pct[3];
    StrategySummary summary;
    aggregate_finalize(&agg, 3, pct, &summary);

    report(summary.crop_usage_observed, "crop usage observed once something was planted");
    report(pct[0] == 50.0 && pct[1] == 40.0 && pct[2] == 10.0,
           "crop percentages are shares of the cohort's total plantings");
    report(fabs(pct[0] + pct[1] + pct[2] - 100.0) < 1e-9, "crop percentages sum to 100");

    /* Runs happened, but nothing was ever planted: the percentages are
     * undefined rather than zero, and the flag is what says so. */
    memset(&agg, 0, sizeof(agg));
    long empty_totals[3] = {0, 0, 0};
    agg.crop_totals = empty_totals;
    int zero_counts[3] = {0, 0, 0};
    BatchRunResult barren = make_run(zero_counts);
    aggregate_add_run(&agg, &barren, 3);
    aggregate_finalize(&agg, 3, pct, &summary);
    report(summary.runs == 1, "barren cohort still counts its run");
    report(!summary.crop_usage_observed, "nothing planted means crop usage is unobserved");
}

/* The adapter is a pure field copy, but it is the seam between the two
 * modules: a field left unassigned here reaches the warning rules as
 * whatever was on the stack. */
static void test_warning_stats_adapter_copies_every_field(void) {
    StrategySummary summary;
    memset(&summary, 0, sizeof(summary));
    summary.runs = 7;
    summary.bankruptcy_rate = 12.5;
    summary.avg_final_money = 321.0;
    summary.crop_usage_observed = true;
    summary.has_first_upgrade_day = true;
    summary.avg_first_upgrade_day = 9.0;
    summary.first_upgrade_rate = 42.0;
    summary.has_crop_loss_rate = true;
    summary.avg_crop_loss_rate = 33.0;
    summary.avg_watering_rate = 66.0;

    double pct[3] = {1.0, 2.0, 97.0};
    StrategyWarningStats stats;
    memset(&stats, 0xA5, sizeof(stats)); /* poisoned: every field must be written */
    aggregate_to_warning_stats(&summary, CROP_IDS, pct, 3, &stats);

    report(stats.num_runs == 7 && stats.bankruptcy_rate == 12.5 && stats.avg_final_money == 321.0,
           "adapter copies the cohort scalars");
    report(stats.crop_usage_observed && stats.crop_ids == CROP_IDS &&
               stats.crop_usage_pct == pct && stats.crop_count == 3,
           "adapter passes the crop arrays through by reference");
    report(stats.has_first_upgrade_day && stats.avg_first_upgrade_day == 9.0 &&
               stats.first_upgrade_rate == 42.0,
           "adapter copies the upgrade fields");
    report(stats.has_crop_loss_rate && stats.avg_crop_loss_rate == 33.0 &&
               stats.avg_watering_rate == 66.0,
           "adapter copies the rate fields");
}

int main(void) {
    test_quiet_baseline();
    test_threshold_boundaries();
    test_runaway_scaling_and_boundary();
    test_unobserved_crop_usage();

    test_empty_cohort_is_no_data_not_zero();
    test_profit_per_day_is_mean_of_ratios();
    test_undefined_rates_skip_the_mean();
    test_crop_usage_percentages();
    test_warning_stats_adapter_copies_every_field();

    if (failures > 0) {
        printf("%d failed\n", failures);
        return 1;
    }
    puts("reporting tests passed");
    return 0;
}
