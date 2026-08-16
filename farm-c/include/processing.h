/* Faithful port of simulation/processing.py (Phase 2 -- no ported agent
 * ever calls either of these directly, so nothing here existed before). */
#ifndef FARM_PROCESSING_H
#define FARM_PROCESSING_H

#include "config.h"
#include "state.h"

/* simulation/processing.py:7-58. `quantity_batches`/`capacity` are always
 * real ints in this port (unlike Python, which re-validates against bools
 * masquerading as ints -- moot here), so only the *value* checks (positive
 * batches, non-negative capacity, room within capacity, positive recipe
 * quantities, affordable cost, sufficient eligible input inventory) are
 * ported. `capacity` is an explicit parameter, not read from
 * `state->processing_capacity` -- matching Python's own signature, where
 * the engine resolves and passes it in (see engine.py:100/147) rather than
 * this function consulting player state for it itself. */
bool processing_start_job(FarmState *state, const RecipeDef *recipe, int quantity_batches,
                           int capacity);

/* simulation/processing.py:61-82. Returns total output units completed. */
int processing_complete_jobs(FarmState *state);

#endif /* FARM_PROCESSING_H */
