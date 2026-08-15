/* Faithful port of agents/fertilizer_maximalist.py: identical to
 * ProfitOptimizer except should_use_fertilizer/should_fertilize ignore the
 * marginal-profit math and fertilize whenever affordable. Every other field
 * is ProfitOptimizer's own function pointer, copied verbatim -- see
 * agents/profit_optimizer.h.
 */
#include "agents/profit_optimizer.h"

#include "config.h"

/* agents/fertilizer_maximalist.py:15-16 */
static bool fertilizer_maximalist_should_use_fertilizer(const Agent *self, const FarmState *state,
                                                          ItemId crop_item_id) {
    (void)self;
    const CropDef *crop = config_find_crop(state->config, crop_item_id);
    return state->money >= crop->seed_cost + state->config->fertilizer.cost;
}

/* agents/fertilizer_maximalist.py:18-19 */
static bool fertilizer_maximalist_should_fertilize(const Agent *self, const FarmState *state,
                                                     int planted_index) {
    (void)self;
    (void)state;
    (void)planted_index;
    return true;
}

const Agent AGENT_FERTILIZER_MAXIMALIST = {
    .name = "fertilizer_maximalist",
    .description = "Plays like the profit optimizer but fertilizes every planting it can afford, "
                    "math be damned -- isolates fertilizer ROI.",
    .watering_diligence = 1.0,
    .choose_crop = profit_optimizer_choose_crop,
    .should_buy_upgrade = profit_optimizer_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = fertilizer_maximalist_should_fertilize,
    .choose_contracts = profit_optimizer_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = profit_optimizer_choose_processing,
    .choose_sales = profit_optimizer_choose_sales,
    .should_use_fertilizer = fertilizer_maximalist_should_use_fertilizer,
};
