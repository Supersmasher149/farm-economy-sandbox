/* Faithful port of the one function ported agents call directly from
 * simulation/inventory.py: `available_quantity`. Note this does *not* check
 * remaining shelf life (unlike simulation/contracts.py's own private
 * `_inventory_quantity`, which does -- see contracts.h/.c, a separate
 * function, not a caller of this one, exactly as in Python).
 */
#ifndef FARM_INVENTORY_H
#define FARM_INVENTORY_H

#include "config.h"
#include "state.h"

/* simulation/inventory.py:16-22 */
int inventory_available_quantity(const FarmState *state, ItemId item_id, Quality min_quality);

#endif /* FARM_INVENTORY_H */
