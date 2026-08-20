#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "aggregate.h"
#include "batch.h"
#include "config.h"
#include "dashboard.h"
#include "runner.h"
#include "warnings.h"

#define MAX_BATCH_STRATEGIES 64

typedef struct {
    const char *strategy;
    const char *config_dir;
    RunSeed seed;
    bool verbose;
} SingleOptions;

typedef struct {
    const char *config_dir;
    bool has_runs;
    long runs;
    RunSeed seed;
    const char *strategies[MAX_BATCH_STRATEGIES];
    size_t strategy_count;
    bool has_days;
    long days;
    bool has_start_money;
    double start_money;
    const char *csv_path;
    const char *html_path;
} BatchOptions;

static void usage(FILE *stream) {
    fprintf(stream,
           "usage: farm-c single [--strategy NAME] [--seed INT] [--config DIR] [--verbose]\n"
           "       farm-c batch --runs N [--strategy NAME]... [--seed INT] [--config DIR]\n"
           "                    [--days N] [--start-money N] [--csv PATH] [--html PATH]\n");
}

static bool parse_seed(const char *text, uint64_t *seed) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') return false;
    *seed = (uint64_t)value;
    return true;
}

static bool parse_positive_long(const char *text, long *out) {
    char *end = NULL;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0) return false;
    *out = value;
    return true;
}

static bool parse_double(const char *text, double *out) {
    char *end = NULL;
    errno = 0;
    double value = strtod(text, &end);
    if (errno != 0 || end == text || *end != '\0' || !isfinite(value)) return false;
    *out = value;
    return true;
}

static bool parse_single_args(int argc, char **argv, SingleOptions *options) {
    *options = (SingleOptions){"profit_optimizer", "../config", {false, 0}, false};
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--verbose") == 0) {
            options->verbose = true;
        } else if (strcmp(argv[i], "--strategy") == 0 && i + 1 < argc) {
            options->strategy = argv[++i];
        } else if (strcmp(argv[i], "--config") == 0 && i + 1 < argc) {
            options->config_dir = argv[++i];
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            if (!parse_seed(argv[++i], &options->seed.seed)) return false;
            options->seed.has_seed = true;
        } else {
            return false;
        }
    }
    return true;
}

static bool parse_batch_args(int argc, char **argv, BatchOptions *options) {
    *options = (BatchOptions){
        .config_dir = "../config",
        .has_runs = false,
        .runs = 0,
        .seed = {false, 0},
        .strategy_count = 0,
        .has_days = false,
        .days = 0,
        .has_start_money = false,
        .start_money = 0.0,
        .csv_path = NULL,
        .html_path = NULL,
    };
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--runs") == 0 && i + 1 < argc) {
            if (!parse_positive_long(argv[++i], &options->runs)) return false;
            options->has_runs = true;
        } else if (strcmp(argv[i], "--strategy") == 0 && i + 1 < argc) {
            if (options->strategy_count >= MAX_BATCH_STRATEGIES) return false;
            options->strategies[options->strategy_count++] = argv[++i];
        } else if (strcmp(argv[i], "--config") == 0 && i + 1 < argc) {
            options->config_dir = argv[++i];
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            if (!parse_seed(argv[++i], &options->seed.seed)) return false;
            options->seed.has_seed = true;
        } else if (strcmp(argv[i], "--days") == 0 && i + 1 < argc) {
            if (!parse_positive_long(argv[++i], &options->days)) return false;
            options->has_days = true;
        } else if (strcmp(argv[i], "--start-money") == 0 && i + 1 < argc) {
            if (!parse_double(argv[++i], &options->start_money)) return false;
            options->has_start_money = true;
        } else if (strcmp(argv[i], "--csv") == 0 && i + 1 < argc) {
            options->csv_path = argv[++i];
        } else if (strcmp(argv[i], "--html") == 0 && i + 1 < argc) {
            options->html_path = argv[++i];
        } else {
            return false;
        }
    }
    return options->has_runs;
}

static bool load_config_or_report(const char *dir, ResolvedConfig *config,
                                  SimulationSettings *settings) {
    ConfigError config_error;
    if (!config_load_directory(dir, config, &config_error) ||
        !config_load_simulation_settings(dir, settings, &config_error)) {
        fprintf(stderr, "configuration error: %s\n", config_error.message);
        config_destroy(config);
        return false;
    }
    return true;
}

static void print_day(const FarmState *state, const WeatherDay *weather, void *context) {
    (void)context;
    printf("day %d: money=%.2f temperature=%.2f rainfall=%.3f planted=%zu inventory=%zu\n",
           state->day, state->money, weather->temperature, weather->rainfall,
           state->planted.count, state->inventory_lots.count);
}

static void print_result(const SingleOptions *options, const RunResult *result) {
    const FarmState *state = &result->state;
    printf("strategy: %s\n", options->strategy);
    printf("actual_seed: %" PRIu64 "\n", result->seed);
    printf("days_simulated: %d\nfinal_money: %.17g\nrevenue: %.17g\nexpenses: %.17g\n",
           result->days_simulated, state->money, state->total_revenue, state->total_expenses);
    printf("planted: %d\nharvested: %d\nsold: %d\nidle_days: %d\n",
           state->total_planted, state->total_harvested, state->total_sold, state->idle_days);
    printf("upgrades:");
    for (size_t i = 0; i < state->config->upgrade_count; i++)
        if (state->upgrades_owned[state->config->upgrades[i].id])
            printf(" %s", state->config->upgrades[i].external_id);
    printf("\n");
    printf("bankrupt: %s\n", state->bankrupt ? "true" : "false");
    if (state->bankrupt)
        printf("bankruptcy_reason: %s\n", state->bankruptcy_reason != NULL
                                             ? state->bankruptcy_reason : "unknown");
    printf("lowest_money: %.17g\nhighest_money: %.17g\n",
           state->lowest_money, state->has_highest_money ? state->highest_money : state->money);
    printf("watering: %d/%d\nfertilizer: bought=%d applied=%d\n",
           state->total_waterings, state->slot_days, state->total_fertilizer_bought,
           state->total_fertilizer_applied);
    printf("quality: premium=%d standard=%d processing=%d rejected=%d\n",
           state->quality_harvested[QUALITY_PREMIUM], state->quality_harvested[QUALITY_STANDARD],
           state->quality_harvested[QUALITY_PROCESSING], state->rejected_quality_units);
    printf("processing: jobs=%d revenue=%.17g\ncontracts: completed=%d failed=%d penalties=%.17g reputation=%.17g\n",
           state->total_processed, state->processing_revenue, state->contracts_completed,
           state->contracts_failed, state->contract_penalties, state->reputation);
    printf("revenue_by_channel:");
    for (size_t i = 0; i < state->config->channel_count; i++)
        printf(" %s=%.17g", state->config->channels[i].external_id, state->revenue_by_channel[i]);
    printf(" contract=%.17g\n", state->contract_channel_revenue);
}

static int cmd_single(int argc, char **argv) {
    SingleOptions options;
    if (!parse_single_args(argc, argv, &options)) {
        usage(stderr);
        return 2;
    }
    const Agent *agent = agent_registry_find(options.strategy);
    if (agent == NULL) {
        fprintf(stderr, "unknown strategy: %s\n", options.strategy);
        return 2;
    }
    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(options.config_dir, &config, &settings)) return 1;
    RunResult result = {0};
    RunnerError error;
    bool ok = runner_run_single(&config, &settings, agent, options.seed,
                                options.verbose ? print_day : NULL, NULL, &result, &error);
    if (!ok) {
        fprintf(stderr, "run error: %s\n", error.message);
        runner_run_result_destroy(&result);
        config_destroy(&config);
        return 1;
    }
    print_result(&options, &result);
    runner_run_result_destroy(&result);
    config_destroy(&config);
    return 0;
}

/* StrategyAgg and every value derived from it now live in
 * include/aggregate.h -- the terminal table below, the balance warnings,
 * and the HTML dashboard all read the same accumulators, so an average
 * cannot mean one thing on stdout and another on the page. */

typedef struct {
    FILE *csv;
    DashboardWriter *html;
    StrategyAgg *agg;
    const char *const *names;
    size_t agent_count;
    const ResolvedConfig *config;
} BatchContext;

static void write_csv_row(FILE *csv, const BatchRunResult *r) {
    fprintf(csv,
           "%s,%" PRIu64 ",%d,%.17g,%.17g,%.17g,%.17g,%d,%d,%d,%d,%s,",
           r->strategy, r->seed, r->days_simulated, r->final_money, r->total_revenue,
           r->total_expenses, r->net_profit, r->total_planted, r->total_harvested,
           r->total_sold, r->idle_days, r->bankrupt ? "true" : "false");
    if (r->bankruptcy_day == INVALID_DAY) {
        fputc(',', csv);
    } else {
        fprintf(csv, "%d,", r->bankruptcy_day);
    }
    fprintf(csv, "%.17g,%.17g,%d,%d,%d,%d,%d,%.17g,%.17g\n", r->lowest_money, r->highest_money,
           r->total_waterings, r->total_fertilizer_applied, r->total_processed,
           r->contracts_completed, r->contracts_failed, r->contract_penalties, r->reputation);
}

static void on_batch_result(const BatchRunResult *r, void *context) {
    BatchContext *ctx = context;
    if (ctx->csv != NULL) write_csv_row(ctx->csv, r);
    dashboard_add_run(ctx->html, r); /* no-op when --html was not passed */

    /* Match by the strategy name batch_run reports, not by run position:
     * r->strategy is the same names[] pointer batch_run was given (see
     * batch.c's snapshot_result), so this doesn't need to re-derive
     * batch_run's agent-major traversal order the way dividing a running
     * count by runs-per-strategy did. */
    size_t agent_index = ctx->agent_count > 0 ? ctx->agent_count - 1 : 0;
    for (size_t i = 0; i < ctx->agent_count; i++) {
        if (strcmp(ctx->names[i], r->strategy) == 0) {
            agent_index = i;
            break;
        }
    }
    aggregate_add_run(&ctx->agg[agent_index], r, ctx->config->crop_count);
}

static void print_batch_summary(const char *const *names, const StrategyAgg *agg,
                                size_t agent_count, size_t crop_count) {
    printf("%-24s %8s %10s %16s %16s %10s\n", "strategy", "runs", "bankrupt%",
          "avg_final_money", "avg_net_profit", "avg_days");
    for (size_t i = 0; i < agent_count; i++) {
        StrategySummary s;
        aggregate_finalize(&agg[i], crop_count, NULL, &s);
        printf("%-24s %8ld %9.2f%% %16.2f %16.2f %10.2f\n", names[i], s.runs, s.bankruptcy_rate,
              s.avg_final_money, s.avg_net_profit, s.avg_days_simulated);
    }
}

/* Balance warnings on stdout, from the same accumulators the dashboard
 * reads -- ../metrics/warnings.py is what `main.py batch`'s report leads
 * with, and a batch that only prints averages buries the finding. */
static void emit_stdout_warning(const char *line, void *context) {
    long *count = context;
    if ((*count)++ == 0) printf("\n");
    printf("! %s\n", line);
}

static void print_batch_warnings(const ResolvedConfig *config, const char *const *names,
                                 const StrategyAgg *agg, size_t agent_count, int total_days,
                                 double start_money) {
    size_t crop_count = config->crop_count;
    double *crop_pct = crop_count > 0 ? calloc(crop_count, sizeof(double)) : NULL;
    const char **crop_ids = crop_count > 0 ? calloc(crop_count, sizeof(char *)) : NULL;
    if (crop_count > 0 && (crop_pct == NULL || crop_ids == NULL)) {
        free(crop_pct);
        free(crop_ids);
        return;
    }
    for (size_t c = 0; c < crop_count; c++) {
        const ItemDef *item = config_find_item(config, config->crops[c].item_id);
        crop_ids[c] = item != NULL && item->external_id != NULL ? item->external_id : "?";
    }

    long emitted = 0;
    for (size_t i = 0; i < agent_count; i++) {
        StrategySummary s;
        aggregate_finalize(&agg[i], crop_count, crop_pct, &s);
        StrategyWarningStats stats;
        aggregate_to_warning_stats(&s, (const char *const *)crop_ids, crop_pct, crop_count,
                                   &stats);
        warnings_evaluate_strategy(names[i], &stats, total_days, start_money,
                                   &WARNING_DEFAULT_THRESHOLDS, emit_stdout_warning, &emitted);
    }
    free(crop_pct);
    free(crop_ids);
}

static int cmd_batch(int argc, char **argv) {
    BatchOptions options;
    if (!parse_batch_args(argc, argv, &options)) {
        usage(stderr);
        return 2;
    }

    const Agent *agents[MAX_BATCH_STRATEGIES];
    const char *names[MAX_BATCH_STRATEGIES];
    size_t agent_count = 0;
    if (options.strategy_count == 0) {
        for (const AgentRegistryEntry *entry = AGENT_REGISTRY; entry->strategy_name != NULL;
            entry++) {
            agents[agent_count] = entry->agent;
            names[agent_count] = entry->strategy_name;
            agent_count++;
        }
    } else {
        for (size_t i = 0; i < options.strategy_count; i++) {
            const Agent *agent = agent_registry_find(options.strategies[i]);
            if (agent == NULL) {
                fprintf(stderr, "unknown strategy: %s\n", options.strategies[i]);
                return 2;
            }
            agents[agent_count] = agent;
            names[agent_count] = options.strategies[i];
            agent_count++;
        }
    }

    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(options.config_dir, &config, &settings)) return 1;
    if (options.has_days) settings.days = (int)options.days;
    if (options.has_start_money) settings.start_money = options.start_money;

    FILE *csv = NULL;
    if (options.csv_path != NULL) {
        csv = fopen(options.csv_path, "w");
        if (csv == NULL) {
            fprintf(stderr, "could not open --csv path for writing: %s\n", options.csv_path);
            config_destroy(&config);
            return 1;
        }
        fprintf(csv,
               "strategy,seed,days_simulated,final_money,total_revenue,total_expenses,"
               "net_profit,total_planted,total_harvested,total_sold,idle_days,bankrupt,"
               "bankruptcy_day,lowest_money,highest_money,total_waterings,"
               "total_fertilizer_applied,total_processed,contracts_completed,"
               "contracts_failed,contract_penalties,reputation\n");
    }

    DashboardWriter *html = NULL;
    if (options.html_path != NULL) {
        html = dashboard_open(options.html_path, &config, names, agent_count, &settings);
        if (html == NULL) {
            fprintf(stderr, "could not open --html path for writing: %s\n", options.html_path);
            if (csv != NULL) fclose(csv);
            config_destroy(&config);
            return 1;
        }
    }

    StrategyAgg agg[MAX_BATCH_STRATEGIES];
    memset(agg, 0, sizeof(agg));
    /* One flat allocation carved into per-strategy slices, which is the
     * arrangement StrategyAgg.crop_totals documents. calloc of a zero-sized
     * request is implementation-defined, so a crop-less config keeps NULL
     * slices -- safe, because aggregate_add_run's loop then runs zero
     * times. */
    long *crop_totals = NULL;
    if (config.crop_count > 0) {
        crop_totals = calloc(agent_count * config.crop_count, sizeof(long));
        if (crop_totals == NULL) {
            fprintf(stderr, "out of memory allocating per-strategy crop totals\n");
            if (csv != NULL) fclose(csv);
            dashboard_close(html, agg, agent_count, settings.days, settings.start_money);
            config_destroy(&config);
            return 1;
        }
        for (size_t i = 0; i < agent_count; i++)
            agg[i].crop_totals = crop_totals + i * config.crop_count;
    }

    BatchContext context = {csv, html, agg, names, agent_count, &config};

    uint64_t resolved_seed = 0;
    BatchError error;
    bool ok = batch_run(&config, &settings, agents, names, agent_count, (size_t)options.runs,
                        options.seed.has_seed, options.seed.seed, &resolved_seed,
                        on_batch_result, &context, &error);
    if (csv != NULL) fclose(csv);
    /* Closed on both paths: a partially written page is still valid HTML
     * for the runs that did complete, and leaving the file open would leak
     * the writer. */
    bool html_ok = dashboard_close(html, agg, agent_count, settings.days, settings.start_money);
    if (!ok) {
        fprintf(stderr, "batch run error: %s\n", error.message);
        free(crop_totals);
        config_destroy(&config);
        return 1;
    }
    if (!html_ok) {
        fprintf(stderr, "failed writing --html report: %s\n", options.html_path);
        free(crop_totals);
        config_destroy(&config);
        return 1;
    }

    printf("base_seed: %" PRIu64 "\n", resolved_seed);
    printf("strategies: %zu\nruns_per_strategy: %ld\ntotal_runs: %zu\n", agent_count,
          options.runs, agent_count * (size_t)options.runs);
    if (csv != NULL) printf("csv: %s\n", options.csv_path);
    if (html != NULL) printf("html: %s\n", options.html_path);
    print_batch_summary(names, agg, agent_count, config.crop_count);
    print_batch_warnings(&config, names, agg, agent_count, settings.days, settings.start_money);

    free(crop_totals);
    config_destroy(&config);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        usage(stderr);
        return 2;
    }
    if (strcmp(argv[1], "single") == 0) return cmd_single(argc, argv);
    if (strcmp(argv[1], "batch") == 0) return cmd_batch(argc, argv);
    usage(stderr);
    return 2;
}
