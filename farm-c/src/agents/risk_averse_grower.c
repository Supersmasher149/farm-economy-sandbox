/* Faithful port of agents/risk_averse_grower.py. */
#include "agent.h"

#include "config.h"
#include "economy.h"

/* agents/risk_averse_grower.py:17-31. Tuple-key min by
 * (loss_chance ascending, -expected_profit_per_day ascending i.e. profit
 * descending); ties keep the first candidate encountered, matching
 * Python's min(). */
static ItemId risk_averse_grower_choose_crop(const Agent *self, const FarmState *state,
                                              const ResolvedConfig *config) {
    (void)self;
    const CropDef *best = NULL;
    double best_loss_chance = 0.0;
    double best_neg_profit = 0.0;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (!economy_is_crop_unlocked(crop, state) || state->money < crop->seed_cost) {
            continue;
        }
        double loss_chance = crop->loss_chance;
        double neg_profit = -economy_expected_profit_per_day(crop, state, config);
        bool better = best == NULL || loss_chance < best_loss_chance ||
                      (loss_chance == best_loss_chance && neg_profit < best_neg_profit);
        if (better) {
            best = crop;
            best_loss_chance = loss_chance;
            best_neg_profit = neg_profit;
        }
    }
    return best != NULL ? best->item_id : INVALID_ID;
}

/* agents/risk_averse_grower.py:33-39 */
static bool risk_averse_grower_should_buy_upgrade(const Agent *self, const FarmState *state,
                                                   UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return economy_can_spend_with_reserve(state, upgrade->cost);
}

/* agents/risk_averse_grower.py:41-44 */
static bool risk_averse_grower_should_use_fertilizer(const Agent *self, const FarmState *state,
                                                       ItemId crop_item_id) {
    (void)self;
    const CropDef *crop = config_find_crop(state->config, crop_item_id);
    const FertilizerConfig *fertilizer = &state->config->fertilizer;
    if (state->money < crop->seed_cost + fertilizer->cost) {
        return false;
    }
    return economy_fertilizer_safety_value(crop, fertilizer, state->config) > 0;
}

const Agent AGENT_RISK_AVERSE_GROWER = {
    .name = "risk_averse_grower",
    .description = "Always plants the safest (lowest loss-chance) affordable crop over the most "
                    "profitable one; fertilizes for safety, not yield.",
    .watering_diligence = 1.0,
    .choose_crop = risk_averse_grower_choose_crop,
    .should_buy_upgrade = risk_averse_grower_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = agent_base_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = agent_base_choose_sales,
    .should_use_fertilizer = risk_averse_grower_should_use_fertilizer,
};
