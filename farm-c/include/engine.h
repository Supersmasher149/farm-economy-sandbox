#ifndef FARM_ENGINE_H
#define FARM_ENGINE_H

#include <stdbool.h>

#include "agent.h"
#include "rng.h"
#include "state.h"
#include "weather.h"

typedef enum {
    ENGINE_ERROR_NONE,
    ENGINE_ERROR_ARGUMENT,
    ENGINE_ERROR_ALLOCATION
} EngineErrorCode;

typedef struct {
    EngineErrorCode code;
    char message[256];
} EngineError;

typedef void (*EngineDayCallback)(const FarmState *state, const WeatherDay *weather,
                                  void *context);

bool engine_run_day(FarmState *state, const Agent *agent, FarmRng *rng,
                    EngineError *error);

bool engine_run_day_observed(FarmState *state, const Agent *agent, FarmRng *rng,
                             EngineDayCallback callback, void *context,
                             EngineError *error);

#endif
