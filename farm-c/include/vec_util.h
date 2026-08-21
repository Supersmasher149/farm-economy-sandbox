/* Generic growable-array backing, shared by every Vec/Buffer type in this
 * port (PlantedCropVec, ContractVec, SalesDecisionBuffer, ...) so the
 * doubling-growth logic exists once per growth discipline. docs/c-port-plan.md
 * Section 9: "centralize vector growth and destruction." Callers keep typed
 * wrapper structs/push functions -- this only backs their data/count/capacity
 * triple. Two disciplines live here: `vec_grow`, which makes room for one
 * more element of a known type, and `scratch_buffer_reserve`, which reaches
 * an arbitrary byte size in one call for a reusable per-call scratch array.
 */
#ifndef FARM_VEC_UTIL_H
#define FARM_VEC_UTIL_H

#include <stdbool.h>
#include <stddef.h>

/* Ensure room for `needed` elements of `elem_size` bytes at `*data`/
 * `*capacity`, growing by doubling (starting at 4). Returns false (and leaves
 * everything unchanged) on allocation failure or size overflow. */
bool vec_reserve(void **data, size_t *capacity, size_t needed, size_t elem_size);

/* Ensure room for one more element of `elem_size` bytes at `*data`/`*capacity`
 * (current live count `count`). */
bool vec_grow(void **data, size_t *capacity, size_t count, size_t elem_size);

/* Floor division (Python's `//`): C's `/` truncates toward zero, so this
 * disagrees with `/` only when exactly one operand is negative. Shared by
 * every faithful port of a Python `//` expression over possibly-negative
 * operands (contracts.c, profit_optimizer.c). `b` must be nonzero. */
int int_floor_div(int a, int b);

/* One reusable allocation, borrowed by a function that needs a short-lived
 * typed array during a single call and would otherwise malloc a fresh one
 * every call (inventory.c/markets.c/contracts.c's per-call decorate-sort
 * buffers). Buffers are declared as separate *named* instances rather than
 * one shared pool, since two typed arrays can be live at once within a
 * single call; each declaration documents which call sites share it and why
 * that is safe. */
typedef struct {
    void *data;
    size_t capacity_bytes;
} ScratchBuffer;

/* Ensures `scratch`'s backing allocation is at least `bytes`, growing (never
 * shrinking) via realloc, doubling from an initial 256 bytes. Returns the
 * buffer pointer, or NULL only on allocation failure or `bytes == 0`.
 * The returned pointer is invalidated by the *next* call that grows this
 * same ScratchBuffer -- callers must finish using it (or copy out) before
 * reserving again. */
void *scratch_buffer_reserve(ScratchBuffer *scratch, size_t bytes);

void scratch_buffer_free(ScratchBuffer *scratch);

#endif /* FARM_VEC_UTIL_H */
