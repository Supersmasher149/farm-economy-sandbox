/* Bit-exact port of simulation/weather.py. weather_apply ports the plain
 * per-plot Python loop line-for-line (weather.py:126-174) -- not the
 * optional `_fastplot` accelerator's local-variable-juggling version -- so
 * every step reads/writes PlotState fields directly in the same order the
 * Python attribute reads/writes happen, which is what makes the two
 * "regen only writes when nonzero" branches (nutrients, soil_health, pest,
 * disease) trivially exact: an untouched field simply isn't written, same
 * as Python's `if regen_x: plot.x = ...`.
 */
#ifndef FARM_WEATHER_H
#define FARM_WEATHER_H

#include "config.h"
#include "rng.h"
#include "state.h"

typedef struct {
    Season season;
    double temperature; /* rounded to 2 decimal places, matching weather.py */
    double rainfall;     /* rounded to 3 decimal places */
    double evaporation;  /* rounded to 3 decimal places */
} WeatherDay;

/* simulation/weather.py:22-23. */
Season weather_season_for_day(int day, int season_length_days);

/* simulation/weather.py:26-44. RNG draw order is load-bearing: temperature,
 * then the rain-chance roll, then (only if it rained) the rainfall draw --
 * see weather.py's own header comment on generate_weather. */
WeatherDay weather_generate(int day, const ResolvedConfig *config, FarmRng *rng);

/* simulation/weather.py:47-174 (the plain, non-accelerated loop -- the
 * reference implementation). Mutates every plot in `state->plots` and every
 * growing crop in `state->planted` in place. `config` is read from
 * `state->config`. */
void weather_apply(FarmState *state, const WeatherDay *weather);

#endif /* FARM_WEATHER_H */
