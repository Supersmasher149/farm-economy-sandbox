/* Faithful port of agents/reckless_spender.py. */
#include "agent.h"

#include "config.h"
#include "economy.h"

/* agents/reckless_spender.py:17-25 */
static ItemId reckless_spender_choose_crop(const Agent *self, const FarmState *state,
                                            const ResolvedConfig *config) {
    (void)self;
    const CropDef *best = NULL;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state) && state->money >= crop->seed_cost) {
            if (best == NULL || crop->seed_cost > best->seed_cost) {
                best = crop;
            }
        }
    }
    return best != NULL ? best->item_id : INVALID_ID;
}

/* agents/reckless_spender.py:27-28 */
static bool reckless_spender_should_buy_upgrade(const Agent *self, const FarmState *state,
                                                 UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return state->money >= upgrade->cost;
}

/* agents/reckless_spender.py:30-31 */
static bool reckless_spender_should_use_fertilizer(const Agent *self, const FarmState *state,
                                                     ItemId crop_item_id) {
    (void)self;
    const CropDef *crop = config_find_crop(state->config, crop_item_id);
    return state->money >= crop->seed_cost + state->config->fertilizer.cost;
}

const Agent AGENT_RECKLESS_SPENDER = {
    .name = "reckless_spender",
    .description = "Waters reliably but always buys the priciest affordable crop and fertilizes "
                    "on impulse, with no cash reserve.",
    .watering_diligence = 1.0,
    .choose_crop = reckless_spender_choose_crop,
    .should_buy_upgrade = reckless_spender_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = agent_base_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = agent_base_choose_sales,
    .should_use_fertilizer = reckless_spender_should_use_fertilizer,
};
