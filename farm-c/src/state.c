#include "state.h"

#include <limits.h>
#include <math.h>
#include <stdint.h>
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

DEFINE_VEC_PUSH(inventory_lot_vec_push, InventoryLotVec, InventoryLot)
DEFINE_VEC_PUSH(processing_job_vec_push, ProcessingJobVec, ProcessingJob)
DEFINE_VEC_PUSH(contract_vec_push, ContractVec, ContractRecord)

#undef DEFINE_VEC_PUSH

PlantedCrop planted_crop_columns_get(const PlantedCropColumns *cols, size_t i) {
    return (PlantedCrop){
        .crop_item_id = cols->crop_item_id[i],
        .day_planted = cols->day_planted[i],
        .growth_days_required = cols->growth_days_required[i],
        .last_watered_day = cols->last_watered_day[i],
        .neglect_days = cols->neglect_days[i],
        .fertilized = cols->fertilized[i],
        .plot_index = cols->plot_index[i],
        .water_stress = cols->water_stress[i],
        .nutrient_stress = cols->nutrient_stress[i],
        .temperature_stress = cols->temperature_stress[i],
        .pest_stress = cols->pest_stress[i],
        .disease_stress = cols->disease_stress[i],
        .accrued_cost = cols->accrued_cost[i],
    };
}

void planted_crop_columns_set(PlantedCropColumns *cols, size_t i, PlantedCrop value) {
    cols->crop_item_id[i] = value.crop_item_id;
    cols->day_planted[i] = value.day_planted;
    cols->growth_days_required[i] = value.growth_days_required;
    cols->last_watered_day[i] = value.last_watered_day;
    cols->neglect_days[i] = value.neglect_days;
    cols->fertilized[i] = value.fertilized;
    cols->plot_index[i] = value.plot_index;
    cols->water_stress[i] = value.water_stress;
    cols->nutrient_stress[i] = value.nutrient_stress;
    cols->temperature_stress[i] = value.temperature_stress;
    cols->pest_stress[i] = value.pest_stress;
    cols->disease_stress[i] = value.disease_stress;
    cols->accrued_cost[i] = value.accrued_cost;
}

bool planted_crop_columns_reserve(PlantedCropColumns *cols, size_t needed) {
    VecColumn columns[] = {
        {(void **)&cols->crop_item_id, sizeof(*cols->crop_item_id)},
        {(void **)&cols->day_planted, sizeof(*cols->day_planted)},
        {(void **)&cols->growth_days_required, sizeof(*cols->growth_days_required)},
        {(void **)&cols->last_watered_day, sizeof(*cols->last_watered_day)},
        {(void **)&cols->neglect_days, sizeof(*cols->neglect_days)},
        {(void **)&cols->fertilized, sizeof(*cols->fertilized)},
        {(void **)&cols->plot_index, sizeof(*cols->plot_index)},
        {(void **)&cols->water_stress, sizeof(*cols->water_stress)},
        {(void **)&cols->nutrient_stress, sizeof(*cols->nutrient_stress)},
        {(void **)&cols->temperature_stress, sizeof(*cols->temperature_stress)},
        {(void **)&cols->pest_stress, sizeof(*cols->pest_stress)},
        {(void **)&cols->disease_stress, sizeof(*cols->disease_stress)},
        {(void **)&cols->accrued_cost, sizeof(*cols->accrued_cost)},
    };
    return multi_vec_reserve(columns, sizeof(columns) / sizeof(columns[0]), &cols->capacity,
                              needed);
}

bool planted_crop_vec_push(PlantedCropColumns *cols, PlantedCrop item) {
    if (cols->count == SIZE_MAX) {
        return false;
    }
    if (!planted_crop_columns_reserve(cols, cols->count + 1)) {
        return false;
    }
    planted_crop_columns_set(cols, cols->count, item);
    cols->count++;
    return true;
}

void planted_crop_vec_free(PlantedCropColumns *cols) {
    free(cols->crop_item_id);
    free(cols->day_planted);
    free(cols->growth_days_required);
    free(cols->last_watered_day);
    free(cols->neglect_days);
    free(cols->fertilized);
    free(cols->plot_index);
    free(cols->water_stress);
    free(cols->nutrient_stress);
    free(cols->temperature_stress);
    free(cols->pest_stress);
    free(cols->disease_stress);
    free(cols->accrued_cost);
    *cols = (PlantedCropColumns){0};
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

PlotState plot_columns_get(const PlotColumns *cols, size_t i) {
    return (PlotState){
        .moisture = cols->moisture[i],
        .nitrogen = cols->nitrogen[i],
        .phosphorus = cols->phosphorus[i],
        .potassium = cols->potassium[i],
        .ph = cols->ph[i],
        .soil_health = cols->soil_health[i],
        .pest_pressure = cols->pest_pressure[i],
        .disease_pressure = cols->disease_pressure[i],
        .previous_crop_family = cols->previous_crop_family[i],
        .planted_index = cols->planted_index[i],
    };
}

void plot_columns_set(PlotColumns *cols, size_t i, PlotState value) {
    cols->moisture[i] = value.moisture;
    cols->nitrogen[i] = value.nitrogen;
    cols->phosphorus[i] = value.phosphorus;
    cols->potassium[i] = value.potassium;
    cols->ph[i] = value.ph;
    cols->soil_health[i] = value.soil_health;
    cols->pest_pressure[i] = value.pest_pressure;
    cols->disease_pressure[i] = value.disease_pressure;
    cols->previous_crop_family[i] = value.previous_crop_family;
    cols->planted_index[i] = value.planted_index;
}

/* Reallocs every PlotColumns array from old_count to new_count elements (a
 * pure grow -- new_count is always >= old_count, callers never shrink) and
 * default-initializes the new tail [old_count, new_count) to the same
 * per-plot defaults farm_state_init and farm_state_add_slots have always
 * used (simulation/state.py PlotState field defaults, state.py:37-46).
 * Consolidates what was previously two separately-maintained default-value
 * literal blocks into one. On any individual column's realloc failure,
 * columns already grown are left as-is (harmless -- the caller marks
 * allocation_failed and the run terminates via farm_state_destroy) and
 * cols->count is left unchanged. */
static bool plot_columns_resize(PlotColumns *cols, size_t old_count, size_t new_count) {
    if (new_count == old_count) {
        return true;
    }
    if (new_count > SIZE_MAX / sizeof(double) || new_count > SIZE_MAX / sizeof(int) ||
        new_count > SIZE_MAX / sizeof(const char *)) {
        return false;
    }

#define GROW_COLUMN(field, type)                                                    \
    do {                                                                            \
        type *grown = realloc(cols->field, (new_count ? new_count : 1) * sizeof(type)); \
        if (grown == NULL) {                                                        \
            return false;                                                           \
        }                                                                           \
        cols->field = grown;                                                        \
    } while (0)

    GROW_COLUMN(moisture, double);
    GROW_COLUMN(nitrogen, double);
    GROW_COLUMN(phosphorus, double);
    GROW_COLUMN(potassium, double);
    GROW_COLUMN(ph, double);
    GROW_COLUMN(soil_health, double);
    GROW_COLUMN(pest_pressure, double);
    GROW_COLUMN(disease_pressure, double);
    GROW_COLUMN(previous_crop_family, const char *);
    GROW_COLUMN(planted_index, int);

#undef GROW_COLUMN

    for (size_t i = old_count; i < new_count; i++) {
        cols->moisture[i] = 0.65;
        cols->nitrogen[i] = 0.75;
        cols->phosphorus[i] = 0.75;
        cols->potassium[i] = 0.75;
        cols->ph[i] = 6.5;
        cols->soil_health[i] = 0.7;
        cols->pest_pressure[i] = 0.05;
        cols->disease_pressure[i] = 0.03;
        cols->previous_crop_family[i] = NULL;
        cols->planted_index[i] = -1;
    }
    cols->count = new_count;
    return true;
}

static void plot_columns_free(PlotColumns *cols) {
    free(cols->moisture);
    free(cols->nitrogen);
    free(cols->phosphorus);
    free(cols->potassium);
    free(cols->ph);
    free(cols->soil_health);
    free(cols->pest_pressure);
    free(cols->disease_pressure);
    free(cols->previous_crop_family);
    free(cols->planted_index);
    *cols = (PlotColumns){0};
}

bool farm_state_init(FarmState *state, const ResolvedConfig *config, double money,
                     int slots_total) {
    if (state == NULL || config == NULL || slots_total < 0 || !isfinite(money) ||
        money < 0.0)
        return false;
    memset(state, 0, sizeof(*state));
    state->config = config;
    state->money = money;
    state->slots_total = slots_total;
    state->has_total_days = false;
    state->lowest_money = money;
    state->bankruptcy_day = INVALID_DAY;

    if (!plot_columns_resize(&state->plots, 0, (size_t)slots_total)) {
        farm_state_destroy(state);
        return false;
    }

    state->seed_inventory = calloc(config->item_count, sizeof(int));
    state->crop_plant_counts = calloc(config->item_count, sizeof(int));

    state->upgrades_owned = calloc(config->upgrade_count, sizeof(bool));
    state->upgrade_purchase_days = config->upgrade_count <= SIZE_MAX / sizeof(int)
                                       ? malloc(config->upgrade_count * sizeof(int))
                                       : NULL;
    if (config->upgrade_count && state->upgrade_purchase_days == NULL) {
        farm_state_destroy(state);
        return false;
    }
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

    if ((config->item_count && (state->seed_inventory == NULL ||
                                state->crop_plant_counts == NULL ||
                                state->market_prices == NULL ||
                                state->has_market_price == NULL ||
                                state->market_supply == NULL)) ||
        (config->upgrade_count && (state->upgrades_owned == NULL ||
                                   state->upgrade_purchase_days == NULL)) ||
        (config->buyer_count && state->buyer_relationships == NULL) ||
        (config->channel_count && (state->channel_capacity_used == NULL ||
                                   state->revenue_by_channel == NULL))) {
        farm_state_destroy(state);
        return false;
    }
    return true;
}

void farm_state_destroy(FarmState *state) {
    plot_columns_free(&state->plots);
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
    free(state->bankruptcy_reason);
    scratch_buffer_free(&state->scratch_lot_sort);
    scratch_buffer_free(&state->scratch_sell_candidates);
    scratch_buffer_free(&state->scratch_sell_planned);
    /* The per-day decision buffers. engine.c resets these to count 0 rather
     * than freeing them between days, so this is the only place they are
     * released -- including on the failure paths, where the engine returns
     * with a buffer still holding its allocation. */
    contract_decision_free(&state->decide_contracts);
    delivery_decision_free(&state->decide_deliveries);
    processing_decision_free(&state->decide_processing);
    sales_decision_free(&state->decide_sales);
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
        /* Configured upgrade amounts are positive. Treat an invalid direct
         * call as a safe no-op rather than allowing slots_total to underflow. */
        return true;
    }
    if ((size_t)amount > SIZE_MAX - state->plots.count ||
        state->plots.count + (size_t)amount > SIZE_MAX / sizeof(PlotState) ||
        state->slots_total > INT_MAX - amount) {
        farm_state_mark_allocation_failed(state);
        return false;
    }
    size_t new_count = state->plots.count + (size_t)amount;
    if (!plot_columns_resize(&state->plots, state->plots.count, new_count)) {
        farm_state_mark_allocation_failed(state);
        return false;
    }
    state->slots_total += amount;
    return true;
}
