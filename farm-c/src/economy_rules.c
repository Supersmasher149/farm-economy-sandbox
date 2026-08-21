#include "economy.h"

#include <string.h>

#include "derived.h"
#include "pyfloat.h"

/* economy_rules.py:146-153 */
#define NUTRIENT_RISK_SENSITIVITY 18.0
#define SAME_FAMILY_REPLANT_DISCOUNT 0.1
/* economy_rules.py:238 */
#define CRITICAL_SOIL_HEALTH 0.35

static double crop_base_price(const CropDef *crop, const ResolvedConfig *config) {
    const ItemDef *item = config_find_item(config, crop->item_id);
    return item != NULL ? item->base_price : 0.0;
}

#define min2 py_min
#define max2 py_max

bool economy_is_crop_unlocked(const CropDef *crop, const FarmState *state) {
    switch (crop->unlock_requirement.type) {
        case UNLOCK_REVENUE:
            return state->total_revenue >= crop->unlock_requirement.revenue_threshold;
        case UNLOCK_UPGRADE:
            return state->upgrades_owned[crop->unlock_requirement.upgrade];
        case UNLOCK_NONE:
        default:
            return true;
    }
}

int economy_effective_growth_days(const CropDef *crop, const FarmState *state,
                                   const ResolvedConfig *config) {
    return derived_effective_growth_days(crop, state->upgrades_owned, config);
}

bool economy_last_executable_day(const FarmState *state, int *out_day) {
    if (!state->has_total_days) {
        return false;
    }
    *out_day = state->total_days - 1;
    return true;
}

int economy_effective_deadline(const FarmState *state, int deadline) {
    int last_day;
    if (!economy_last_executable_day(state, &last_day)) {
        return deadline;
    }
    return deadline < last_day ? deadline : last_day;
}

bool economy_matures_within_run(int growth_days, const FarmState *state) {
    int last_day;
    if (!economy_last_executable_day(state, &last_day)) {
        return true;
    }
    return state->day + growth_days <= last_day;
}

double economy_expected_profit_per_day(const CropDef *crop, const FarmState *state,
                                        const ResolvedConfig *config) {
    double avg_yield = (crop->min_yield + crop->max_yield) / 2.0;
    double avg_revenue = avg_yield * crop_base_price(crop, config) * (1.0 - crop->loss_chance);
    int days = economy_effective_growth_days(crop, state, config);
    double profit = avg_revenue - crop->seed_cost;
    return profit / days;
}

double economy_operating_reserve(const FarmState *state) {
    return max2(0.0, state->operating_reserve);
}

bool economy_can_spend_with_reserve(const FarmState *state, double amount) {
    return state->money >= amount + economy_operating_reserve(state);
}

double economy_fertilizer_expected_marginal_profit(const CropDef *crop,
                                                     const FertilizerConfig *fertilizer,
                                                     const ResolvedConfig *config) {
    double avg_yield = (crop->min_yield + crop->max_yield) / 2.0;
    double original_loss_chance = crop->loss_chance;
    double reduced_loss_chance = max2(0.0, original_loss_chance - fertilizer->loss_chance_reduction);

    double base_price = crop_base_price(crop, config);
    double yield_bonus = avg_yield * fertilizer->yield_bonus_pct;
    double revenue_from_yield_bonus = yield_bonus * base_price * (1.0 - reduced_loss_chance);
    double revenue_from_loss_reduction =
        (original_loss_chance - reduced_loss_chance) * avg_yield * base_price;

    return revenue_from_yield_bonus + revenue_from_loss_reduction - fertilizer->cost;
}

double economy_fertilizer_safety_value(const CropDef *crop, const FertilizerConfig *fertilizer,
                                        const ResolvedConfig *config) {
    double avg_yield = (crop->min_yield + crop->max_yield) / 2.0;
    double original_loss_chance = crop->loss_chance;
    double reduced_loss_chance = max2(0.0, original_loss_chance - fertilizer->loss_chance_reduction);
    double revenue_from_loss_reduction =
        (original_loss_chance - reduced_loss_chance) * avg_yield * crop_base_price(crop, config);
    return revenue_from_loss_reduction - fertilizer->cost;
}

double economy_soil_health_factor(const FarmState *state) {
    if (state->plot_count == 0) {
        return 1.0;
    }
    double sum = 0.0;
    for (size_t i = 0; i < state->plot_count; i++) {
        const PlotState *plot = &state->plots[i];
        sum += min2(plot->nitrogen, min2(plot->phosphorus, plot->potassium));
    }
    return sum / (double)state->plot_count;
}

double economy_soil_quality_risk(const CropDef *crop, const FarmState *state) {
    double health = economy_soil_health_factor(state);
    double demand_weight = derived_nutrient_demand_total(crop);
    double nutrient_risk = (1.0 - health) * demand_weight * NUTRIENT_RISK_SENSITIVITY;

    double same_family_fraction = 0.0;
    if (state->plot_count > 0 && crop->family != NULL) {
        size_t matches = 0;
        for (size_t i = 0; i < state->plot_count; i++) {
            const char *previous = state->plots[i].previous_crop_family;
            if (previous != NULL && strcmp(previous, crop->family) == 0) {
                matches++;
            }
        }
        same_family_fraction = (double)matches / (double)state->plot_count;
    }
    double family_risk = same_family_fraction * SAME_FAMILY_REPLANT_DISCOUNT;

    return min2(0.95, nutrient_risk + family_risk);
}

double economy_quality_adjusted_profit_per_day(const CropDef *crop, const FarmState *state,
                                                const ResolvedConfig *config) {
    double avg_yield = (crop->min_yield + crop->max_yield) / 2.0;
    double avg_revenue = avg_yield * crop_base_price(crop, config) * (1.0 - crop->loss_chance);
    double realized_revenue = avg_revenue * (1.0 - economy_soil_quality_risk(crop, state));

    int days = economy_effective_growth_days(crop, state, config);
    double profit = realized_revenue - crop->seed_cost;
    return profit / days;
}

bool economy_crop_seed_reserve_gate(const CropDef *crop, const FarmState *state,
                                     double reserve_fraction) {
    return state->money >= crop->seed_cost + economy_operating_reserve(state) * reserve_fraction;
}

const CropDef *economy_best_crop_by_expected_profit(const CropDef *const *candidates,
                                                      size_t candidate_count,
                                                      const FarmState *state,
                                                      const ResolvedConfig *config) {
    if (candidate_count == 0) {
        return NULL;
    }
    if (economy_soil_health_factor(state) < CRITICAL_SOIL_HEALTH) {
        const CropDef *best = candidates[0];
        double best_demand = derived_nutrient_demand_total(best);
        for (size_t i = 1; i < candidate_count; i++) {
            double demand = derived_nutrient_demand_total(candidates[i]);
            if (demand < best_demand) {
                best = candidates[i];
                best_demand = demand;
            }
        }
        return best;
    }
    const CropDef *best = candidates[0];
    double best_profit = economy_quality_adjusted_profit_per_day(best, state, config);
    for (size_t i = 1; i < candidate_count; i++) {
        double profit = economy_quality_adjusted_profit_per_day(candidates[i], state, config);
        if (profit > best_profit) {
            best = candidates[i];
            best_profit = profit;
        }
    }
    return best;
}

const CropDef *economy_choose_crop_with_relaxed_reserve(const CropDef *const *candidates,
                                                          size_t candidate_count,
                                                          const FarmState *state,
                                                          const ResolvedConfig *config,
                                                          const double *reserve_fractions,
                                                          size_t fraction_count) {
    if (candidate_count == 0) {
        return NULL;
    }
    const CropDef *safe[candidate_count];
    for (size_t f = 0; f < fraction_count; f++) {
        size_t safe_count = 0;
        for (size_t i = 0; i < candidate_count; i++) {
            if (economy_crop_seed_reserve_gate(candidates[i], state, reserve_fractions[f])) {
                safe[safe_count++] = candidates[i];
            }
        }
        if (safe_count > 0) {
            return economy_best_crop_by_expected_profit(safe, safe_count, state, config);
        }
    }
    return NULL;
}

bool economy_upgrade_payback_days(const UpgradeDef *upgrade, const FarmState *state,
                                   const ResolvedConfig *config, double *out_days) {
    if (config->crop_count == 0) {
        return false;
    }
    const CropDef *unlocked[config->crop_count];
    size_t unlocked_count = 0;
    for (size_t i = 0; i < config->crop_count; i++) {
        if (economy_is_crop_unlocked(&config->crops[i], state)) {
            unlocked[unlocked_count++] = &config->crops[i];
        }
    }
    if (unlocked_count == 0) {
        return false;
    }

    const CropDef *affordable[config->crop_count];
    size_t affordable_count = 0;
    for (size_t i = 0; i < unlocked_count; i++) {
        if (state->money >= unlocked[i]->seed_cost) {
            affordable[affordable_count++] = unlocked[i];
        }
    }
    const CropDef *const *viable = affordable_count > 0 ? affordable : unlocked;
    size_t viable_count = affordable_count > 0 ? affordable_count : unlocked_count;

    double best_profit_per_day = economy_expected_profit_per_day(viable[0], state, config);
    for (size_t i = 1; i < viable_count; i++) {
        double profit = economy_expected_profit_per_day(viable[i], state, config);
        if (profit > best_profit_per_day) {
            best_profit_per_day = profit;
        }
    }
    if (best_profit_per_day <= 0) {
        return false;
    }

    double added_value_per_day;
    switch (upgrade->effect.type) {
        case EFFECT_CAPACITY:
            added_value_per_day = best_profit_per_day * upgrade->effect.as.capacity;
            break;
        case EFFECT_GROWTH_TIME_REDUCTION: {
            double amount = upgrade->effect.as.growth_time_reduction;
            if (amount <= 0.0 || amount >= 1.0) {
                return false;
            }
            const CropDef *best_crop = viable[0];
            double best_crop_profit = economy_expected_profit_per_day(best_crop, state, config);
            for (size_t i = 1; i < viable_count; i++) {
                double profit = economy_expected_profit_per_day(viable[i], state, config);
                if (profit > best_crop_profit) {
                    best_crop = viable[i];
                    best_crop_profit = profit;
                }
            }
            int current_days = economy_effective_growth_days(best_crop, state, config);

            bool hypothetical_owned[config->upgrade_count];
            memcpy(hypothetical_owned, state->upgrades_owned,
                   config->upgrade_count * sizeof(bool));
            hypothetical_owned[upgrade->id] = true;
            int new_days = derived_effective_growth_days(best_crop, hypothetical_owned, config);

            if (new_days >= current_days) {
                return false;
            }
            double avg_yield = (best_crop->min_yield + best_crop->max_yield) / 2.0;
            double avg_revenue =
                avg_yield * crop_base_price(best_crop, config) * (1.0 - best_crop->loss_chance);
            double profit_per_cycle = avg_revenue - best_crop->seed_cost;
            added_value_per_day = state->slots_total * profit_per_cycle *
                                   (1.0 / new_days - 1.0 / current_days);
            break;
        }
        case EFFECT_PROCESSING_CAPACITY:
            added_value_per_day = 0.5 * best_profit_per_day * upgrade->effect.as.processing_capacity;
            break;
        case EFFECT_STORAGE:
        default:
            return false;
    }

    if (added_value_per_day <= 0) {
        return false;
    }
    *out_days = upgrade->cost / added_value_per_day;
    return true;
}

bool economy_should_buy_upgrade_within_budget(const FarmState *state, const UpgradeDef *upgrade,
                                               const ResolvedConfig *config, int cooldown_days,
                                               double min_payback_multiple,
                                               double max_cumulative_spend_fraction,
                                               int default_payback_horizon_days) {
    if (!economy_can_spend_with_reserve(state, upgrade->cost)) {
        return false;
    }

    bool any_purchase = false;
    int max_purchase_day = INVALID_DAY;
    for (size_t i = 0; i < config->upgrade_count; i++) {
        int purchase_day = state->upgrade_purchase_days[i];
        if (purchase_day != INVALID_DAY) {
            any_purchase = true;
            if (purchase_day > max_purchase_day) {
                max_purchase_day = purchase_day;
            }
        }
    }
    if (any_purchase) {
        int days_since_last = state->day - max_purchase_day;
        if (days_since_last < cooldown_days) {
            return false;
        }
    }

    double peak_money =
        state->has_highest_money ? max2(state->highest_money, state->money) : state->money;
    double spent_on_upgrades = state->expenses_by_category[EXPENSE_UPGRADES];
    if (peak_money <= 0 ||
        spent_on_upgrades + upgrade->cost > peak_money * max_cumulative_spend_fraction) {
        return false;
    }

    double payback;
    if (economy_upgrade_payback_days(upgrade, state, config, &payback)) {
        int remaining_days = state->has_total_days ? state->total_days - state->day
                                                     : default_payback_horizon_days;
        if (payback * min_payback_multiple > remaining_days) {
            return false;
        }
    }

    return true;
}
