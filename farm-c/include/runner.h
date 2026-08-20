#ifndef FARM_RUNNER_H
#define FARM_RUNNER_H

#include <stdbool.h>
#include <stdint.h>

#include "agent.h"
#include "config.h"
#include "state.h"
#include "weather.h"

typedef struct {
    bool has_seed;
    uint64_t seed;
} RunSeed;

typedef struct {
    FarmState state;
    uint64_t seed;
    int days_simulated;
} RunResult;

typedef enum {
    RUNNER_ERROR_NONE,
    RUNNER_ERROR_ARGUMENT,
    RUNNER_ERROR_SEED,
    RUNNER_ERROR_ENGINE,
    RUNNER_ERROR_ALLOCATION
} RunnerErrorCode;

typedef struct {
    RunnerErrorCode code;
    char message[256];
} RunnerError;

typedef void (*RunDayCallback)(const FarmState *state, const WeatherDay *weather,
                               void *context);

bool runner_run_single(const ResolvedConfig *config,
                       const SimulationSettings *settings,
                       const Agent *agent,
                       RunSeed requested_seed,
                       RunDayCallback on_day,
                       void *context,
                       RunResult *out,
                       RunnerError *error);
void runner_run_result_destroy(RunResult *result);

#endif
