/* Batch execution: run every strategy many times each, the C analogue of
 * ../runner/batch_run.py. See that module's header comment for the design
 * this mirrors.
 *
 * Deliberately narrower than the Python surface (docs/c-port-plan.md's
 * "modern engine, single-run only" scope boundary, now extended one step):
 *   - Sequential only. Python's process pool exists because CPython's GIL
 *     makes threads useless for CPU-bound work; C has no such constraint,
 *     but this port adds no threading of its own -- a run here is already
 *     fast enough that batches worth doing on this port (thousands of
 *     runs) finish in well under a second sequentially. Nothing here rules
 *     out a future parallel path; it just isn't built.
 *   - No report artifacts (summary.json/summary_report.md/dashboard.html).
 *     Callers get one BatchRunResult per completed run via a callback and
 *     decide what to do with it -- print it, aggregate it, write a CSV.
 *
 * What *is* preserved bit-exactly: seed minting. A single FarmRng, seeded
 * from one base seed, mints every run's seed via rng_randrange_2_32 in
 * strict agent-major order (all of agent[0]'s runs, then all of agent[1]'s,
 * ...) -- exactly matching runner/batch_run.py's
 * `seed_rng.randrange(2**32)` loop order. The same base seed therefore
 * mints the same per-run seeds here as it does in the Python batch runner,
 * so a `--seed` shared between `farm-c batch` and `python3 main.py batch`
 * runs each (agent, run_seed) pair through an independently-verified
 * simulation of the same inputs.
 */
#ifndef FARM_BATCH_H
#define FARM_BATCH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "agent.h"
#include "config.h"

/* One run's outcome, flattened out of FarmState the way
 * metrics/run_results.py:build_run_result flattens PlayerState -- a
 * trimmed subset (the scalar fields main.c's `single` summary already
 * prints, plus the handful of additional raw fields
 * metrics/warnings.py:evaluate_warnings's rules need -- see
 * include/warnings.h), not the full dataclass (crop_percentages,
 * expenses_by_category, and the other report-only dict-valued fields stay
 * out of scope here; see README). The FarmState itself is destroyed before
 * the next run starts, keeping a batch's peak memory bounded independent of
 * how many runs it covers, same as run_batch's streaming-generator
 * discipline. */
typedef struct {
    const char *strategy;
    uint64_t seed;
    int days_simulated;

    double final_money;
    double total_revenue;
    double total_expenses;
    double net_profit; /* total_revenue - total_expenses */

    int total_planted;
    int total_harvested;
    int total_sold;
    int idle_days;

    bool bankrupt;
    int bankruptcy_day; /* INVALID_DAY if never bankrupt */

    double lowest_money;
    double highest_money;

    int total_waterings;
    int total_fertilizer_applied;
    int total_processed;

    int contracts_completed;
    int contracts_failed;
    double contract_penalties;
    double reputation;

    /* Warnings-only additions (metrics/run_results.py's crop_counts,
     * crop_loss_rate's inputs, first_upgrade_day, and watering_rate's
     * denominator). crop_plant_counts is borrowed from FarmState -- valid
     * only for the duration of the BatchRunCallback, same lifetime as
     * `strategy` above -- length config->crop_count, one entry per
     * config->crops[i] in that same order. */
    const int *crop_plant_counts;
    int total_crops_lost;
    int total_harvest_events;
    int slot_days;
    int first_upgrade_day; /* INVALID_DAY if no upgrade was ever bought */
} BatchRunResult;

typedef enum {
    BATCH_ERROR_NONE,
    BATCH_ERROR_ARGUMENT,
    BATCH_ERROR_SEED,
    BATCH_ERROR_ENGINE,
    BATCH_ERROR_ALLOCATION
} BatchErrorCode;

typedef struct {
    BatchErrorCode code;
    char message[320];
    const char *strategy; /* which job failed, borrowed from the caller's agents/names array */
    uint64_t seed;
} BatchError;

/* Invoked once per completed run, in job order (agent-major, then run
 * index within that agent) -- immediately after the run finishes and
 * before its FarmState is freed. `result` and everything it points to
 * (only `strategy`, which is borrowed from `strategy_names`) is invalid
 * once the callback returns. */
typedef void (*BatchRunCallback)(const BatchRunResult *result, void *context);

/* Runs `agent_count` agents (`agents`/`strategy_names` parallel arrays)
 * `runs_per_strategy` times each, agent-major order, and streams one
 * BatchRunResult per completed run through `on_result`.
 *
 * `has_base_seed`/`base_seed` follow RunSeed's convention: pass
 * has_base_seed == false to mint a fresh, /dev/urandom-backed base seed
 * (returned via `out_base_seed` so a caller can record/print it, the same
 * "always know what you ran" guarantee `farm-c single`'s actual_seed
 * output gives). `settings->days`/`start_money`/`start_slots`/
 * `operating_reserve` apply to every run in the batch; `settings->has_seed`
 * is ignored here (per-run seeds come from the batch's own minting, not
 * the settings file).
 *
 * Returns false and stops (without invoking `on_result` for the failed
 * job) on the first run that fails to allocate or errors inside the
 * engine -- matching batch_run.py's `_execute` wrapping a run failure in a
 * RuntimeError that names the strategy and seed, rather than silently
 * skipping it. Every prior job's callback has already fired by then. */
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
               BatchError *error);

#endif /* FARM_BATCH_H */
