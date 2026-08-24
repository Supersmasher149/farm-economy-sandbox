/* Faithful port of agents/no_upgrade_player.py: identical to
 * ProfitOptimizer except should_buy_upgrade always returns false. Every
 * other field below is ProfitOptimizer's own function pointer, copied
 * verbatim -- not reimplemented -- matching the Python subclass
 * relationship (see agents/profit_optimizer.h).
 */
#include "agents/profit_optimizer.h"

static bool no_upgrade_player_should_buy_upgrade(const Agent *self, const FarmState *state,
                                                   UpgradeId upgrade_id) {
    (void)self;
    (void)state;
    (void)upgrade_id;
    return false;
}

const Agent AGENT_NO_UPGRADE_PLAYER = {
    .name = "no_upgrade_player",
    .description = "Plays like the profit optimizer but never buys an upgrade -- isolates how "
                    "much upgrades are actually worth.",
    .watering_diligence = 1.0,
    .choose_crop = profit_optimizer_choose_crop,
    .should_buy_upgrade = no_upgrade_player_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = profit_optimizer_should_fertilize,
    .choose_contracts = profit_optimizer_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = profit_optimizer_choose_processing,
    .choose_sales = profit_optimizer_choose_sales,
    .should_use_fertilizer = profit_optimizer_should_use_fertilizer,
};
