#include "weather.h"

#include "crop_growth.h"
#include "pyfloat.h"

/* simulation/weather.py:22-23. */
Season weather_season_for_day(int day, int season_length_days) {
    /* Python's `//` and `%` on non-negative operands agree with C's `/` and
     * `%`; day and season_length_days are both always non-negative in this
     * port's callers (day counts up from 0, season_length_days is a
     * positive config value), so no floor-division helper is needed here
     * unlike contracts.c/profit_optimizer.c's genuinely-signed cases. */
    return (Season)((day / season_length_days) % SEASON_COUNT);
}

/* simulation/weather.py:26-44. */
WeatherDay weather_generate(int day, const ResolvedConfig *config, FarmRng *rng) {
    const WeatherParams *params = &config->weather;
    Season season = weather_season_for_day(day, params->season_length_days);
    const SeasonWeather *by_season = &params->by_season[season];

    /* Draw order fixed: temperature, then the rain-chance roll, then
     * (conditionally) the rainfall draw -- see weather.h's header comment. */
    double temperature = rng_uniform_event(rng, by_season->temperature_low, by_season->temperature_high);
    double rainfall = rng_chance(rng, by_season->rain_chance)
                           ? rng_uniform_event(rng, by_season->rainfall_low, by_season->rainfall_high)
                           : 0.0;
    /* Computed from the raw (unrounded) temperature draw, before rounding
     * is applied to any of the three output fields below -- matching
     * weather.py's `evaporation = base_evaporation + max(0.0, temperature -
     * 25) * 0.005` line, which runs before the returned dict rounds
     * anything. */
    double evaporation = by_season->evaporation + py_max(0.0, temperature - 25) * 0.005;

    WeatherDay result;
    result.season = season;
    result.temperature = py_round_ndigits(temperature, 2);
    result.rainfall = py_round_ndigits(rainfall, 3);
    result.evaporation = py_round_ndigits(evaporation, 3);
    return result;
}

/* simulation/weather.py:47-174 (plain loop; see weather.h's header comment
 * for why this ports that path rather than _fastplotmodule.c's). */
void weather_apply(FarmState *state, const WeatherDay *weather) {
    const ResolvedConfig *config = state->config;
    const SoilDynamics *dynamics = &config->soil_dynamics;
    const PlotRegen *regen = &config->plot_regen;
    double rainfall = weather->rainfall;
    double evaporation = weather->evaporation;
    int day = state->day;

    /* Python: `regen_n or regen_p or regen_k` -- a truthiness gate only,
     * evaluated once per day rather than once per plot per nutrient. */
    bool regenerates_nutrients =
        (regen->nitrogen != 0.0) || (regen->phosphorus != 0.0) || (regen->potassium != 0.0);

    for (size_t i = 0; i < state->plots.count; i++) {
        /* weather.py:130: `plot.moisture = min(1.0, plot.moisture +
         * rainfall + regen_moisture)` -- left-associative, matching C's
         * default grouping for `a + b + c`. Direct column access here (not
         * a gathered PlotState local) is the actual cache-locality win of
         * the SoA layout for this, the hottest per-day loop. */
        state->plots.moisture[i] =
            py_min(1.0, state->plots.moisture[i] + rainfall + regen->moisture);
        if (regenerates_nutrients) {
            if (regen->nitrogen != 0.0) {
                state->plots.nitrogen[i] = py_min(1.0, state->plots.nitrogen[i] + regen->nitrogen);
            }
            if (regen->phosphorus != 0.0) {
                state->plots.phosphorus[i] =
                    py_min(1.0, state->plots.phosphorus[i] + regen->phosphorus);
            }
            if (regen->potassium != 0.0) {
                state->plots.potassium[i] =
                    py_min(1.0, state->plots.potassium[i] + regen->potassium);
            }
        }
        if (regen->soil_health != 0.0) {
            state->plots.soil_health[i] = py_min(1.0, state->plots.soil_health[i] + regen->soil_health);
        }
        if (regen->pest_pressure != 0.0) {
            state->plots.pest_pressure[i] =
                py_max(0.0, state->plots.pest_pressure[i] - regen->pest_pressure);
        }
        if (regen->disease_pressure != 0.0) {
            state->plots.disease_pressure[i] =
                py_max(0.0, state->plots.disease_pressure[i] - regen->disease_pressure);
        }

        if (state->plots.planted_index[i] < 0) {
            /* Fallow: these four fields are always written, unlike the
             * conditional regen writes above (matches weather.py:150-153
             * running unconditionally once a plot is confirmed fallow). */
            state->plots.moisture[i] = clamp01(state->plots.moisture[i] - evaporation);
            state->plots.pest_pressure[i] =
                py_max(0.0, state->plots.pest_pressure[i] * dynamics->fallow_pest_decay);
            state->plots.disease_pressure[i] =
                py_max(0.0, state->plots.disease_pressure[i] * dynamics->fallow_disease_decay);
            state->plots.soil_health[i] =
                py_min(1.0, state->plots.soil_health[i] + dynamics->fallow_soil_health_regen);
            continue;
        }

        size_t planted_index = (size_t)state->plots.planted_index[i];
        const CropDef *crop = config_find_crop(config, state->planted.crop_item_id[planted_index]);

        /* crop_growth_update_stress mutates both its PlantedCrop and
         * PlotState in place, and its signature stays plain pointers
         * (tests/test_physics.c exercises it standalone with no FarmState)
         * -- gather this one plot's and one crop's rows, call, scatter the
         * mutated rows back. */
        PlotState plot_row = plot_columns_get(&state->plots, i);
        PlantedCrop planted_row = planted_crop_columns_get(&state->planted, planted_index);
        crop_growth_update_stress(&planted_row, &plot_row, crop, weather->temperature, evaporation);
        plot_columns_set(&state->plots, i, plot_row);
        planted_crop_columns_set(&state->planted, planted_index, planted_row);

        int overdue =
            day - state->planted.last_watered_day[planted_index] - crop->water_interval_days;
        state->planted.neglect_days[planted_index] = overdue > 0 ? overdue : 0;

        state->plots.disease_pressure[i] = py_min(
            dynamics->max_disease_pressure,
            state->plots.disease_pressure[i] + rainfall * dynamics->disease_growth_per_rainfall);
        state->plots.pest_pressure[i] = py_min(
            dynamics->max_pest_pressure, state->plots.pest_pressure[i] + dynamics->pest_growth_per_day);
    }
}
