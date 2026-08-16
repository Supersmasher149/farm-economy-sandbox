#include "crop_growth.h"

#include <math.h>
#include <string.h>

#include "pyfloat.h"

/* simulation/crop_growth.py:21-64. Ported from _fastplotmodule.c's
 * update_crop_stress (same file's header explains why the summation and
 * clamp forms are load-bearing), adapted from Python-attribute reads to
 * direct CropDef/PlotState field reads -- this port has no per-day-cached
 * CropProfile.flat tuple to index positionally, it just reads CropDef,
 * which already carries every one of these fields resolved (config.h). */
void crop_growth_update_stress(PlantedCrop *planted, PlotState *plot, const CropDef *crop,
                                double temperature, double evaporation) {
    planted->water_stress += py_max(0.0, crop->min_moisture - plot->moisture);

    /* Nutrient shortfall: nitrogen, phosphorus, potassium, in that fixed
     * order -- must match CropProfile.nutrient_demand's iteration order
     * (derived.py:52 DEFAULT_NUTRIENT_DEMAND / crop.get("nutrient_demand")
     * dict order, which is always nitrogen/phosphorus/potassium in every
     * shipped config and every fixture this port is checked against) since
     * the sum is float addition and reordering it perturbs the last bits. */
    double shortfalls[3] = {
        py_max(0.0, crop->nutrient_demand.nitrogen - plot->nitrogen),
        py_max(0.0, crop->nutrient_demand.phosphorus - plot->phosphorus),
        py_max(0.0, crop->nutrient_demand.potassium - plot->potassium),
    };
    double nutrient_shortfall = py_neumaier_sum(shortfalls, 3);

    double ph_stress = 0.0;
    if (plot->ph < crop->ph_low) {
        ph_stress = (crop->ph_low - plot->ph) * 0.1;
    } else if (plot->ph > crop->ph_high) {
        ph_stress = (plot->ph - crop->ph_high) * 0.1;
    }
    planted->nutrient_stress += nutrient_shortfall + ph_stress;

    if (temperature < crop->temperature_low) {
        planted->temperature_stress += (crop->temperature_low - temperature) / 20;
    } else if (temperature > crop->temperature_high) {
        planted->temperature_stress += (temperature - crop->temperature_high) / 20;
    }

    planted->pest_stress += plot->pest_pressure * crop->pest_susceptibility;
    planted->disease_stress += plot->disease_pressure * crop->disease_susceptibility;

    plot->moisture = clamp01(plot->moisture - evaporation);
    plot->nitrogen = clamp01(plot->nitrogen - crop->nutrient_demand.nitrogen);
    plot->phosphorus = clamp01(plot->phosphorus - crop->nutrient_demand.phosphorus);
    plot->potassium = clamp01(plot->potassium - crop->nutrient_demand.potassium);
}

/* simulation/crop_growth.py:67-114. */
void crop_growth_harvest_multipliers(const PlantedCrop *planted, const CropDef *crop,
                                      const PlotState *plot, const FertilizerConfig *fertilizer,
                                      const SoilDynamics *dynamics, double *out_yield_multiplier,
                                      double *out_quality_multiplier) {
    double environmental_stress = planted->water_stress * 0.16 + planted->nutrient_stress * 0.18 +
                                   planted->temperature_stress * 0.12 +
                                   planted->pest_stress * 0.10 + planted->disease_stress * 0.12;
    double quality_stress = environmental_stress + planted->neglect_days * 0.08;

    double yield_multiplier = py_max(0.15, py_min(1.35, 1.0 - environmental_stress));
    double quality_multiplier = py_max(0.0, py_min(1.2, 1.0 - quality_stress * 1.25));

    if (planted->fertilized) {
        quality_multiplier += fertilizer != NULL ? fertilizer->quality_bonus
                                                   : CROP_GROWTH_DEFAULT_FERTILIZER_QUALITY_BONUS;
    }
    if (plot != NULL) {
        if (crop->family != NULL && plot->previous_crop_family != NULL &&
            strcmp(plot->previous_crop_family, crop->family) == 0) {
            yield_multiplier *= dynamics->same_family_yield_penalty;
            quality_multiplier *= dynamics->same_family_quality_penalty;
        }
        yield_multiplier *=
            dynamics->soil_health_yield_floor + plot->soil_health * dynamics->soil_health_yield_span;
    }

    *out_yield_multiplier = py_max(CROP_GROWTH_YIELD_MULTIPLIER_MIN,
                                    py_min(CROP_GROWTH_YIELD_MULTIPLIER_MAX, yield_multiplier));
    *out_quality_multiplier = py_max(CROP_GROWTH_QUALITY_MULTIPLIER_MIN,
                                      py_min(CROP_GROWTH_QUALITY_MULTIPLIER_MAX, quality_multiplier));
}

/* simulation/crop_growth.py:117-124. */
Quality crop_growth_quality_grade(double score) {
    if (score >= 0.9) {
        return QUALITY_PREMIUM;
    }
    if (score >= 0.62) {
        return QUALITY_STANDARD;
    }
    if (score >= 0.3) {
        return QUALITY_PROCESSING;
    }
    return QUALITY_REJECTED;
}

/* simulation/crop_growth.py:127-163. */
bool crop_growth_compute_harvest_outcome(const PlantedCrop *planted, const CropDef *crop,
                                          const WateringConfig *watering,
                                          const FertilizerConfig *fertilizer, FarmRng *rng,
                                          const PlotState *plot, const SoilDynamics *dynamics,
                                          int *out_yield) {
    double loss_bonus = py_min(planted->neglect_days * watering->neglect_loss_chance_penalty_per_day,
                                watering->max_neglect_loss_chance_bonus);
    double loss_chance = crop->loss_chance + loss_bonus;
    if (planted->fertilized) {
        loss_chance -= fertilizer->loss_chance_reduction;
    }
    if (rng_roll_loss(rng, py_max(0.0, py_min(0.95, loss_chance)))) {
        *out_yield = 0;
        return true;
    }

    int base_yield = rng_roll_yield(rng, crop->min_yield, crop->max_yield);
    double yield_multiplier, quality_multiplier;
    crop_growth_harvest_multipliers(planted, crop, plot, fertilizer, dynamics, &yield_multiplier,
                                     &quality_multiplier);
    (void)quality_multiplier; /* compute_harvest_outcome only returns yield --
                                * the Python source discards `_quality` too. */

    if (planted->fertilized) {
        /* config.get("yield_bonus_pct", 0.25): the FertilizerConfig this
         * port carries is always fully resolved at load time (no "field
         * absent" state to fall back from), so fertilizer->yield_bonus_pct
         * is read directly rather than defaulted here again. */
        yield_multiplier = py_max(CROP_GROWTH_YIELD_MULTIPLIER_MIN,
                                   py_min(CROP_GROWTH_YIELD_MULTIPLIER_MAX,
                                          yield_multiplier + fertilizer->yield_bonus_pct));
    }
    double neglect_penalty = py_min(planted->neglect_days * watering->neglect_yield_penalty_per_day,
                                     watering->max_neglect_yield_penalty);

    double raw_yield = base_yield * yield_multiplier * (1 - neglect_penalty);
    /* Python's round() (no ndigits) is round-half-to-even; rint() under the
     * default FE_TONEAREST rounding mode (never changed anywhere in this
     * port) matches that -- same reasoning as derived.c's growth-days fold. */
    double rounded = rint(raw_yield);
    *out_yield = rounded > 0.0 ? (int)rounded : 0;
    return false;
}
