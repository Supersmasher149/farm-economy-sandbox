#include "runner.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "engine.h"
#include "rng.h"

static void set_error(RunnerError *error, RunnerErrorCode code, const char *message) {
    if (error == NULL) return;
    error->code = code;
    snprintf(error->message, sizeof(error->message), "%s", message);
}

static void apply_initial_soil(FarmState *state, const SoilInitial *soil) {
    for (size_t i = 0; i < state->plot_count; i++) {
        state->plots[i].moisture = soil->moisture;
        state->plots[i].nitrogen = soil->nitrogen;
        state->plots[i].phosphorus = soil->phosphorus;
        state->plots[i].potassium = soil->potassium;
        state->plots[i].ph = soil->ph;
        state->plots[i].soil_health = soil->soil_health;
        state->plots[i].pest_pressure = soil->pest_pressure;
        state->plots[i].disease_pressure = soil->disease_pressure;
    }
}

bool runner_run_single(const ResolvedConfig *config,
                       const SimulationSettings *settings,
                       const Agent *agent,
                       RunSeed requested_seed,
                       RunDayCallback on_day,
                       void *context,
                       RunResult *out,
                       RunnerError *error) {
    if (out != NULL) memset(out, 0, sizeof(*out));
    if (error != NULL) memset(error, 0, sizeof(*error));
    if (config == NULL || settings == NULL || agent == NULL || out == NULL ||
        settings->days < 1 || settings->start_slots < 0 || !isfinite(settings->start_money)) {
        set_error(error, RUNNER_ERROR_ARGUMENT, "config, settings, agent, and result are required");
        return false;
    }

    uint64_t seed = requested_seed.seed;
    if (!requested_seed.has_seed && !rng_fresh_seed(&seed)) {
        set_error(error, RUNNER_ERROR_SEED, "could not generate a run seed");
        return false;
    }
    out->seed = seed;
    if (!farm_state_init(&out->state, config, settings->start_money, settings->start_slots)) {
        set_error(error, RUNNER_ERROR_ALLOCATION, "could not allocate farm state");
        return false;
    }
    out->state.operating_reserve = settings->operating_reserve;
    out->state.has_total_days = true;
    out->state.total_days = settings->days;
    out->state.has_run_seed = true;
    out->state.run_seed = (int64_t)seed;
    apply_initial_soil(&out->state, &config->soil_initial);
    /* runner/single_run.py:47's `player.highest_money = player.money`. Without
     * it, a run that never rises above its opening balance reports a *lower*
     * peak than Python does -- the peak stays unset until the first
     * track_peak_cash call inside a day, by which point spending has already
     * pulled money below the start.
     *
     * This belongs here rather than in farm_state_init for the same reason it
     * lives in the runner in Python: a fresh PlayerState has
     * `highest_money = None`, which economy_rules reads as a real state
     * (economy_rules.c:332's `has_highest_money ? ... : ...`, mirroring
     * economy_rules.py:373-374's `if player.highest_money is not None`), and
     * state.h documents farm_state_init as producing exactly those fresh-
     * PlayerState defaults. Seeding it there would make that branch
     * unreachable from the runner while it stays reachable for the
     * directly-constructed states the fixture tests build.
     *
     * lowest_money needs no counterpart: farm_state_init already seeds it,
     * since the C models it as a plain double rather than an Optional. */
    farm_state_track_peak_cash(&out->state);

    FarmRng rng;
    rng_seed(&rng, seed);
    for (int day = 0; day < settings->days && !out->state.bankrupt; day++) {
        EngineError engine_error;
        if (!engine_run_day_observed(&out->state, agent, &rng, on_day, context,
                                     &engine_error)) {
            set_error(error, engine_error.code == ENGINE_ERROR_ALLOCATION
                                ? RUNNER_ERROR_ALLOCATION : RUNNER_ERROR_ENGINE,
                      engine_error.message);
            runner_run_result_destroy(out);
            return false;
        }
    }
    out->days_simulated = out->state.day;
    return true;
}

void runner_run_result_destroy(RunResult *result) {
    if (result == NULL) return;
    farm_state_destroy(&result->state);
    result->seed = 0;
    result->days_simulated = 0;
}
