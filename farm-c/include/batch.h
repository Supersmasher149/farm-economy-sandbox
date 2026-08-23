/* Batch execution: run every strategy many times each, the C analogue of
 * ../runner/batch_run.py. See that module's header comment for the design
 * this mirrors.
 *
 * Deliberately narrower than the Python surface (docs/c-port-plan.md's
 * "modern engine, single-run only" scope boundary):
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
 *
 * --- Parallelism (docs/c-port-plan.md step 22) --------------------------
 *
 * batch_run_parallel spreads the jobs across POSIX threads. Python needs a
 * *process* pool because the GIL makes threads useless for CPU-bound work;
 * C does not, so this is threads over one address space, sharing the single
 * immutable ResolvedConfig rather than pickling a copy per worker.
 *
 * The whole design exists to make worker count a pure performance knob --
 * `--workers N` must not be able to change a single bit of output for a
 * given base seed, exactly as ../CLAUDE.md requires of the Python batch
 * runner. Three properties get that, and all three are load-bearing:
 *
 *   1. Seeds are still minted by one FarmRng in strict job order. Workers
 *      claim jobs under a mutex and mint inside that critical section, so
 *      job i gets the i-th `randrange(2**32)` draw no matter which thread
 *      runs it or when.
 *   2. Runs share nothing. Each has its own FarmState, its own FarmRng, and
 *      a `const ResolvedConfig *` nobody writes to; agents are stateless
 *      const singletons (include/agent.h). The two file-scope scratch
 *      buffers in the run path (contracts.c, rng_hash.c) are already
 *      _Thread_local.
 *   3. Results are *delivered* in job order, not completion order. A
 *      bounded reorder ring holds finished-but-not-yet-deliverable results
 *      while the caller's thread drains it sequentially, so on_result fires
 *      in exactly the agent-major order the sequential path used. This is
 *      what keeps floating-point aggregation, CSV row order and the HTML
 *      payload byte-identical -- summing the same doubles in a different
 *      order would not be.
 *
 * The ring is what bounds memory: at most `4 * workers` runs may be in
 * flight or awaiting delivery, so peak memory scales with worker count,
 * never with batch size -- the same streaming discipline the sequential
 * path gets by freeing each FarmState before the next run starts.
 *
 * Failure semantics are unchanged, and are stated in delivery order rather
 * than execution order: the batch reports the *first failing job by index*,
 * every earlier job's callback has fired, and no later job's has. Later
 * jobs may have already executed on another thread when the failure is
 * delivered; their results are discarded unseen, which is unobservable
 * because a run has no side effects outside its own FarmState.
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

/* Hard ceiling on `worker_count`, so a fat-fingered `--workers 1000000`
 * fails an argument check instead of trying to spawn a million threads. */
#define BATCH_MAX_WORKERS 256

/* One worker per online CPU (BATCH_MAX_WORKERS at most, 1 if the count
 * cannot be determined) -- the same default os.cpu_count() gives
 * runner/batch_run.py. */
size_t batch_default_worker_count(void);

/* batch_run, spread across `worker_count` threads. Identical contract:
 * same minted seeds, same `on_result` order, same first-failure reporting,
 * bit-identical results for a given base seed at every worker count.
 *
 * `worker_count == 0` means batch_default_worker_count(); `1` runs the
 * sequential loop directly, spawning no thread at all (batch_run is exactly
 * that call). Values above BATCH_MAX_WORKERS are a BATCH_ERROR_ARGUMENT.
 * The effective count is clamped down to the job count -- there is nothing
 * for the 8th thread of a 3-run batch to do -- and if no thread can be
 * spawned at all the batch completes sequentially rather than failing. */
bool batch_run_parallel(const ResolvedConfig *config,
                        const SimulationSettings *settings,
                        const Agent *const *agents,
                        const char *const *strategy_names,
                        size_t agent_count,
                        size_t runs_per_strategy,
                        size_t worker_count,
                        bool has_base_seed,
                        uint64_t base_seed,
                        uint64_t *out_base_seed,
                        BatchRunCallback on_result,
                        void *context,
                        BatchError *error);

#endif /* FARM_BATCH_H */
