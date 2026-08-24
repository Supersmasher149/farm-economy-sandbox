/* Faithful port of agents/upgrade_rusher.py. */
#include "agent.h"

#include "config.h"
#include "economy.h"

/* agents/upgrade_rusher.py:16-24 */
static ItemId upgrade_rusher_choose_crop(const Agent *self, const FarmState *state,
                                          const ResolvedConfig *config) {
    (void)self;
    const CropDef *best = NULL;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state) && state->money >= crop->seed_cost) {
            if (best == NULL || crop->seed_cost < best->seed_cost) {
                best = crop;
            }
        }
    }
    return best != NULL ? best->item_id : INVALID_ID;
}

/* agents/upgrade_rusher.py:26-27 */
static bool upgrade_rusher_should_buy_upgrade(const Agent *self, const FarmState *state,
                                               UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return state->money >= upgrade->cost;
}

/* agents/upgrade_rusher.py:29-34 */
static void upgrade_rusher_choose_sales(const Agent *self, const FarmState *state,
                                         const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self;
    agent_route_sales_by_best_price(state, config, out);
}

const Agent AGENT_UPGRADE_RUSHER = {
    .name = "upgrade_rusher",
    .description = "Always plants the cheapest affordable crop to hoard cash, and buys every "
                    "upgrade the moment it's affordable.",
    .watering_diligence = 1.0,
    .choose_crop = upgrade_rusher_choose_crop,
    .should_buy_upgrade = upgrade_rusher_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = agent_base_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = upgrade_rusher_choose_sales,
    .should_use_fertilizer = agent_base_should_use_fertilizer,
};
