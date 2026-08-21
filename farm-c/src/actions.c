#include "actions.h"

#include "crop_growth.h"
#include "pyfloat.h"

/* --- simulation/actions.py:16-25 --- */

bool actions_buy_seeds(FarmState *state, const CropDef *crop, int quantity) {
    if (quantity <= 0) {
        return false;
    }
    double cost = crop->seed_cost * quantity;
    if (state->money < cost) {
        return false;
    }
    state->money -= cost;
    farm_state_record_expense(state, EXPENSE_SEEDS, cost);
    state->seed_inventory[crop->item_id] += quantity;
    return true;
}

/* --- simulation/actions.py:28-65 --- */

bool actions_plant_seed(FarmState *state, const CropDef *crop, int growth_days, bool fertilized,
                         const FertilizerConfig *fertilizer) {
    if (farm_state_open_slots(state) <= 0 || state->seed_inventory[crop->item_id] <= 0) {
        return false;
    }
    if (fertilized && state->fertilizer_inventory <= 0) {
        return false;
    }
    int plot_index = -1;
    for (size_t i = 0; i < state->plot_count; i++) {
        if (state->plots[i].planted_index == -1) {
            plot_index = (int)i;
            break;
        }
    }
    if (plot_index == -1) {
        return false;
    }

    PlantedCrop planted = {
        .crop_item_id = crop->item_id,
        .day_planted = state->day,
        .growth_days_required = growth_days,
        .last_watered_day = state->day, /* PlantedCrop.__post_init__ */
        .neglect_days = 0,
        .fertilized = fertilized,
        .plot_index = plot_index,
        .accrued_cost = crop->seed_cost + (fertilized ? fertilizer->cost : 0.0),
    };
    if (!planted_crop_vec_push(&state->planted, planted)) {
        farm_state_mark_allocation_failed(state);
        return false;
    }
    int new_index = (int)state->planted.count - 1;

    state->seed_inventory[crop->item_id] -= 1;
    if (fertilized) {
        state->fertilizer_inventory -= 1;
        state->total_fertilizer_applied += 1;
    }
    state->plots[plot_index].planted_index = new_index;
    if (fertilized) {
        PlotState *plot = &state->plots[plot_index];
        plot->nitrogen = py_min(1.0, plot->nitrogen + fertilizer->nutrients_added.nitrogen);
        plot->phosphorus = py_min(1.0, plot->phosphorus + fertilizer->nutrients_added.phosphorus);
        plot->potassium = py_min(1.0, plot->potassium + fertilizer->nutrients_added.potassium);
    }
    state->total_planted += 1;
    state->crop_plant_counts[crop->item_id] += 1;
    return true;
}

/* --- simulation/actions.py:68-82 --- */

bool actions_water_crop(FarmState *state, PlantedCrop *planted, const WateringConfig *watering) {
    double cost = watering->cost_per_plot;
    if (state->money < cost) {
        return false;
    }
    if (planted->plot_index < 0 || (size_t)planted->plot_index >= state->plot_count) {
        return false;
    }
    PlotState *plot = &state->plots[planted->plot_index];
    state->money -= cost;
    farm_state_record_expense(state, EXPENSE_WATERING, cost);
    planted->accrued_cost += cost;
    planted->last_watered_day = state->day;
    planted->neglect_days = 0;
    plot->moisture = py_min(1.0, plot->moisture + watering->moisture_added);
    state->total_waterings += 1;
    return true;
}

/* --- simulation/actions.py:108-118 --- */

bool actions_buy_fertilizer(FarmState *state, const FertilizerConfig *fertilizer, int quantity) {
    if (quantity <= 0) {
        return false;
    }
    double cost = fertilizer->cost * quantity;
    if (state->money < cost) {
        return false;
    }
    state->money -= cost;
    farm_state_record_expense(state, EXPENSE_FERTILIZER, cost);
    state->fertilizer_inventory += quantity;
    state->total_fertilizer_bought += quantity;
    return true;
}

/* --- simulation/actions.py:121-135 --- */

bool actions_fertilize_crop(FarmState *state, PlantedCrop *planted,
                             const FertilizerConfig *fertilizer) {
    if (planted->fertilized || state->fertilizer_inventory <= 0) {
        return false;
    }
    planted->fertilized = true;
    state->fertilizer_inventory -= 1;
    state->total_fertilizer_applied += 1;
    planted->accrued_cost += fertilizer->cost;
    if (planted->plot_index >= 0 && (size_t)planted->plot_index < state->plot_count) {
        PlotState *plot = &state->plots[planted->plot_index];
        plot->nitrogen = py_min(1.0, plot->nitrogen + fertilizer->nutrients_added.nitrogen);
        plot->phosphorus = py_min(1.0, plot->phosphorus + fertilizer->nutrients_added.phosphorus);
        plot->potassium = py_min(1.0, plot->potassium + fertilizer->nutrients_added.potassium);
    }
    return true;
}

/* --- simulation/actions.py:138-206 harvest_mature (modern-path shape) --- */

bool actions_harvest_mature(FarmState *state, const ResolvedConfig *config, FarmRng *rng,
                             const WateringConfig *watering, const FertilizerConfig *fertilizer) {
    bool harvested_any = false;
    size_t mature_count = 0;
    for (size_t i = 0; i < state->planted.count; i++) {
        const PlantedCrop *planted = &state->planted.data[i];
        if (state->day - planted->day_planted >= planted->growth_days_required)
            mature_count++;
    }
    if (mature_count > 0 &&
        (mature_count > SIZE_MAX - state->inventory_lots.count ||
        !vec_reserve((void **)&state->inventory_lots.data, &state->inventory_lots.capacity,
                     state->inventory_lots.count + mature_count, sizeof(InventoryLot)))) {
        farm_state_mark_allocation_failed(state);
        return false;
    }
    /* In-place write-pointer compaction on state->planted itself, instead
     * of rebuilding into a fresh vector and swapping -- same pattern as
     * inventory.c's remove_empty_lots/processing.c's processing_complete_
     * jobs. write <= read always holds, and `planted` below is copied out
     * of state->planted.data[read] before any write, so writing through
     * data[write] (write < read) never corrupts an element not yet read. */
    size_t write = 0;

    /* Iterated in state->planted's original order, not plot order: RNG
     * draws happen below (compute_harvest_outcome) only for mature crops,
     * in this exact sequence, so reordering would silently desynchronize
     * replay for every recorded seed -- see actions.h's header comment. */
    for (size_t read = 0; read < state->planted.count; read++) {
        PlantedCrop planted = state->planted.data[read];
        bool mature = (state->day - planted.day_planted) >= planted.growth_days_required;
        if (!mature) {
            state->planted.data[write] = planted;
            if (planted.plot_index >= 0 && (size_t)planted.plot_index < state->plot_count) {
                state->plots[planted.plot_index].planted_index = (int)write;
            }
            write++;
            continue;
        }

        harvested_any = true;
        state->total_harvest_events += 1;
        const CropDef *crop = config_find_crop(config, planted.crop_item_id);
        PlotState *plot = (planted.plot_index >= 0 && (size_t)planted.plot_index < state->plot_count)
                               ? &state->plots[planted.plot_index]
                               : NULL;

        int amount;
        bool lost = crop_growth_compute_harvest_outcome(&planted, crop, watering, fertilizer, rng,
                                                          plot, &config->soil_dynamics, &amount);
        if (lost || amount <= 0) {
            state->total_crops_lost += 1;
            state->crop_loss_events += 1;
        } else {
            double yield_multiplier, quality_score;
            crop_growth_harvest_multipliers(&planted, crop, plot, fertilizer,
                                             &config->soil_dynamics, &yield_multiplier,
                                             &quality_score);
            Quality grade = crop_growth_quality_grade(quality_score);
            if (grade != QUALITY_REJECTED) {
                InventoryLot lot = {
                    .item_id = planted.crop_item_id,
                    .quantity = amount,
                    .quality = grade,
                    .produced_day = state->day,
                    .age_days = 0,
                    .shelf_life_days = crop->shelf_life_days,
                    .effective_shelf_life_days = 0, /* unset -- falls back to shelf_life_days */
                    /* Real cash spent on this planting (seed + fertilizer +
                     * watering), not seed cost alone. */
                    .unit_cost = planted.accrued_cost / amount,
                    .item_type = ITEM_CROP,
                };
                if (!inventory_lot_vec_push(&state->inventory_lots, lot)) {
                    farm_state_mark_allocation_failed(state);
                    return false;
                }
                state->total_harvested += amount;
                state->quality_harvested[grade] += amount;
            } else {
                state->total_crops_lost += 1;
                state->rejected_quality_units += amount;
            }
        }

        if (plot != NULL) {
            /* `crop.get("family", crop["id"])`: a family-less crop's plot
             * remembers the crop's own item id instead, so a later
             * same-family rotation check can never accidentally match a
             * different family-less crop. */
            const ItemDef *item = config_find_item(config, crop->item_id);
            plot->previous_crop_family = crop->family != NULL ? crop->family : item->external_id;
            plot->planted_index = -1;
            plot->soil_health = py_max(config->soil_dynamics.min_soil_health,
                                       plot->soil_health - config->soil_dynamics.harvest_soil_health_cost);
        }
    }

    state->planted.count = write;
    return harvested_any;
}

/* --- simulation/actions.py:232-242 --- */

bool actions_buy_upgrade(FarmState *state, const UpgradeDef *upgrade) {
    if (state->upgrades_owned[upgrade->id] || state->money < upgrade->cost) {
        return false;
    }
    if (upgrade->effect.type == EFFECT_CAPACITY &&
        !farm_state_add_slots(state, upgrade->effect.as.capacity)) {
        return false;
    }
    state->money -= upgrade->cost;
    farm_state_record_expense(state, EXPENSE_UPGRADES, upgrade->cost);
    state->upgrades_owned[upgrade->id] = true;
    state->upgrade_purchase_days[upgrade->id] = state->day;
    return true;
}

/* --- simulation/actions.py:245-246 --- */

void actions_do_nothing(FarmState *state) {
    state->idle_days += 1;
}
