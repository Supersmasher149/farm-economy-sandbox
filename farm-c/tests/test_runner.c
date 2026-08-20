#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "runner.h"

typedef struct {
    int calls;
    int last_day;
    double first_moisture;
} Trace;

static void observe(const FarmState *state, const WeatherDay *weather, void *context) {
    Trace *trace = context;
    assert(weather != NULL);
    trace->calls++;
    trace->last_day = state->day;
    if (trace->calls == 1) trace->first_moisture = state->plots[0].moisture;
}

static void load(ResolvedConfig *config, SimulationSettings *settings) {
    ConfigError error;
    assert(config_load_directory("../config", config, &error));
    assert(config_load_simulation_settings("../config", settings, &error));
}

static void test_repeatability_and_seed(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    RunSeed seed = {true, 123456789};
    RunResult a = {0}, b = {0};
    RunnerError error;
    assert(runner_run_single(&config, &settings, &AGENT_FAST_SELLER, seed,
                             NULL, NULL, &a, &error));
    assert(runner_run_single(&config, &settings, &AGENT_FAST_SELLER, seed,
                             NULL, NULL, &b, &error));
    assert(a.seed == seed.seed && b.seed == seed.seed);
    assert(a.days_simulated == b.days_simulated);
    assert(a.state.money == b.state.money);
    assert(a.state.total_harvested == b.state.total_harvested);
    runner_run_result_destroy(&a);
    runner_run_result_destroy(&b);
    config_destroy(&config);
}

static void test_settings_callback_and_bankruptcy(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    settings.days = 4;
    settings.start_money = 100.0;
    settings.operating_reserve = 7.0;
    RunResult result = {0};
    Trace trace = {0};
    RunnerError error;
    assert(runner_run_single(&config, &settings, &AGENT_PROFIT_OPTIMIZER,
                             (RunSeed){true, 42}, observe, &trace, &result, &error));
    assert(result.state.operating_reserve == 7.0);
    assert(result.state.total_days == 4 && result.state.has_total_days);
    assert(result.days_simulated == trace.calls && trace.last_day == trace.calls);
    assert(trace.first_moisture != 0.65 || config.soil_initial.moisture != 0.65);
    runner_run_result_destroy(&result);

    settings.days = 365;
    settings.start_money = 0.0;
    assert(runner_run_single(&config, &settings, &AGENT_PROFIT_OPTIMIZER,
                             (RunSeed){true, 7}, NULL, NULL, &result, &error));
    assert(result.state.bankrupt);
    assert(result.days_simulated < settings.days);
    runner_run_result_destroy(&result);
    config_destroy(&config);
}

static void test_omitted_seed_and_invalid_arguments(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);
    RunResult result = {0};
    RunnerError error;
    assert(runner_run_single(&config, &settings, &AGENT_FAST_SELLER,
                             (RunSeed){false, 0}, NULL, NULL, &result, &error));
    assert(result.seed != 0);
    runner_run_result_destroy(&result);
    assert(!runner_run_single(&config, &settings, NULL, (RunSeed){true, 1},
                              NULL, NULL, &result, &error));
    assert(error.code == RUNNER_ERROR_ARGUMENT);
    runner_run_result_destroy(&result);
    config_destroy(&config);
}

int main(void) {
    test_repeatability_and_seed();
    test_settings_callback_and_bankruptcy();
    test_omitted_seed_and_invalid_arguments();
    puts("runner tests passed");
    return 0;
}
