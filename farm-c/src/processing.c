#include "processing.h"

#include <limits.h>

#include "inventory.h"

/* --- simulation/processing.py:7-58 --- */

bool processing_start_job(FarmState *state, const RecipeDef *recipe, int quantity_batches,
                           int capacity) {
    if (quantity_batches <= 0 || capacity < 0 ||
        (int)state->processing_jobs.count + quantity_batches > capacity) {
        return false;
    }
    if (recipe->input_quantity <= 0 || recipe->output_quantity <= 0) {
        return false;
    }

    if (quantity_batches > INT_MAX / recipe->input_quantity) {
        return false;
    }
    int total_input = recipe->input_quantity * quantity_batches;
    double total_cost = recipe->cost * quantity_batches;
    if (state->money < total_cost) {
        return false;
    }
    if (inventory_available_quantity(state, recipe->input_item_id, recipe->min_quality) <
        total_input) {
        return false;
    }
    if ((size_t)quantity_batches > SIZE_MAX - state->processing_jobs.count ||
        !vec_reserve((void **)&state->processing_jobs.data, &state->processing_jobs.capacity,
                     state->processing_jobs.count + (size_t)quantity_batches,
                     sizeof(ProcessingJob))) {
        farm_state_mark_allocation_failed(state);
        return false;
    }
    int consumed;
    double input_cost;
    inventory_consume(state, recipe->input_item_id, total_input, recipe->min_quality, &consumed,
                       &input_cost);
    if (consumed != total_input) {
        return false;
    }

    state->money -= total_cost;
    farm_state_record_expense(state, EXPENSE_PROCESSING, total_cost);
    for (int i = 0; i < quantity_batches; i++) {
        ProcessingJob job = {
            .recipe_id = recipe->id,
            .output_item_id = recipe->output_item_id,
            .output_quantity = recipe->output_quantity,
            .completion_day = state->day + recipe->processing_days,
            .shelf_life_days = recipe->shelf_life_days,
            .unit_cost = (input_cost / quantity_batches + recipe->cost) / recipe->output_quantity,
        };
        if (!processing_job_vec_push(&state->processing_jobs, job)) {
            farm_state_mark_allocation_failed(state);
            return false;
        }
    }
    return true;
}

/* --- simulation/processing.py:61-82 --- */

int processing_complete_jobs(FarmState *state) {
    int completed = 0;
    size_t completed_jobs = 0;
    for (size_t i = 0; i < state->processing_jobs.count; i++) {
        if (state->day >= state->processing_jobs.data[i].completion_day) completed_jobs++;
    }
    if (completed_jobs > SIZE_MAX - state->inventory_lots.count ||
        !vec_reserve((void **)&state->inventory_lots.data, &state->inventory_lots.capacity,
                     state->inventory_lots.count + completed_jobs, sizeof(InventoryLot))) {
        if (completed_jobs > 0) farm_state_mark_allocation_failed(state);
        return 0;
    }
    size_t write = 0;
    for (size_t read = 0; read < state->processing_jobs.count; read++) {
        ProcessingJob job = state->processing_jobs.data[read];
        if (state->day < job.completion_day) {
            state->processing_jobs.data[write++] = job;
            continue;
        }
        InventoryLot lot = {
            .item_id = job.output_item_id,
            .quantity = job.output_quantity,
            .quality = QUALITY_STANDARD,
            .produced_day = state->day,
            .age_days = 0,
            .shelf_life_days = job.shelf_life_days,
            .effective_shelf_life_days = 0, /* unset -- falls back to shelf_life_days */
            .unit_cost = job.unit_cost,
            .item_type = ITEM_PRODUCT,
        };
        if (!inventory_lot_vec_push(&state->inventory_lots, lot)) {
            farm_state_mark_allocation_failed(state);
            return 0;
        }
        completed += job.output_quantity;
    }
    state->processing_jobs.count = write;
    state->total_processed += completed;
    return completed;
}
