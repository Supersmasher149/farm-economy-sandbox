/* Faithful port of agents/profit_optimizer.py. */
#include "agents/profit_optimizer.h"

#include <stdlib.h>

#include "contracts.h"
#include "economy.h"
#include "inventory.h"
#include "vec_util.h"

/* agents/profit_optimizer.py:23-24 */
#define SOIL_HEALTH_FERTILIZE_THRESHOLD 0.5
#define SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR (-3.0)

/* agents/profit_optimizer.py:26-79 */
ItemId profit_optimizer_choose_crop(const Agent *self, const FarmState *state,
                                     const ResolvedConfig *config) {
    (void)self;
    if (config->crop_count == 0) {
        return INVALID_ID;
    }
    const CropDef *candidates[config->crop_count];
    size_t candidate_count = 0;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state) && state->money >= crop->seed_cost) {
            candidates[candidate_count++] = crop;
        }
    }
    size_t matures_count = 0;
    for (size_t i = 0; i < candidate_count; i++) {
        int growth_days = economy_effective_growth_days(candidates[i], state, config);
        if (economy_matures_within_run(growth_days, state)) {
            candidates[matures_count++] = candidates[i];
        }
    }
    candidate_count = matures_count;
    if (candidate_count == 0) {
        return INVALID_ID;
    }

    const ContractRecord *active = NULL;
    for (size_t i = 0; i < state->active_contracts.count; i++) {
        const ContractRecord *contract = &state->active_contracts.data[i];
        if (!contract->resolved && state->day <= contract->deadline_day) {
            active = contract;
            break;
        }
    }
    if (active != NULL) {
        const CropDef *contracted_crop = NULL;
        for (size_t i = 0; i < candidate_count; i++) {
            if (candidates[i]->item_id == active->item_id) {
                contracted_crop = candidates[i];
                break;
            }
        }
        if (contracted_crop != NULL) {
            int days_to_deadline = active->deadline_day - state->day;
            int growth_days = economy_effective_growth_days(contracted_crop, state, config);
            bool still_short =
                contracts_forecast_committed_supply(state, config, active) < contract_remaining(active);
            if (growth_days <= days_to_deadline && still_short) {
                return contracted_crop->item_id;
            }
        }
    }

    static const double RESERVE_FRACTIONS[3] = {1.0, 0.5, 0.25};
    const CropDef *crop = economy_choose_crop_with_relaxed_reserve(
        candidates, candidate_count, state, config, RESERVE_FRACTIONS, 3);
    if (crop != NULL) {
        return crop->item_id;
    }

    const CropDef *fastest = candidates[0];
    for (size_t i = 1; i < candidate_count; i++) {
        if (candidates[i]->growth_days < fastest->growth_days) {
            fastest = candidates[i];
        }
    }
    return fastest->item_id;
}

/* agents/profit_optimizer.py:81-82 */
bool profit_optimizer_should_buy_upgrade(const Agent *self, const FarmState *state,
                                          UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return economy_should_buy_upgrade_within_budget(
        state, upgrade, state->config, ECONOMY_UPGRADE_COOLDOWN_DAYS_DEFAULT,
        ECONOMY_UPGRADE_MIN_PAYBACK_MULTIPLE_DEFAULT,
        ECONOMY_UPGRADE_MAX_CUMULATIVE_SPEND_FRACTION_DEFAULT,
        ECONOMY_UPGRADE_DEFAULT_PAYBACK_HORIZON_DAYS_DEFAULT);
}

/* agents/profit_optimizer.py:84-99 */
bool profit_optimizer_should_fertilize(const Agent *self, const FarmState *state,
                                        int planted_index) {
    (void)self;
    const PlantedCrop *planted = &state->planted.data[planted_index];
    const CropDef *crop = config_find_crop(state->config, planted->crop_item_id);
    const FertilizerConfig *fertilizer = &state->config->fertilizer;

    double marginal_profit = economy_fertilizer_expected_marginal_profit(crop, fertilizer, state->config);
    bool low_soil_health = economy_soil_health_factor(state) < SOIL_HEALTH_FERTILIZE_THRESHOLD;
    if (low_soil_health && marginal_profit > SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR) {
        return state->money >= fertilizer->cost;
    }
    if (!economy_can_spend_with_reserve(state, fertilizer->cost)) {
        return false;
    }
    return marginal_profit > 0;
}

/* agents/profit_optimizer.py:101-110 */
void profit_optimizer_choose_contracts(const Agent *self, const FarmState *state,
                                        const ResolvedConfig *config, ContractDecisionBuffer *out) {
    (void)self;
    for (size_t i = 0; i < state->active_contracts.count; i++) {
        if (!state->active_contracts.data[i].resolved) {
            return; /* any unresolved active contract -> accept nothing */
        }
    }
    const ContractRecord *best = NULL;
    for (size_t i = 0; i < state->contract_offers.count; i++) {
        const ContractRecord *offer = &state->contract_offers.data[i];
        if (offer->resolved) {
            continue;
        }
        if (contracts_is_offer_profitable(state, config, offer) &&
            contracts_is_offer_feasible(state, config, offer)) {
            if (best == NULL || offer->unit_price > best->unit_price) {
                best = offer;
            }
        }
    }
    if (best != NULL) {
        contract_decision_push(out, best->id);
    }
}

typedef struct {
    double margin;
    const RecipeDef *recipe;
    size_t original_index;
} ScoredRecipe;

/* Stable sort by descending margin (ties keep `recipes`' own order) --
 * decorate-with-index since qsort isn't guaranteed stable. */
static int cmp_scored_recipe(const void *a, const void *b) {
    const ScoredRecipe *sa = a;
    const ScoredRecipe *sb = b;
    if (sa->margin != sb->margin) {
        return sa->margin > sb->margin ? -1 : 1;
    }
    return (sa->original_index > sb->original_index) - (sa->original_index < sb->original_index);
}

/* agents/profit_optimizer.py:112-169 */
void profit_optimizer_choose_processing(const Agent *self, const FarmState *state,
                                         const ResolvedConfig *config,
                                         ProcessingDecisionBuffer *out) {
    (void)self;
    int remaining_capacity =
        (state->has_processing_capacity ? state->processing_capacity : 0) -
        (int)state->processing_jobs.count;
    if (remaining_capacity <= 0) {
        return;
    }
    if (config->recipe_count == 0) {
        return;
    }

    ScoredRecipe *profitable = malloc(config->recipe_count * sizeof(ScoredRecipe));
    size_t profitable_count = 0;
    for (size_t i = 0; i < config->recipe_count; i++) {
        const RecipeDef *recipe = &config->recipes[i];
        double input_price =
            state->has_market_price[recipe->input_item_id] ? state->market_prices[recipe->input_item_id] : 0.0;
        double output_price = state->has_market_price[recipe->output_item_id]
                                   ? state->market_prices[recipe->output_item_id]
                                   : 0.0;
        double input_value = input_price * recipe->input_quantity;
        double output_value = output_price * recipe->output_quantity;
        double margin_per_batch = output_value - input_value - recipe->cost;
        if (margin_per_batch > 0) {
            profitable[profitable_count++] =
                (ScoredRecipe){.margin = margin_per_batch, .recipe = recipe, .original_index = i};
        }
    }
    qsort(profitable, profitable_count, sizeof(ScoredRecipe), cmp_scored_recipe);

    typedef struct {
        ItemId input_item_id;
        int reserved;
    } Reservation;
    Reservation *reserved = malloc((profitable_count > 0 ? profitable_count : 1) * sizeof(Reservation));
    size_t reserved_count = 0;
    double cash_remaining = state->money;

    for (size_t i = 0; i < profitable_count && remaining_capacity > 0; i++) {
        const RecipeDef *recipe = profitable[i].recipe;
        ItemId input_item_id = recipe->input_item_id;

        Reservation *slot = NULL;
        for (size_t r = 0; r < reserved_count; r++) {
            if (reserved[r].input_item_id == input_item_id) {
                slot = &reserved[r];
                break;
            }
        }
        int already_reserved = slot != NULL ? slot->reserved : 0;

        int available =
            inventory_available_quantity(state, input_item_id, recipe->min_quality) - already_reserved;
        int max_by_input = int_floor_div(available, recipe->input_quantity);
        double cost_per_batch = recipe->cost;
        int max_by_cash =
            cost_per_batch > 0 ? (int)(cash_remaining / cost_per_batch) : max_by_input;
        int batches = remaining_capacity;
        if (max_by_input < batches) {
            batches = max_by_input;
        }
        if (max_by_cash < batches) {
            batches = max_by_cash;
        }
        if (batches <= 0) {
            continue;
        }

        processing_decision_push(out, (ProcessingDecision){.recipe_id = recipe->id, .batches = batches});
        remaining_capacity -= batches;
        if (slot == NULL) {
            reserved[reserved_count++] =
                (Reservation){.input_item_id = input_item_id,
                               .reserved = already_reserved + batches * recipe->input_quantity};
        } else {
            slot->reserved = already_reserved + batches * recipe->input_quantity;
        }
        cash_remaining -= batches * cost_per_batch;
    }

    free(reserved);
    free(profitable);
}

/* agents/profit_optimizer.py:171-172 */
void profit_optimizer_choose_sales(const Agent *self, const FarmState *state,
                                    const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self;
    agent_route_sales_by_best_price(state, config, out);
}

/* agents/profit_optimizer.py:174-185 */
bool profit_optimizer_should_use_fertilizer(const Agent *self, const FarmState *state,
                                             ItemId crop_item_id) {
    (void)self;
    const CropDef *crop = config_find_crop(state->config, crop_item_id);
    const FertilizerConfig *fertilizer = &state->config->fertilizer;

    double marginal_profit = economy_fertilizer_expected_marginal_profit(crop, fertilizer, state->config);
    bool low_soil_health = economy_soil_health_factor(state) < SOIL_HEALTH_FERTILIZE_THRESHOLD;
    if (low_soil_health && marginal_profit > SOIL_MAINTENANCE_MARGINAL_PROFIT_FLOOR) {
        return state->money >= crop->seed_cost + fertilizer->cost;
    }
    if (!economy_can_spend_with_reserve(state, crop->seed_cost + fertilizer->cost)) {
        return false;
    }
    return marginal_profit > 0;
}

const Agent AGENT_PROFIT_OPTIMIZER = {
    .name = "profit_optimizer",
    .description = "Maximizes expected profit per slot per day; waters reliably; fertilizes only "
                    "when the math says it pays off.",
    .watering_diligence = 1.0,
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
