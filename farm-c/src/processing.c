#include "processing.h"

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

    int total_input = recipe->input_quantity * quantity_batches;
    double total_cost = recipe->cost * quantity_batches;
    if (state->money < total_cost) {
        return false;
    }
    if (inventory_available_quantity(state, recipe->input_item_id, recipe->min_quality) <
        total_input) {
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
        processing_job_vec_push(&state->processing_jobs, job);
    }
    return true;
}

/* --- simulation/processing.py:61-82 --- */

int processing_complete_jobs(FarmState *state) {
    int completed = 0;
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
        inventory_lot_vec_push(&state->inventory_lots, lot);
        completed += job.output_quantity;
    }
    state->processing_jobs.count = write;
    state->total_processed += completed;
    return completed;
}
