/* Faithful port of agents/neglectful_grower.py: identical decision logic to
 * ProfitOptimizer -- every function pointer below is copied verbatim, none
 * reimplemented -- with only watering_diligence lowered to 0.15 (~1 day in
 * 7). See agents/profit_optimizer.h.
 */
#include "agents/profit_optimizer.h"

/* agents/neglectful_grower.py:11 WATERING_DILIGENCE */
#define WATERING_DILIGENCE 0.15

const Agent AGENT_NEGLECTFUL_GROWER = {
    .name = "neglectful_grower",
    .description = "Picks crops like a profit optimizer but waters only ~15% of days; crops "
                    "accrue neglect and underperform.",
    .watering_diligence = WATERING_DILIGENCE,
    .choose_crop = profit_optimizer_choose_crop,
    .should_buy_upgrade = profit_optimizer_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = profit_optimizer_should_fertilize,
    .choose_contracts = profit_optimizer_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = profit_optimizer_choose_processing,
    .choose_sales = profit_optimizer_choose_sales,
    .should_use_fertilizer = profit_optimizer_should_use_fertilizer,
};
