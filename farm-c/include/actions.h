/* Faithful port of simulation/actions.py's modern-path functions (Phase 2 --
 * see docs/c-port-plan.md's scope decision on the modern vs. legacy
 * `run_day`): buy_seeds, plant_seed, water_crop, buy_fertilizer,
 * fertilize_crop, harvest_mature, buy_upgrade, do_nothing. `water_farm` and
 * `sell_all` back only the legacy (`world=None`) path this port doesn't
 * target and are intentionally not ported.
 *
 * Every `watering`/`fertilizer` config parameter below is required
 * (non-NULL): Python defaults a missing one to DEFAULT_WATERING/
 * DEFAULT_FERTILIZER, but the modern engine path (engine.py's `run_day`
 * with `world` set) always passes an explicit, fully-resolved config from
 * `derived.world_lookups`, so that fallback is unreachable from the path
 * this port targets -- same reasoning as crop_growth.h's fertilizer=NULL
 * case being the one exception (a real Python call site that omits it).
 */
#ifndef FARM_ACTIONS_H
#define FARM_ACTIONS_H

#include "config.h"
#include "rng.h"
#include "state.h"

/* simulation/actions.py:16-25 */
bool actions_buy_seeds(FarmState *state, const CropDef *crop, int quantity);

/* simulation/actions.py:28-65. Finds the first plot with no crop
 * (`plot_index == -1`, iterating state->plots in order -- matches Python's
 * `next(... for index, plot in enumerate(player.plots) ...)`), plants it,
 * and folds the fertilizer's nutrients_added into that plot when
 * `fertilized` is true. */
bool actions_plant_seed(FarmState *state, const CropDef *crop, int growth_days, bool fertilized,
                         const FertilizerConfig *fertilizer);

/* simulation/actions.py:68-82. `planted` must point into
 * `state->planted.data` (or otherwise be a crop whose `plot_index` is
 * valid) -- mutated in place. */
bool actions_water_crop(FarmState *state, PlantedCrop *planted, const WateringConfig *watering);

/* simulation/actions.py:108-118 */
bool actions_buy_fertilizer(FarmState *state, const FertilizerConfig *fertilizer, int quantity);

/* simulation/actions.py:121-135 */
bool actions_fertilize_crop(FarmState *state, PlantedCrop *planted,
                             const FertilizerConfig *fertilizer);

/* simulation/actions.py:138-206 (the modern-path call shape only: `rng,
 * watering_settings, fertilizer_config` -- see actions.py:141-142). Rolls
 * harvest outcomes (RNG-consuming, in `state->planted`'s original order --
 * load-bearing for replay) for every mature crop, appends harvested lots to
 * `state->inventory_lots`, clears each harvested plot, and rebuilds
 * `state->planted` to hold only the still-growing crops in their original
 * relative order (their plots' `planted_index` is updated to match).
 * Returns true if anything was harvested (including a total loss). */
bool actions_harvest_mature(FarmState *state, const ResolvedConfig *config, FarmRng *rng,
                             const WateringConfig *watering, const FertilizerConfig *fertilizer);

/* simulation/actions.py:232-242 */
bool actions_buy_upgrade(FarmState *state, const UpgradeDef *upgrade);

/* simulation/actions.py:245-246 */
void actions_do_nothing(FarmState *state);

#endif /* FARM_ACTIONS_H */
