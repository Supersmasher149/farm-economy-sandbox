#include "batch.h"

#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "contracts.h"
#include "engine.h"
#include "rng.h"
#include "runner.h"

/* The immutable half of a batch: what every job needs and nothing mutates.
 * Bundled so the sequential loop, the worker pool and the pool's threads
 * all read one description of the work instead of six parallel parameters
 * each. */
typedef struct {
    const ResolvedConfig *config;
    const SimulationSettings *settings;
    const Agent *const *agents;
    const char *const *strategy_names;
    size_t agent_count;
    size_t runs_per_strategy;
} BatchJobSpec;

static void set_error(BatchError *error, BatchErrorCode code, const char *strategy,
                      uint64_t seed, const char *message) {
    if (error == NULL) return;
    error->code = code;
    error->strategy = strategy;
    error->seed = seed;
    snprintf(error->message, sizeof(error->message), "%s", message);
}

/* Both paths report a failed run identically -- naming the strategy and
 * seed, the way batch_run.py's `_execute` wraps a run failure in a
 * RuntimeError rather than silently skipping it. */
static void report_run_failure(BatchError *error, const char *strategy, uint32_t seed,
                               const RunnerError *runner_error) {
    char message[256];
    snprintf(message, sizeof(message), "strategy=%s seed=%u: %s", strategy, seed,
             runner_error->message);
    set_error(error,
              runner_error->code == RUNNER_ERROR_ALLOCATION ? BATCH_ERROR_ALLOCATION
                                                            : BATCH_ERROR_ENGINE,
              strategy, seed, message);
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

    out.crop_plant_counts = state->crop_plant_counts;
    out.total_crops_lost = state->total_crops_lost;
    out.total_harvest_events = state->total_harvest_events;
    out.slot_days = state->slot_days;
    out.first_upgrade_day = INVALID_DAY;
    for (size_t i = 0; i < state->config->upgrade_count; i++) {
        int day = state->upgrade_purchase_days[i];
        if (day != INVALID_DAY &&
            (out.first_upgrade_day == INVALID_DAY || day < out.first_upgrade_day))
            out.first_upgrade_day = day;
    }
    return out;
}

/* --- Sequential path ---------------------------------------------------
 *
 * Unchanged from before batch_run_parallel existed, and deliberately kept
 * as its own loop rather than expressed as "the pool with one worker": it
 * is the reference the parallel path is checked against
 * (tests/test_batch.c's worker-count invariance test), so it must not
 * share the ring, the mutex, or anything else that could drift with them. */
static bool run_sequential(const BatchJobSpec *spec, uint64_t base_seed,
                           BatchRunCallback on_result, void *context, BatchError *error) {
    FarmRng seed_rng;
    rng_seed(&seed_rng, base_seed);

    SimulationSettings run_settings = *spec->settings;
    run_settings.has_seed = false; /* per-run seed always comes from minting below */

    for (size_t a = 0; a < spec->agent_count; a++) {
        for (size_t r = 0; r < spec->runs_per_strategy; r++) {
            uint32_t run_seed = rng_randrange_2_32(&seed_rng);

            RunResult run = {0};
            RunnerError runner_error;
            bool ok = runner_run_single(spec->config, &run_settings, spec->agents[a],
                                        (RunSeed){true, run_seed}, NULL, NULL, &run,
                                        &runner_error);
            if (!ok) {
                report_run_failure(error, spec->strategy_names[a], run_seed, &runner_error);
                return false;
            }

            /* Callback first, destroy second -- the order include/batch.h
             * documents. snapshot.crop_plant_counts is borrowed straight
             * from the FarmState, so destroying first leaves the callback
             * reading freed memory. */
            BatchRunResult snapshot = snapshot_result(spec->strategy_names[a], run_seed, &run);
            if (on_result != NULL) on_result(&snapshot, context);
            runner_run_result_destroy(&run);
        }
    }
    return true;
}

/* --- Parallel path ------------------------------------------------------
 *
 * See include/batch.h for the three invariants this implements. In short:
 * workers claim jobs (and mint their seeds) in order under `mutex`, run
 * them with nothing shared, and publish into ring[job % ring_capacity];
 * the calling thread drains that ring strictly in job order and is the
 * only thread that ever calls on_result. */

/* One finished run, parked until its turn to be delivered. */
typedef struct {
    bool filled;
    bool ok;
    BatchRunResult result; /* result.crop_plant_counts points at crop_counts */
    int *crop_counts;      /* owned slice of BatchPool.crop_block */
    uint32_t seed;
    const char *strategy;  /* borrowed from strategy_names */
    RunnerError runner_error; /* meaningful only when !ok */
} ResultSlot;

typedef struct {
    const BatchJobSpec *spec;
    SimulationSettings run_settings;
    size_t total_jobs;
    size_t ring_capacity;

    pthread_mutex_t mutex;
    pthread_cond_t slot_free;    /* a delivery freed a ring slot */
    pthread_cond_t result_ready; /* a worker filled the slot being awaited */

    /* Everything below is guarded by `mutex`. */
    FarmRng seed_rng;
    size_t next_job;     /* lowest unclaimed job index */
    size_t next_deliver; /* lowest undelivered job index */
    bool aborted;        /* a failed job was delivered; workers should stop */
    ResultSlot *ring;
    int *crop_block; /* ring_capacity * crop_count ints, carved into slots */
} BatchPool;

/* Claims the next job, minting its seed inside the same critical section so
 * job i always gets the i-th rng_randrange_2_32 draw. Blocks while the
 * reorder ring is full, which is what bounds in-flight runs to
 * ring_capacity. Returns false when the batch is finished or aborted. */
static bool pool_claim(BatchPool *pool, size_t *out_job, uint32_t *out_seed) {
    pthread_mutex_lock(&pool->mutex);
    while (!pool->aborted && pool->next_job < pool->total_jobs &&
           pool->next_job - pool->next_deliver >= pool->ring_capacity) {
        pthread_cond_wait(&pool->slot_free, &pool->mutex);
    }
    if (pool->aborted || pool->next_job >= pool->total_jobs) {
        pthread_mutex_unlock(&pool->mutex);
        return false;
    }
    *out_job = pool->next_job++;
    *out_seed = rng_randrange_2_32(&pool->seed_rng);
    pthread_mutex_unlock(&pool->mutex);
    return true;
}

/* Parks a finished run in its slot. The slot is guaranteed free: pool_claim
 * only hands out job indexes within [next_deliver, next_deliver +
 * ring_capacity), and those map one-to-one onto the ring's slots. */
static void pool_publish(BatchPool *pool, size_t job, const ResultSlot *finished) {
    pthread_mutex_lock(&pool->mutex);
    ResultSlot *slot = &pool->ring[job % pool->ring_capacity];
    int *crop_counts = slot->crop_counts; /* preserve the slot's own buffer */
    *slot = *finished;
    slot->crop_counts = crop_counts;
    if (finished->ok && crop_counts != NULL) {
        memcpy(crop_counts, finished->result.crop_plant_counts,
               pool->spec->config->crop_count * sizeof(int));
    }
    slot->result.crop_plant_counts = crop_counts;
    slot->filled = true;
    pthread_cond_signal(&pool->result_ready);
    pthread_mutex_unlock(&pool->mutex);
}

static void *worker_main(void *arg) {
    BatchPool *pool = arg;
    size_t job;
    uint32_t seed;
    while (pool_claim(pool, &job, &seed)) {
        size_t agent_index = job / pool->spec->runs_per_strategy;
        ResultSlot finished = {0};
        finished.seed = seed;
        finished.strategy = pool->spec->strategy_names[agent_index];

        RunResult run = {0};
        finished.ok = runner_run_single(pool->spec->config, &pool->run_settings,
                                        pool->spec->agents[agent_index],
                                        (RunSeed){true, seed}, NULL, NULL, &run,
                                        &finished.runner_error);
        if (finished.ok) {
            /* pool_publish copies crop_plant_counts out before this
             * FarmState dies, which is what lets the delivering thread see
             * the same borrowed-array contract the sequential path gives. */
            finished.result = snapshot_result(finished.strategy, seed, &run);
            pool_publish(pool, job, &finished);
            runner_run_result_destroy(&run);
        } else {
            pool_publish(pool, job, &finished);
        }
    }
    /* contracts.c's decorate-sort scratch is _Thread_local and never freed
     * between calls; on a thread that is about to exit that is a leak, and
     * this build runs under ASan. See contracts_release_thread_scratch. */
    contracts_release_thread_scratch();
    return NULL;
}

/* Drains the ring in job order on the calling thread -- the only thread
 * that ever touches `on_result`, `context`, or anything they reach. Stops
 * at the first failed job, having delivered every job before it and none
 * after. */
static bool pool_deliver_all(BatchPool *pool, BatchRunCallback on_result, void *context,
                             int *scratch_crop_counts, BatchError *error) {
    size_t crop_count = pool->spec->config->crop_count;
    for (size_t job = 0; job < pool->total_jobs; job++) {
        pthread_mutex_lock(&pool->mutex);
        ResultSlot *slot = &pool->ring[job % pool->ring_capacity];
        while (!slot->filled) {
            pthread_cond_wait(&pool->result_ready, &pool->mutex);
        }
        /* Copy out and release the slot before running the callback, so a
         * slow consumer (CSV write, HTML payload append) never blocks a
         * worker that is only waiting for ring space. */
        ResultSlot delivered = *slot;
        if (delivered.ok && crop_count > 0) {
            memcpy(scratch_crop_counts, slot->crop_counts, crop_count * sizeof(int));
        }
        slot->filled = false;
        pool->next_deliver = job + 1;
        if (!delivered.ok) pool->aborted = true;
        pthread_cond_broadcast(&pool->slot_free);
        pthread_mutex_unlock(&pool->mutex);

        if (!delivered.ok) {
            report_run_failure(error, delivered.strategy, delivered.seed,
                               &delivered.runner_error);
            return false;
        }
        delivered.result.crop_plant_counts = crop_count > 0 ? scratch_crop_counts : NULL;
        if (on_result != NULL) on_result(&delivered.result, context);
    }
    return true;
}

size_t batch_default_worker_count(void) {
    long online = sysconf(_SC_NPROCESSORS_ONLN);
    if (online < 1) return 1;
    if (online > BATCH_MAX_WORKERS) return BATCH_MAX_WORKERS;
    return (size_t)online;
}

static bool run_parallel(const BatchJobSpec *spec, size_t worker_count, uint64_t base_seed,
                         BatchRunCallback on_result, void *context, BatchError *error) {
    size_t total_jobs = spec->agent_count * spec->runs_per_strategy;
    size_t crop_count = spec->config->crop_count;
    /* Four slots per worker: deep enough that a worker essentially never
     * waits on ring space (it would take three of its peers finishing while
     * one job stalls delivery), shallow enough that peak memory tracks
     * worker count rather than batch size. */
    size_t ring_capacity = worker_count * 4;
    if (ring_capacity > total_jobs) ring_capacity = total_jobs;

    BatchPool pool = {0};
    pool.spec = spec;
    pool.run_settings = *spec->settings;
    pool.run_settings.has_seed = false; /* per-run seed always comes from minting */
    pool.total_jobs = total_jobs;
    pool.ring_capacity = ring_capacity;
    rng_seed(&pool.seed_rng, base_seed);

    pthread_t *threads = calloc(worker_count, sizeof(pthread_t));
    pool.ring = calloc(ring_capacity, sizeof(ResultSlot));
    /* One flat block carved into per-slot slices, plus one more slice the
     * delivering thread hands to the callback -- the same arrangement
     * main.c uses for per-strategy crop totals, and it keeps the whole
     * parallel path down to three allocations. */
    int *crop_block = crop_count > 0
                          ? calloc((ring_capacity + 1) * crop_count, sizeof(int))
                          : NULL;
    if (threads == NULL || pool.ring == NULL || (crop_count > 0 && crop_block == NULL)) {
        free(threads);
        free(pool.ring);
        free(crop_block);
        set_error(error, BATCH_ERROR_ALLOCATION, NULL, 0,
                  "could not allocate the parallel batch worker pool");
        return false;
    }
    pool.crop_block = crop_block;
    for (size_t i = 0; i < ring_capacity; i++) {
        pool.ring[i].crop_counts = crop_count > 0 ? crop_block + i * crop_count : NULL;
    }
    int *scratch_crop_counts = crop_count > 0 ? crop_block + ring_capacity * crop_count : NULL;

    pthread_mutex_init(&pool.mutex, NULL);
    pthread_cond_init(&pool.slot_free, NULL);
    pthread_cond_init(&pool.result_ready, NULL);

    size_t started = 0;
    for (size_t i = 0; i < worker_count; i++) {
        if (pthread_create(&threads[i], NULL, worker_main, &pool) != 0) break;
        started++;
    }

    bool ok;
    if (started == 0) {
        /* Nothing to drain, because nothing will ever fill the ring. Fall
         * back rather than fail: a batch that cannot get a thread is still
         * a batch that can run, just slower. */
        ok = run_sequential(spec, base_seed, on_result, context, error);
    } else {
        ok = pool_deliver_all(&pool, on_result, context, scratch_crop_counts, error);
        if (!ok) {
            /* pool_deliver_all already latched `aborted` under the mutex
             * and broadcast, so every worker wakes and returns. */
            pthread_mutex_lock(&pool.mutex);
            pool.aborted = true;
            pthread_cond_broadcast(&pool.slot_free);
            pthread_mutex_unlock(&pool.mutex);
        }
        for (size_t i = 0; i < started; i++) pthread_join(threads[i], NULL);
    }

    pthread_cond_destroy(&pool.result_ready);
    pthread_cond_destroy(&pool.slot_free);
    pthread_mutex_destroy(&pool.mutex);
    free(threads);
    free(pool.ring);
    free(crop_block);
    return ok;
}

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
                        BatchError *error) {
    if (error != NULL) memset(error, 0, sizeof(*error));
    if (config == NULL || settings == NULL || agents == NULL || strategy_names == NULL ||
         agent_count == 0 || runs_per_strategy == 0 || settings->days < 1 ||
          settings->start_slots < 1 || !isfinite(settings->start_money) ||
          settings->start_money < 0.0 || !isfinite(settings->operating_reserve) ||
          settings->operating_reserve < 0.0) {
        set_error(error, BATCH_ERROR_ARGUMENT, NULL, 0,
                 "config, settings, and a non-empty agent list are required");
        return false;
    }
    if (worker_count > BATCH_MAX_WORKERS) {
        char message[128];
        snprintf(message, sizeof(message), "worker_count must be 0 (auto) or 1..%d",
                 BATCH_MAX_WORKERS);
        set_error(error, BATCH_ERROR_ARGUMENT, NULL, 0, message);
        return false;
    }
    if (agent_count > SIZE_MAX / runs_per_strategy) {
        set_error(error, BATCH_ERROR_ARGUMENT, NULL, 0, "batch job count overflows");
        return false;
    }

    uint64_t seed = base_seed;
    if (!has_base_seed && !rng_fresh_seed(&seed)) {
        set_error(error, BATCH_ERROR_SEED, NULL, 0, "could not generate a base seed");
        return false;
    }
    if (out_base_seed != NULL) *out_base_seed = seed;

    BatchJobSpec spec = {config, settings, agents, strategy_names, agent_count,
                         runs_per_strategy};
    if (worker_count == 0) worker_count = batch_default_worker_count();
    size_t total_jobs = agent_count * runs_per_strategy;
    if (worker_count > total_jobs) worker_count = total_jobs;
    if (worker_count <= 1) return run_sequential(&spec, seed, on_result, context, error);
    return run_parallel(&spec, worker_count, seed, on_result, context, error);
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
    return batch_run_parallel(config, settings, agents, strategy_names, agent_count,
                              runs_per_strategy, 1, has_base_seed, base_seed, out_base_seed,
                              on_result, context, error);
}
