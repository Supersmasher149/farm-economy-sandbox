#include <assert.h>
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

typedef struct {
    BatchRunResult results[64];
    size_t count;
} Collected;

static void collect(const BatchRunResult *result, void *context) {
    Collected *out = context;
    assert(out->count < sizeof(out->results) / sizeof(out->results[0]));
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

int main(void) {
    test_randrange_2_32_matches_python();
    test_job_order_and_seed_minting();
    test_batch_run_matches_single_run();
    test_determinism_and_fresh_seed();
    test_invalid_arguments();
    puts("batch tests passed");
    return 0;
}
