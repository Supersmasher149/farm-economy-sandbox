/* Faithful port of agents/progression_player.py. */
#include "agent.h"

#include "config.h"
#include "contracts.h"
#include "economy.h"

/* agents/progression_player.py:13 RECOVERY_MONEY_MULTIPLE */
#define RECOVERY_MONEY_MULTIPLE 3.0

/* agents/progression_player.py:20-75 */
static ItemId progression_player_choose_crop(const Agent *self, const FarmState *state,
                                              const ResolvedConfig *config) {
    (void)self;
    if (config->crop_count == 0) {
        return INVALID_ID;
    }
    const CropDef *candidates[config->crop_count];
    size_t candidate_count = 0;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state)) {
            candidates[candidate_count++] = crop;
        }
    }
    if (candidate_count == 0) {
        return INVALID_ID;
    }

    double cheapest_cost = candidates[0]->seed_cost;
    for (size_t i = 1; i < candidate_count; i++) {
        if (candidates[i]->seed_cost < cheapest_cost) {
            cheapest_cost = candidates[i]->seed_cost;
        }
    }

    const CropDef *affordable[config->crop_count];
    size_t affordable_count = 0;
    for (size_t i = 0; i < candidate_count; i++) {
        if (state->money >= candidates[i]->seed_cost) {
            affordable[affordable_count++] = candidates[i];
        }
    }
    if (affordable_count == 0) {
        return INVALID_ID;
    }

    double recovery_threshold = economy_operating_reserve(state);
    double scaled_cheapest = cheapest_cost * RECOVERY_MONEY_MULTIPLE;
    if (scaled_cheapest > recovery_threshold) {
        recovery_threshold = scaled_cheapest;
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
        for (size_t i = 0; i < affordable_count; i++) {
            if (affordable[i]->item_id == active->item_id) {
                contracted_crop = affordable[i];
                break;
            }
        }
        if (contracted_crop != NULL) {
            int days_to_deadline = active->deadline_day - state->day;
            int growth_days = economy_effective_growth_days(contracted_crop, state, config);
            bool matures_in_time =
                growth_days <= days_to_deadline && economy_matures_within_run(growth_days, state);
            bool still_short =
                contracts_forecast_committed_supply(state, config, active) < contract_remaining(active);
            if (matures_in_time && still_short) {
                return contracted_crop->item_id;
            }
        }
    }

    if (state->money < recovery_threshold) {
        const CropDef *fastest = affordable[0];
        for (size_t i = 1; i < affordable_count; i++) {
            if (affordable[i]->growth_days < fastest->growth_days) {
                fastest = affordable[i];
            }
        }
        return fastest->item_id;
    }

    const CropDef *safe[config->crop_count];
    size_t safe_count = 0;
    for (size_t i = 0; i < affordable_count; i++) {
        if (economy_crop_seed_reserve_gate(affordable[i], state, 1.0)) {
            safe[safe_count++] = affordable[i];
        }
    }
    if (safe_count == 0) {
        const CropDef *fastest = affordable[0];
        for (size_t i = 1; i < affordable_count; i++) {
            if (affordable[i]->growth_days < fastest->growth_days) {
                fastest = affordable[i];
            }
        }
        return fastest->item_id;
    }

    for (size_t i = 0; i < safe_count; i++) {
        if (safe[i]->role == CROP_ROLE_STANDARD) {
            return safe[i]->item_id;
        }
    }
    const CropDef *cheapest_safe = safe[0];
    for (size_t i = 1; i < safe_count; i++) {
        if (safe[i]->seed_cost < cheapest_safe->seed_cost) {
            cheapest_safe = safe[i];
        }
    }
    return cheapest_safe->item_id;
}

/* agents/progression_player.py:77-78 */
static bool progression_player_should_buy_upgrade(const Agent *self, const FarmState *state,
                                                    UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    return economy_should_buy_upgrade_within_budget(
        state, upgrade, state->config, ECONOMY_UPGRADE_COOLDOWN_DAYS_DEFAULT,
        ECONOMY_UPGRADE_MIN_PAYBACK_MULTIPLE_DEFAULT,
        ECONOMY_UPGRADE_MAX_CUMULATIVE_SPEND_FRACTION_DEFAULT,
        ECONOMY_UPGRADE_DEFAULT_PAYBACK_HORIZON_DAYS_DEFAULT);
}

/* agents/progression_player.py:80-91 */
static bool progression_player_should_fertilize(const Agent *self, const FarmState *state,
                                                  int planted_index) {
    (void)self;
    const PlantedCrop *planted = &state->planted.data[planted_index];
    const CropDef *crop = config_find_crop(state->config, planted->crop_item_id);
    if (crop->role == CROP_ROLE_FAST) {
        return false;
    }
    const FertilizerConfig *fertilizer = &state->config->fertilizer;
    if (!economy_can_spend_with_reserve(state, fertilizer->cost)) {
        return false;
    }
    return economy_fertilizer_expected_marginal_profit(crop, fertilizer, state->config) > 0;
}

/* agents/progression_player.py:93-96 */
static void progression_player_choose_sales(const Agent *self, const FarmState *state,
                                             const ResolvedConfig *config,
                                             SalesDecisionBuffer *out) {
    (void)self;
    agent_route_sales_by_best_price(state, config, out);
}

/* agents/progression_player.py:98-109 */
static void progression_player_choose_contracts(const Agent *self, const FarmState *state,
                                                  const ResolvedConfig *config,
                                                  ContractDecisionBuffer *out) {
    (void)self;
    for (size_t i = 0; i < state->active_contracts.count; i++) {
        if (!state->active_contracts.data[i].resolved) {
            return;
        }
    }
    int affordable_scale = state->slots_total * 3;
    if (affordable_scale < 6) {
        affordable_scale = 6;
    }
    const ContractRecord *best = NULL;
    for (size_t i = 0; i < state->contract_offers.count; i++) {
        const ContractRecord *offer = &state->contract_offers.data[i];
        if (offer->resolved) {
            continue;
        }
        if (offer->quantity <= affordable_scale &&
            contracts_is_offer_profitable(state, config, offer) &&
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

const Agent AGENT_PROGRESSION_PLAYER = {
    .name = "progression_player",
    .description = "Saves toward both upgrades, falls back to a fast crop to recover when low; "
                    "waters reliably.",
    .watering_diligence = 1.0,
    .choose_crop = progression_player_choose_crop,
    .should_buy_upgrade = progression_player_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = progression_player_should_fertilize,
    .choose_contracts = progression_player_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = progression_player_choose_sales,
    .should_use_fertilizer = agent_base_should_use_fertilizer,
};
