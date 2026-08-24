#include "inventory.h"

#include <math.h>
#include <stdlib.h>

#include "pyfloat.h"

/* --- shared helpers --- */

#define min2 py_min
#define max2 py_max

/* `player.inventory_lots = [lot for lot in player.inventory_lots if
 * lot.quantity > 0]`, in place. Every mutator below that can zero out a
 * lot's quantity calls this afterward, matching each Python call site. */
static void remove_empty_lots(FarmState *state) {
    size_t write = 0;
    for (size_t read = 0; read < state->inventory_lots.count; read++) {
        InventoryLot lot = state->inventory_lots.data[read];
        if (lot.quantity > 0) {
            state->inventory_lots.data[write++] = lot;
        }
    }
    state->inventory_lots.count = write;
}

int inventory_available_quantity(const FarmState *state, ItemId item_id, Quality min_quality) {
    int total = 0;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        if (lot->item_id == item_id && lot->quality >= min_quality) {
            total += lot->quantity;
        }
    }
    return total;
}

/* --- simulation/inventory.py:25-50 consume --- */

/* Decorate-sort-undecorate on (remaining_shelf_life, quality rank), by
 * lot index into state->inventory_lots -- avoids copying InventoryLot
 * structs through the sort (they're the point of the sort, not incidental
 * payload) and gives consume() a plan it applies directly. */
typedef struct {
    size_t lot_index;
    int remaining_shelf_life;
    Quality quality;
} EligibleLot;

static int cmp_eligible_lot(const void *a, const void *b) {
    const EligibleLot *ea = a;
    const EligibleLot *eb = b;
    if (ea->remaining_shelf_life != eb->remaining_shelf_life) {
        return ea->remaining_shelf_life < eb->remaining_shelf_life ? -1 : 1;
    }
    if (ea->quality != eb->quality) {
        return ea->quality < eb->quality ? -1 : 1;
    }
    /* Stable tie-break: Python's sorted() is stable, and both sort keys
     * above can tie between two distinct lots. */
    return (ea->lot_index > eb->lot_index) - (ea->lot_index < eb->lot_index);
}

void inventory_consume(FarmState *state, ItemId item_id, int quantity, Quality min_quality,
                        int *out_consumed, double *out_cost) {
    *out_consumed = 0;
    *out_cost = 0.0;
    if (quantity <= 0) {
        return;
    }

    size_t lot_count = state->inventory_lots.count;
    if (lot_count > SIZE_MAX / sizeof(EligibleLot)) {
        farm_state_mark_allocation_failed(state);
        return;
    }
    EligibleLot *eligible =
        scratch_buffer_reserve(&state->scratch_lot_sort, lot_count * sizeof(EligibleLot));
    if (lot_count > 0 && eligible == NULL) {
        farm_state_mark_allocation_failed(state);
        return;
    }
    size_t eligible_count = 0;
    for (size_t i = 0; i < lot_count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        if (lot->item_id == item_id && lot->quality >= min_quality) {
            eligible[eligible_count++] = (EligibleLot){
                .lot_index = i,
                .remaining_shelf_life = inventory_lot_remaining_shelf_life(lot),
                .quality = lot->quality,
            };
        }
    }
    qsort(eligible, eligible_count, sizeof(EligibleLot), cmp_eligible_lot);

    int consumed = 0;
    double cost = 0.0;
    for (size_t i = 0; i < eligible_count && consumed < quantity; i++) {
        InventoryLot *lot = &state->inventory_lots.data[eligible[i].lot_index];
        int take = quantity - consumed < lot->quantity ? quantity - consumed : lot->quantity;
        lot->quantity -= take;
        consumed += take;
        cost += take * lot->unit_cost;
    }

    remove_empty_lots(state);
    *out_consumed = consumed;
    *out_cost = cost;
}

/* --- simulation/inventory.py:53-57 --- */

double inventory_capture_storage_liability(const FarmState *state, const StorageConfig *storage) {
    bool has_inventory = false;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        if (state->inventory_lots.data[i].quantity > 0) {
            has_inventory = true;
            break;
        }
    }
    return (has_inventory && storage->daily_cost > 0) ? storage->daily_cost : 0.0;
}

/* --- simulation/inventory.py:60-66 --- */

double inventory_collect_storage_liability(FarmState *state, double liability) {
    double charged = min2(max2(0.0, state->money), max2(0.0, liability));
    if (charged) {
        state->money -= charged;
        farm_state_record_expense(state, EXPENSE_STORAGE, charged);
    }
    return charged;
}

/* --- simulation/inventory.py:69-91 _trim_to_capacity (private: mutates
 * quantities only, no bookkeeping -- callers own that, since this can run
 * twice in a day; see the Python docstring). --- */

typedef struct {
    size_t lot_index;
    int remaining_shelf_life;
} TrimLot;

static int cmp_trim_lot(const void *a, const void *b) {
    const TrimLot *ta = a;
    const TrimLot *tb = b;
    if (ta->remaining_shelf_life != tb->remaining_shelf_life) {
        return ta->remaining_shelf_life < tb->remaining_shelf_life ? -1 : 1;
    }
    return (ta->lot_index > tb->lot_index) - (ta->lot_index < tb->lot_index);
}

static int trim_to_capacity(FarmState *state, int capacity) {
    long total = 0;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        total += state->inventory_lots.data[i].quantity;
    }
    long overflow = total - capacity;
    if (overflow <= 0) {
        return 0;
    }

    size_t lot_count = state->inventory_lots.count;
    if (lot_count > SIZE_MAX / sizeof(TrimLot)) {
        farm_state_mark_allocation_failed(state);
        return 0;
    }
    TrimLot *lots = scratch_buffer_reserve(&state->scratch_lot_sort, lot_count * sizeof(TrimLot));
    if (lot_count > 0 && lots == NULL) {
        farm_state_mark_allocation_failed(state);
        return 0;
    }
    for (size_t i = 0; i < lot_count; i++) {
        lots[i] = (TrimLot){
            .lot_index = i,
            .remaining_shelf_life = inventory_lot_remaining_shelf_life(&state->inventory_lots.data[i]),
        };
    }
    qsort(lots, lot_count, sizeof(TrimLot), cmp_trim_lot);

    int spoiled = 0;
    for (size_t i = 0; i < lot_count && overflow > 0; i++) {
        InventoryLot *lot = &state->inventory_lots.data[lots[i].lot_index];
        int removed = (int)(overflow < lot->quantity ? overflow : lot->quantity);
        lot->quantity -= removed;
        overflow -= removed;
        spoiled += removed;
    }
    return spoiled;
}

/* --- simulation/inventory.py:94-114 enforce_storage_capacity --- */

int inventory_enforce_storage_capacity(FarmState *state, int capacity) {
    int spoiled = trim_to_capacity(state, capacity);
    if (spoiled) {
        remove_empty_lots(state);
        state->total_spoiled += spoiled;
        state->spoilage_units += spoiled;
    }
    return spoiled;
}

/* --- simulation/inventory.py:117-155 age_and_spoil --- */

int inventory_age_and_spoil(FarmState *state, const StorageConfig *storage, bool charge_storage) {
    double liability = charge_storage ? inventory_capture_storage_liability(state, storage) : 0.0;
    double multiplier = storage->shelf_life_multiplier;
    int spoiled = 0;
    size_t lot_count = state->inventory_lots.count;
    if (lot_count > SIZE_MAX / sizeof(TrimLot) ||
        (lot_count > 0 &&
         scratch_buffer_reserve(&state->scratch_lot_sort,
                                lot_count * sizeof(TrimLot)) == NULL)) {
        farm_state_mark_allocation_failed(state);
        return 0;
    }

    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        InventoryLot *lot = &state->inventory_lots.data[i];
        double rounded = rint(lot->shelf_life_days * multiplier);
        lot->effective_shelf_life_days = rounded > 1.0 ? (int)rounded : 1;
    }

    /* Python iterates `sorted(player.inventory_lots, key=lambda item:
     * item.remaining_shelf_life)` here, but each lot's aging/downgrade/
     * spoilage below only ever reads and writes that same lot -- no shared
     * state crosses lots in this loop -- so the sort has no effect on the
     * outcome and is intentionally not replicated (unlike _trim_to_capacity
     * above, where the sort *is* load-bearing: it decides which lots absorb
     * a shared overflow budget). Iterating in the vector's own order below
     * is behaviorally identical. */
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        InventoryLot *lot = &state->inventory_lots.data[i];
        if (lot->produced_day >= state->day) {
            continue;
        }
        lot->age_days += 1;
        int effective_life = lot->effective_shelf_life_days;
        double age_ratio = (double)lot->age_days / effective_life;
        if (age_ratio >= 1.0) {
            spoiled += lot->quantity;
            lot->quantity = 0;
        } else if (age_ratio >= 0.5 && lot->quality == QUALITY_PREMIUM) {
            lot->quality = QUALITY_STANDARD;
        } else if (age_ratio >= 0.8 && lot->quality == QUALITY_STANDARD) {
            lot->quality = QUALITY_PROCESSING;
        }
    }

    spoiled += trim_to_capacity(state, storage->capacity);

    remove_empty_lots(state);
    state->total_spoiled += spoiled;
    if (spoiled) {
        state->spoilage_units += spoiled;
    }
    if (charge_storage) {
        inventory_collect_storage_liability(state, liability);
    }
    return spoiled;
}
