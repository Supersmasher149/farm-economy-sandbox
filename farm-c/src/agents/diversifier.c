/* Faithful port of agents/diversifier.py. */
#include "agent.h"

#include "config.h"
#include "economy.h"

/* agents/diversifier.py:16-27. Tuple-key min by (plant_count ascending,
 * seed_cost ascending); ties keep the first candidate encountered. */
static ItemId diversifier_choose_crop(const Agent *self, const FarmState *state,
                                       const ResolvedConfig *config) {
    (void)self;
    const CropDef *best = NULL;
    int best_count = 0;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (!economy_is_crop_unlocked(crop, state) || state->money < crop->seed_cost) {
            continue;
        }
        int plant_count = state->crop_plant_counts[crop->item_id];
        bool better = best == NULL || plant_count < best_count ||
                      (plant_count == best_count && crop->seed_cost < best->seed_cost);
        if (better) {
            best = crop;
            best_count = plant_count;
        }
    }
    return best != NULL ? best->item_id : INVALID_ID;
}

/* agents/diversifier.py:29-30 */
static bool diversifier_should_buy_upgrade(const Agent *self, const FarmState *state,
                                            UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return state->money >= upgrade->cost;
}

/* agents/diversifier.py:32-38 */
static void diversifier_choose_sales(const Agent *self, const FarmState *state,
                                      const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self;
    agent_route_sales_by_best_price(state, config, out);
}

const Agent AGENT_DIVERSIFIER = {
    .name = "diversifier",
    .description = "Always plants whichever unlocked, affordable crop it has used least so far "
                    "-- deliberate portfolio spread instead of monoculture.",
    .watering_diligence = 1.0,
    .choose_crop = diversifier_choose_crop,
    .should_buy_upgrade = diversifier_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = agent_base_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = diversifier_choose_sales,
    .should_use_fertilizer = agent_base_should_use_fertilizer,
};
