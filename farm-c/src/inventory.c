#include "inventory.h"

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
