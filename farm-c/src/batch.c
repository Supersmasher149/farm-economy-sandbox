#include "batch.h"

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "engine.h"
#include "rng.h"
#include "runner.h"

static void set_error(BatchError *error, BatchErrorCode code, const char *strategy,
                      uint64_t seed, const char *message) {
    if (error == NULL) return;
    error->code = code;
    error->strategy = strategy;
    error->seed = seed;
    snprintf(error->message, sizeof(error->message), "%s", message);
}

/* Same /dev/urandom-with-fallback shape as runner.c's fresh_seed (kept as a
 * separate copy rather than shared: runner.c's is file-static, and this
 * port's "no cross-file coupling beyond the header" style for small
 * one-line helpers -- see e.g. pyfloat.c/vec_util.c -- treats a four-line
 * duplicate as cheaper than adding a new exported entry point for it). */
static bool fresh_seed(uint64_t *seed) {
    int fd = open("/dev/urandom", O_RDONLY);
    if (fd >= 0) {
        ssize_t got = read(fd, seed, sizeof(*seed));
        close(fd);
        if (got == (ssize_t)sizeof(*seed)) return true;
    }
    *seed = (uint64_t)time(NULL) ^ ((uint64_t)(uintptr_t)seed << 17);
    return true;
}

static BatchRunResult snapshot_result(const char *strategy, uint64_t seed,
                                      const RunResult *run) {
    const FarmState *state = &run->state;
    BatchRunResult out = {0};
    out.strategy = strategy;
    out.seed = seed;
    out.days_simulated = run->days_simulated;

    out.final_money = state->money;
    out.total_revenue = state->total_revenue;
    out.total_expenses = state->total_expenses;
    out.net_profit = state->total_revenue - state->total_expenses;

    out.total_planted = state->total_planted;
    out.total_harvested = state->total_harvested;
    out.total_sold = state->total_sold;
    out.idle_days = state->idle_days;

    out.bankrupt = state->bankrupt;
    out.bankruptcy_day = state->bankruptcy_day;

    out.lowest_money = state->lowest_money;
    out.highest_money = state->has_highest_money ? state->highest_money : state->money;

    out.total_waterings = state->total_waterings;
    out.total_fertilizer_applied = state->total_fertilizer_applied;
    out.total_processed = state->total_processed;

    out.contracts_completed = state->contracts_completed;
    out.contracts_failed = state->contracts_failed;
    out.contract_penalties = state->contract_penalties;
    out.reputation = state->reputation;
    return out;
}

bool batch_run(const ResolvedConfig *config,
               const SimulationSettings *settings,
               const Agent *const *agents,
               const char *const *strategy_names,
               size_t agent_count,
               size_t runs_per_strategy,
               bool has_base_seed,
               uint64_t base_seed,
               uint64_t *out_base_seed,
               BatchRunCallback on_result,
               void *context,
               BatchError *error) {
    if (error != NULL) memset(error, 0, sizeof(*error));
    if (config == NULL || settings == NULL || agents == NULL || strategy_names == NULL ||
        agent_count == 0 || runs_per_strategy == 0 || settings->days < 1 ||
        settings->start_slots < 0) {
        set_error(error, BATCH_ERROR_ARGUMENT, NULL, 0,
                 "config, settings, and a non-empty agent list are required");
        return false;
    }

    uint64_t seed = base_seed;
    if (!has_base_seed && !fresh_seed(&seed)) {
        set_error(error, BATCH_ERROR_SEED, NULL, 0, "could not generate a base seed");
        return false;
    }
    if (out_base_seed != NULL) *out_base_seed = seed;

    /* Mints every run's seed single-threaded and up front of that run (not
     * of the whole batch) in strict agent-major order, matching
     * runner/batch_run.py's `seed_rng.randrange(2**32)` generator --
     * see include/batch.h and rng.c:rng_randrange_2_32. */
    FarmRng seed_rng;
    rng_seed(&seed_rng, seed);

    SimulationSettings run_settings = *settings;
    run_settings.has_seed = false; /* per-run seed always comes from minting below */

    for (size_t a = 0; a < agent_count; a++) {
        for (size_t r = 0; r < runs_per_strategy; r++) {
            uint32_t run_seed = rng_randrange_2_32(&seed_rng);

            RunResult run = {0};
            RunnerError runner_error;
            bool ok = runner_run_single(config, &run_settings, agents[a],
                                        (RunSeed){true, run_seed}, NULL, NULL, &run,
                                        &runner_error);
            if (!ok) {
                char message[256];
                snprintf(message, sizeof(message), "strategy=%s seed=%u: %s",
                         strategy_names[a], run_seed, runner_error.message);
                set_error(error,
                         runner_error.code == RUNNER_ERROR_ALLOCATION
                             ? BATCH_ERROR_ALLOCATION : BATCH_ERROR_ENGINE,
                         strategy_names[a], run_seed, message);
                return false;
            }

            BatchRunResult snapshot = snapshot_result(strategy_names[a], run_seed, &run);
            runner_run_result_destroy(&run);
            if (on_result != NULL) on_result(&snapshot, context);
        }
    }
    return true;
}
