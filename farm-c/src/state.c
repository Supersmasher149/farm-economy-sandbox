#include "state.h"

#include <stdlib.h>
#include <string.h>

#include "vec_util.h"

#define DEFINE_VEC_PUSH(FnName, VecType, ElemType)                                       \
    bool FnName(VecType *vec, ElemType item) {                                           \
        if (!vec_grow((void **)&vec->data, &vec->capacity, vec->count, sizeof(ElemType))) { \
            return false;                                                                \
        }                                                                                \
        vec->data[vec->count++] = item;                                                 \
        return true;                                                                     \
    }

DEFINE_VEC_PUSH(planted_crop_vec_push, PlantedCropVec, PlantedCrop)
DEFINE_VEC_PUSH(inventory_lot_vec_push, InventoryLotVec, InventoryLot)
DEFINE_VEC_PUSH(processing_job_vec_push, ProcessingJobVec, ProcessingJob)
DEFINE_VEC_PUSH(contract_vec_push, ContractVec, ContractRecord)

#undef DEFINE_VEC_PUSH

void planted_crop_vec_free(PlantedCropVec *vec) {
    free(vec->data);
    *vec = (PlantedCropVec){0};
}

void inventory_lot_vec_free(InventoryLotVec *vec) {
    free(vec->data);
    *vec = (InventoryLotVec){0};
}

void processing_job_vec_free(ProcessingJobVec *vec) {
    free(vec->data);
    *vec = (ProcessingJobVec){0};
}

void contract_vec_free(ContractVec *vec) {
    free(vec->data);
    *vec = (ContractVec){0};
}

void farm_state_init(FarmState *state, const ResolvedConfig *config, double money,
                      int slots_total) {
    memset(state, 0, sizeof(*state));
    state->config = config;
    state->money = money;
    state->slots_total = slots_total;
    state->has_total_days = false;

    state->plot_count = (size_t)slots_total;
    state->plots = calloc(state->plot_count ? state->plot_count : 1, sizeof(PlotState));
    for (size_t i = 0; i < state->plot_count; i++) {
        /* simulation/state.py PlotState field defaults (state.py:37-46). */
        state->plots[i] = (PlotState){
            .moisture = 0.65,
            .nitrogen = 0.75,
            .phosphorus = 0.75,
            .potassium = 0.75,
            .ph = 6.5,
            .soil_health = 0.7,
            .pest_pressure = 0.05,
            .disease_pressure = 0.03,
            .previous_crop_family = NULL,
            .planted_index = -1,
        };
    }

    state->seed_inventory = calloc(config->item_count, sizeof(int));
    state->crop_plant_counts = calloc(config->item_count, sizeof(int));

    state->upgrades_owned = calloc(config->upgrade_count, sizeof(bool));
    state->upgrade_purchase_days = malloc(config->upgrade_count * sizeof(int));
    for (size_t i = 0; i < config->upgrade_count; i++) {
        state->upgrade_purchase_days[i] = INVALID_DAY;
    }

    state->buyer_relationships = calloc(config->buyer_count, sizeof(double));

    state->market_prices = calloc(config->item_count, sizeof(double));
    state->has_market_price = calloc(config->item_count, sizeof(bool));

    state->channel_capacity_used = calloc(config->channel_count, sizeof(int));

    state->has_highest_money = false;

    /* --- Phase 2 fields --- */
    state->market_supply = calloc(config->item_count, sizeof(double));
    state->current_season = SEASON_SPRING; /* matches `.get("season", "spring")` */
    state->revenue_by_channel = calloc(config->channel_count, sizeof(double));
}

void farm_state_destroy(FarmState *state) {
    free(state->plots);
    planted_crop_vec_free(&state->planted);
    inventory_lot_vec_free(&state->inventory_lots);
    processing_job_vec_free(&state->processing_jobs);
    free(state->seed_inventory);
    free(state->crop_plant_counts);
    free(state->upgrades_owned);
    free(state->upgrade_purchase_days);
    contract_vec_free(&state->active_contracts);
    contract_vec_free(&state->contract_offers);
    free(state->buyer_relationships);
    free(state->market_prices);
    free(state->has_market_price);
    free(state->channel_capacity_used);
    free(state->market_supply);
    free(state->revenue_by_channel);
    memset(state, 0, sizeof(*state));
}

/* --- Phase 2 mutation helpers --- */

void farm_state_record_expense(FarmState *state, ExpenseCategory category, double amount) {
    if (amount <= 0) {
        return;
    }
    state->total_expenses += amount;
    state->expenses_by_category[category] += amount;
}

void farm_state_track_peak_cash(FarmState *state) {
    if (!state->has_highest_money || state->money > state->highest_money) {
        state->highest_money = state->money;
        state->has_highest_money = true;
    }
}

bool farm_state_add_slots(FarmState *state, int amount) {
    if (amount <= 0) {
        /* Python's `add_slots` never guards this (a caller only ever passes
         * a positive upgrade effect amount), but a no-op for <= 0 is a
         * strict superset of that behaviour and keeps this safe to call
         * defensively. */
        state->slots_total += amount;
        return true;
    }
    size_t new_count = state->plot_count + (size_t)amount;
    PlotState *grown = realloc(state->plots, new_count * sizeof(PlotState));
    if (grown == NULL) {
        return false;
    }
    state->plots = grown;
    for (size_t i = state->plot_count; i < new_count; i++) {
        /* Same defaults as farm_state_init's initial plots (simulation/
         * state.py PlotState field defaults, state.py:37-46). */
        state->plots[i] = (PlotState){
            .moisture = 0.65,
            .nitrogen = 0.75,
            .phosphorus = 0.75,
            .potassium = 0.75,
            .ph = 6.5,
            .soil_health = 0.7,
            .pest_pressure = 0.05,
            .disease_pressure = 0.03,
            .previous_crop_family = NULL,
            .planted_index = -1,
        };
    }
    state->plot_count = new_count;
    state->slots_total += amount;
    return true;
}
