#include "derived.h"

#include <math.h>

int derived_effective_growth_days(const CropDef *crop, const bool *upgrades_owned,
                                   const ResolvedConfig *config) {
    /* derived.py:457-458: the overwhelmingly common case (nothing owned) is
     * a no-op fold, and Python special-cases it purely to skip a cache-key
     * build -- here it's just the same fold with an empty set, so no
     * special case is needed for correctness, only for matching the
     * *shape* of the reference; the result is identical either way. */
    double days = (double)crop->growth_days;
    for (size_t i = 0; i < config->upgrade_count; i++) {
        const UpgradeDef *upgrade = &config->upgrades[i];
        if (!upgrades_owned[upgrade->id]) {
            continue;
        }
        if (upgrade->effect.type != EFFECT_GROWTH_TIME_REDUCTION) {
            continue;
        }
        double reduced = days * (1.0 - upgrade->effect.as.growth_time_reduction);
        /* Python's round() is round-half-to-even; rint() under the default
         * FE_TONEAREST rounding mode (never changed anywhere in this port)
         * matches that. See docs/c-port-plan.md Section 7. */
        double rounded = rint(reduced);
        days = rounded < 1.0 ? 1.0 : rounded;
    }
    return (int)days;
}

double derived_nutrient_demand_total(const CropDef *crop) {
    return crop->nutrient_demand.nitrogen + crop->nutrient_demand.phosphorus +
           crop->nutrient_demand.potassium;
}
