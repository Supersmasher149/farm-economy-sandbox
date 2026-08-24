/* Bit-exact port of simulation/crop_growth.py -- see docs/c-port-plan.md
 * Section 7 and farm-c/README.md's Phase 1 note. update_crop_stress reuses
 * the arithmetic already proven in simulation/_fastplotmodule.c (same
 * Neumaier summation, same literal max/min clamp forms, same expression
 * grouping); harvest_multipliers/quality_grade/compute_harvest_outcome are
 * fresh ports -- the fastplot kernel doesn't cover them.
 */
#ifndef FARM_CROP_GROWTH_H
#define FARM_CROP_GROWTH_H

#include "config.h"
#include "rng.h"
#include "state.h"

/* simulation/crop_growth.py:9-14 module constants. */
#define CROP_GROWTH_YIELD_MULTIPLIER_MIN 0.1
#define CROP_GROWTH_YIELD_MULTIPLIER_MAX 1.5
#define CROP_GROWTH_QUALITY_MULTIPLIER_MIN 0.0
#define CROP_GROWTH_QUALITY_MULTIPLIER_MAX 1.25
#define CROP_GROWTH_DEFAULT_FERTILIZER_QUALITY_BONUS 0.05

/* simulation/crop_growth.py:21-64 update_crop_stress. Accumulates today's
 * stress onto `planted` and depletes `plot`'s moisture/nutrients in place.
 * `temperature`/`evaporation` are the day's weather.generate_weather()
 * output fields of the same name (the only two update_crop_stress reads).
 */
void crop_growth_update_stress(PlantedCrop *planted, PlotState *plot, const CropDef *crop,
                                double temperature, double evaporation);

/* simulation/crop_growth.py:67-114. `plot` may be NULL (Python's
 * `plot=None`), in which case the family-rotation and soil-health terms are
 * skipped, matching `if plot is not None:` exactly. */
void crop_growth_harvest_multipliers(const PlantedCrop *planted, const CropDef *crop,
                                      const PlotState *plot, const FertilizerConfig *fertilizer,
                                      const SoilDynamics *dynamics, double *out_yield_multiplier,
                                      double *out_quality_multiplier);

/* simulation/crop_growth.py:117-124. */
Quality crop_growth_quality_grade(double score);

/* simulation/crop_growth.py:127-163. Returns true for a lost harvest
 * (matching Python's `(True, 0)`, with *out_yield left at 0); `plot` may be
 * NULL exactly as harvest_multipliers allows. */
bool crop_growth_compute_harvest_outcome(const PlantedCrop *planted, const CropDef *crop,
                                          const WateringConfig *watering,
                                          const FertilizerConfig *fertilizer, FarmRng *rng,
                                          const PlotState *plot, const SoilDynamics *dynamics,
                                          int *out_yield);

#endif /* FARM_CROP_GROWTH_H */
