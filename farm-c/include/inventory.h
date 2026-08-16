/* Faithful port of simulation/inventory.py: `available_quantity` (Phase 0,
 * the one function ported agents call directly -- note this does *not*
 * check remaining shelf life, unlike simulation/contracts.py's own private
 * `_inventory_quantity`, which does -- see contracts.h/.c, a separate
 * function, not a caller of this one, exactly as in Python), plus Phase 2's
 * mutators: `consume` (FEFO), `capture_storage_liability`,
 * `collect_storage_liability`, `enforce_storage_capacity`, `age_and_spoil`.
 */
#ifndef FARM_INVENTORY_H
#define FARM_INVENTORY_H

#include "config.h"
#include "state.h"

/* simulation/inventory.py:16-22 */
int inventory_available_quantity(const FarmState *state, ItemId item_id, Quality min_quality);

/* simulation/inventory.py:25-50. FEFO (first-expired, first-out): consumes
 * from eligible lots sorted by (remaining_shelf_life, quality rank)
 * ascending -- soonest-to-spoil stock first, ties broken toward the lower
 * grade, matching Python's sort key exactly. Zero-quantity lots are dropped
 * from `state->inventory_lots` afterward, same as Python's list-rebuild.
 * `*out_consumed`/`*out_cost` are always written (0/0.0 for quantity <= 0,
 * matching `is_positive_int`'s reject-and-return-(0, 0.0) in Python --
 * moot in C, where `quantity` is already a real int, not a bool-that-acts-
 * like-an-int). */
void inventory_consume(FarmState *state, ItemId item_id, int quantity, Quality min_quality,
                        int *out_consumed, double *out_cost);

/* simulation/inventory.py:53-57 */
double inventory_capture_storage_liability(const FarmState *state, const StorageConfig *storage);

/* simulation/inventory.py:60-66. Never lets `state->money` go negative. */
double inventory_collect_storage_liability(FarmState *state, double liability);

/* simulation/inventory.py:94-114. Trims the soonest-to-expire (FEFO) lots'
 * quantities until total inventory is at or under `capacity`, records the
 * spoiled units into `state->total_spoiled`/`state->spoilage_units`, and
 * drops any lot trimmed to zero. Returns units spoiled by this call. */
int inventory_enforce_storage_capacity(FarmState *state, int capacity);

/* simulation/inventory.py:117-155. Ages every lot a day, downgrades quality
 * at the 50%/80% shelf-life thresholds, spoils anything at or past 100%,
 * then re-applies the same capacity trim `inventory_enforce_storage_capacity`
 * does (folded into this call's own spoilage/bookkeeping rather than a
 * second nested call, matching Python's single combined tally). Charges the
 * day's storage liability via `inventory_capture_storage_liability` +
 * `inventory_collect_storage_liability` when `charge_storage` is true.
 * Returns total units spoiled (aging + capacity trim). */
int inventory_age_and_spoil(FarmState *state, const StorageConfig *storage, bool charge_storage);

#endif /* FARM_INVENTORY_H */
