#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "batch.h"
#include "rng.h"
#include "runner.h"

static void load(ResolvedConfig *config, SimulationSettings *settings) {
    ConfigError error;
    assert(config_load_directory("../config", config, &error));
    assert(config_load_simulation_settings("../config", settings, &error));
}

/* rng_randrange_2_32 backs every seed batch_run mints -- lock its output to
 * real `random.Random(seed).randrange(2**32)` values (python3 -c
 * "import random; print([random.Random(42).randrange(2**32) for _ in
 * range(5)])") so a regression here is caught independent of the rest of
 * batch_run. */
static void test_randrange_2_32_matches_python(void) {
    FarmRng rng;
    rng_seed(&rng, 42);
    uint32_t expected[] = {2746317213u, 1181241943u, 958682846u, 3163119785u, 1812140441u};
    for (size_t i = 0; i < sizeof(expected) / sizeof(expected[0]); i++) {
        assert(rng_randrange_2_32(&rng) == expected[i]);
    }
}

#define MAX_COLLECTED 64
#define MAX_COLLECTED_CROPS 16

typedef struct {
    BatchRunResult results[MAX_COLLECTED];
    /* crop_plant_counts is only valid for the duration of the callback, so
     * the invariance test below cannot compare it through the borrowed
     * pointer -- it has to keep its own copy. Doing that here also makes
     * the copy itself a check: a parallel worker that published a dangling
     * or torn crop array shows up as a mismatch rather than as a crash. */
    int crops[MAX_COLLECTED][MAX_COLLECTED_CROPS];
    size_t crop_count;
    size_t count;
} Collected;

static void collect(const BatchRunResult *result, void *context) {
    Collected *out = context;
    assert(out->count < MAX_COLLECTED);
    assert(out->crop_count <= MAX_COLLECTED_CROPS);
    for (size_t c = 0; c < out->crop_count; c++)
        out->crops[out->count][c] = result->crop_plant_counts[c];
    out->results[out->count++] = *result;
}

/* Job order and seed minting: strict agent-major order, and every seed is
 * exactly what a FarmRng seeded from the same base seed mints via
 * rng_randrange_2_32 in that same order -- matching
 * runner/batch_run.py's `seed_rng.randrange(2**32)` generator. */
static void test_job_order_and_seed_minting(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 3;

    const Agent *agents[2] = {&AGENT_FAST_SELLER, &AGENT_PROFIT_OPTIMIZER};
    const char *names[2] = {"fast_seller", "profit_optimizer"};
    Collected collected = {0};
    uint64_t resolved_seed = 0;
    BatchError error;
    assert(batch_run(&config, &settings, agents, names, 2, 3, true, 999, &resolved_seed,
                     collect, &collected, &error));
    assert(resolved_seed == 999);
    assert(collected.count == 6);

    FarmRng seed_rng;
    rng_seed(&seed_rng, 999);
    for (int a = 0; a < 2; a++) {
        for (int r = 0; r < 3; r++) {
            size_t i = (size_t)(a * 3 + r);
            uint32_t expected_seed = rng_randrange_2_32(&seed_rng);
            assert(collected.results[i].seed == expected_seed);
            assert(strcmp(collected.results[i].strategy, names[a]) == 0);
        }
    }
    config_destroy(&config);
}

/* Every (agent, minted seed) pair a batch runs must be bit-exact with
 * calling runner_run_single directly for that same explicit seed -- a
 * batch is not a different simulation path, just a driver that mints
 * seeds and loops. */
static void test_batch_run_matches_single_run(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 10;

    const Agent *agents[1] = {&AGENT_RECKLESS_SPENDER};
    const char *names[1] = {"reckless_spender"};
    Collected collected = {0};
    uint64_t resolved_seed = 0;
    BatchError error;
    assert(batch_run(&config, &settings, agents, names, 1, 2, true, 555, &resolved_seed,
                     collect, &collected, &error));
    assert(collected.count == 2);

    for (size_t i = 0; i < 2; i++) {
        RunResult direct = {0};
        RunnerError runner_error;
        assert(runner_run_single(&config, &settings, &AGENT_RECKLESS_SPENDER,
                                 (RunSeed){true, collected.results[i].seed}, NULL, NULL, &direct,
                                 &runner_error));
        assert(direct.days_simulated == collected.results[i].days_simulated);
        assert(direct.state.money == collected.results[i].final_money);
        assert(direct.state.total_revenue == collected.results[i].total_revenue);
        assert(direct.state.total_expenses == collected.results[i].total_expenses);
        assert(direct.state.bankrupt == collected.results[i].bankrupt);
        assert(direct.state.total_harvested == collected.results[i].total_harvested);
        runner_run_result_destroy(&direct);
    }
    config_destroy(&config);
}

static void test_determinism_and_fresh_seed(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 5;

    const Agent *agents[1] = {&AGENT_DIVERSIFIER};
    const char *names[1] = {"diversifier"};

    Collected first = {0}, second = {0};
    uint64_t seed_a = 0, seed_b = 0;
    BatchError error;
    assert(batch_run(&config, &settings, agents, names, 1, 4, true, 2024, &seed_a, collect,
                     &first, &error));
    assert(batch_run(&config, &settings, agents, names, 1, 4, true, 2024, &seed_b, collect,
                     &second, &error));
    assert(seed_a == seed_b && seed_a == 2024);
    assert(first.count == second.count);
    for (size_t i = 0; i < first.count; i++) {
        assert(first.results[i].seed == second.results[i].seed);
        assert(first.results[i].final_money == second.results[i].final_money);
    }

    /* Omitted seed: batch_run mints its own base seed and reports it. */
    Collected fresh = {0};
    uint64_t fresh_seed_value = 0;
    assert(batch_run(&config, &settings, agents, names, 1, 1, false, 0, &fresh_seed_value,
                     collect, &fresh, &error));
    assert(fresh.count == 1);
    config_destroy(&config);
}

static void test_invalid_arguments(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    const Agent *agents[1] = {&AGENT_FAST_SELLER};
    const char *names[1] = {"fast_seller"};
    BatchError error;

    assert(!batch_run(NULL, &settings, agents, names, 1, 1, true, 1, NULL, NULL, NULL, &error));
    assert(error.code == BATCH_ERROR_ARGUMENT);

    assert(!batch_run(&config, &settings, agents, names, 0, 1, true, 1, NULL, NULL, NULL,
                      &error));
    assert(error.code == BATCH_ERROR_ARGUMENT);

    assert(!batch_run(&config, &settings, agents, names, 1, 0, true, 1, NULL, NULL, NULL,
                      &error));
    assert(error.code == BATCH_ERROR_ARGUMENT);

    config_destroy(&config);
}

/* Worker count is a performance knob and nothing else: every field of
 * every run, in every position, must be bit-identical to the sequential
 * path. This is the claim include/batch.h makes and the reason the ordered
 * reorder ring exists -- delivering results in completion order instead
 * would pass a "same set of runs" check and still reorder the floating-
 * point aggregation main.c does downstream.
 *
 * Exact comparison throughout: doubles by `==` (these are the same
 * arithmetic on the same inputs, so anything but equality is a bug, and an
 * epsilon here would hide exactly the 1-ulp drift this port exists to
 * prevent). */
static void assert_identical(const Collected *a, const Collected *b, size_t worker_count) {
    assert(a->count == b->count);
    for (size_t i = 0; i < a->count; i++) {
        const BatchRunResult *x = &a->results[i];
        const BatchRunResult *y = &b->results[i];
        if (strcmp(x->strategy, y->strategy) != 0 || x->seed != y->seed ||
            x->days_simulated != y->days_simulated || x->final_money != y->final_money ||
            x->total_revenue != y->total_revenue || x->total_expenses != y->total_expenses ||
            x->net_profit != y->net_profit || x->total_planted != y->total_planted ||
            x->total_harvested != y->total_harvested || x->total_sold != y->total_sold ||
            x->idle_days != y->idle_days || x->bankrupt != y->bankrupt ||
            x->bankruptcy_day != y->bankruptcy_day || x->lowest_money != y->lowest_money ||
            x->highest_money != y->highest_money || x->total_waterings != y->total_waterings ||
            x->total_fertilizer_applied != y->total_fertilizer_applied ||
            x->total_processed != y->total_processed ||
            x->contracts_completed != y->contracts_completed ||
            x->contracts_failed != y->contracts_failed ||
            x->contract_penalties != y->contract_penalties || x->reputation != y->reputation ||
            x->total_crops_lost != y->total_crops_lost ||
            x->total_harvest_events != y->total_harvest_events ||
            x->slot_days != y->slot_days || x->first_upgrade_day != y->first_upgrade_day) {
            printf("FAIL workers=%zu diverged at run %zu (strategy=%s seed=%" PRIu64 ")\n",
                   worker_count, i, x->strategy, x->seed);
            assert(0);
        }
        for (size_t c = 0; c < a->crop_count; c++) {
            if (a->crops[i][c] != b->crops[i][c]) {
                printf("FAIL workers=%zu diverged at run %zu crop %zu\n", worker_count, i, c);
                assert(0);
            }
        }
    }
}

static void test_worker_count_is_output_invariant(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 24;
    assert(config.crop_count <= MAX_COLLECTED_CROPS);

    /* Agents whose runs differ wildly in length -- reckless_spender goes
     * bankrupt early, profit_optimizer plays out the full horizon -- so
     * workers genuinely finish out of order and the ring has to reorder
     * them. A roster of equal-cost runs would let completion order match
     * job order by luck and prove nothing. */
    const Agent *agents[3] = {&AGENT_RECKLESS_SPENDER, &AGENT_PROFIT_OPTIMIZER,
                              &AGENT_FAST_SELLER};
    const char *names[3] = {"reckless_spender", "profit_optimizer", "fast_seller"};

    Collected sequential = {.crop_count = config.crop_count};
    uint64_t base = 0;
    BatchError error;
    assert(batch_run(&config, &settings, agents, names, 3, 7, true, 31337, &base, collect,
                     &sequential, &error));
    assert(sequential.count == 21);

    /* 1 is the sequential path taken through batch_run_parallel; 2/3/5 sit
     * on either side of the agent count; 64 is far more workers than jobs,
     * which must clamp rather than misbehave. */
    static const size_t worker_counts[] = {1, 2, 3, 5, 8, 64};
    for (size_t w = 0; w < sizeof(worker_counts) / sizeof(worker_counts[0]); w++) {
        Collected parallel = {.crop_count = config.crop_count};
        uint64_t parallel_base = 0;
        assert(batch_run_parallel(&config, &settings, agents, names, 3, 7, worker_counts[w],
                                  true, 31337, &parallel_base, collect, &parallel, &error));
        assert(parallel_base == base);
        assert_identical(&sequential, &parallel, worker_counts[w]);
    }

    /* worker_count == 0 means "one per core", which is whatever this
     * machine has -- still the same output. */
    Collected automatic = {.crop_count = config.crop_count};
    uint64_t automatic_base = 0;
    assert(batch_run_parallel(&config, &settings, agents, names, 3, 7, 0, true, 31337,
                              &automatic_base, collect, &automatic, &error));
    assert(automatic_base == base);
    assert_identical(&sequential, &automatic, 0);
    assert(batch_default_worker_count() >= 1);
    assert(batch_default_worker_count() <= BATCH_MAX_WORKERS);

    config_destroy(&config);
}

/* More jobs than the reorder ring can hold, so delivery has to block
 * workers on ring space and release them again -- the path a batch small
 * enough to fit entirely in the ring never reaches. */
static void test_parallel_beyond_ring_capacity(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 6;

    const Agent *agents[1] = {&AGENT_DIVERSIFIER};
    const char *names[1] = {"diversifier"};

    Collected sequential = {.crop_count = config.crop_count};
    Collected parallel = {.crop_count = config.crop_count};
    uint64_t seed_a = 0, seed_b = 0;
    BatchError error;
    /* 60 jobs against a 2-worker ring of 8 slots. */
    assert(batch_run_parallel(&config, &settings, agents, names, 1, 60, 1, true, 4242, &seed_a,
                              collect, &sequential, &error));
    assert(batch_run_parallel(&config, &settings, agents, names, 1, 60, 2, true, 4242, &seed_b,
                              collect, &parallel, &error));
    assert(sequential.count == 60 && parallel.count == 60);
    assert_identical(&sequential, &parallel, 2);
    config_destroy(&config);
}

static void test_invalid_worker_count(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    const Agent *agents[1] = {&AGENT_FAST_SELLER};
    const char *names[1] = {"fast_seller"};
    BatchError error;

    assert(!batch_run_parallel(&config, &settings, agents, names, 1, 1, BATCH_MAX_WORKERS + 1,
                               true, 1, NULL, NULL, NULL, &error));
    assert(error.code == BATCH_ERROR_ARGUMENT);
    config_destroy(&config);
}

int main(void) {
    test_randrange_2_32_matches_python();
    test_job_order_and_seed_minting();
    test_batch_run_matches_single_run();
    test_determinism_and_fresh_seed();
    test_invalid_arguments();
    test_worker_count_is_output_invariant();
    test_parallel_beyond_ring_capacity();
    test_invalid_worker_count();
    puts("batch tests passed");
    return 0;
}
