#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "batch.h"
#include "config.h"
#include "runner.h"

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
} BatchOptions;

static void usage(FILE *stream) {
    fprintf(stream,
           "usage: farm-c single [--strategy NAME] [--seed INT] [--config DIR] [--verbose]\n"
           "       farm-c batch --runs N [--strategy NAME]... [--seed INT] [--config DIR]\n"
           "                    [--days N] [--start-money N] [--csv PATH]\n");
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
        } else {
            return false;
        }
    }
    return options->has_runs;
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
    ConfigError config_error;
    if (!config_load_directory(options.config_dir, &config, &config_error) ||
        !config_load_simulation_settings(options.config_dir, &settings, &config_error)) {
        fprintf(stderr, "configuration error: %s\n", config_error.message);
        config_destroy(&config);
        return 1;
    }
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

/* Running per-strategy aggregates, kept alongside the batch instead of
 * materializing every BatchRunResult -- same bounded-memory reasoning as
 * batch_run's own streaming, one level up. */
typedef struct {
    long runs;
    long bankrupt_count;
    double sum_final_money;
    double sum_net_profit;
    double sum_revenue;
    double sum_expenses;
    double sum_days_simulated;
    double sum_idle_days;
} StrategyAgg;

typedef struct {
    FILE *csv;
    StrategyAgg *agg;
    size_t agent_count;
    size_t runs_per_strategy;
    long seen;
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

    size_t agent_index = (size_t)(ctx->seen) / ctx->runs_per_strategy;
    if (agent_index >= ctx->agent_count) agent_index = ctx->agent_count - 1; /* defensive only */
    StrategyAgg *agg = &ctx->agg[agent_index];
    agg->runs++;
    if (r->bankrupt) agg->bankrupt_count++;
    agg->sum_final_money += r->final_money;
    agg->sum_net_profit += r->net_profit;
    agg->sum_revenue += r->total_revenue;
    agg->sum_expenses += r->total_expenses;
    agg->sum_days_simulated += r->days_simulated;
    agg->sum_idle_days += r->idle_days;
    ctx->seen++;
}

static void print_batch_summary(const char *const *names, const StrategyAgg *agg,
                                size_t agent_count) {
    printf("%-24s %8s %10s %16s %16s %10s\n", "strategy", "runs", "bankrupt%",
          "avg_final_money", "avg_net_profit", "avg_days");
    for (size_t i = 0; i < agent_count; i++) {
        const StrategyAgg *a = &agg[i];
        double runs = a->runs > 0 ? (double)a->runs : 1.0;
        printf("%-24s %8ld %9.2f%% %16.2f %16.2f %10.2f\n", names[i], a->runs,
              100.0 * (double)a->bankrupt_count / runs, a->sum_final_money / runs,
              a->sum_net_profit / runs, a->sum_days_simulated / runs);
    }
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
    ConfigError config_error;
    if (!config_load_directory(options.config_dir, &config, &config_error) ||
        !config_load_simulation_settings(options.config_dir, &settings, &config_error)) {
        fprintf(stderr, "configuration error: %s\n", config_error.message);
        config_destroy(&config);
        return 1;
    }
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

    StrategyAgg agg[MAX_BATCH_STRATEGIES];
    memset(agg, 0, sizeof(agg));
    BatchContext context = {csv, agg, agent_count, (size_t)options.runs, 0};

    uint64_t resolved_seed = 0;
    BatchError error;
    bool ok = batch_run(&config, &settings, agents, names, agent_count, (size_t)options.runs,
                        options.seed.has_seed, options.seed.seed, &resolved_seed,
                        on_batch_result, &context, &error);
    if (csv != NULL) fclose(csv);
    if (!ok) {
        fprintf(stderr, "batch run error: %s\n", error.message);
        config_destroy(&config);
        return 1;
    }

    printf("base_seed: %" PRIu64 "\n", resolved_seed);
    printf("strategies: %zu\nruns_per_strategy: %ld\ntotal_runs: %zu\n", agent_count,
          options.runs, agent_count * (size_t)options.runs);
    if (csv != NULL) printf("csv: %s\n", options.csv_path);
    print_batch_summary(names, agg, agent_count);

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
