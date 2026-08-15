/* Generic growable-array backing, shared by every Vec/Buffer type in this
 * port (PlantedCropVec, ContractVec, SalesDecisionBuffer, ...) so the
 * doubling-growth logic exists exactly once. docs/c-port-plan.md Section 9:
 * "centralize vector growth and destruction." Callers keep typed wrapper
 * structs/push functions -- this only backs their data/count/capacity triple.
 */
#ifndef FARM_VEC_UTIL_H
#define FARM_VEC_UTIL_H

#include <stdbool.h>
#include <stddef.h>

/* Ensure room for one more element of `elem_size` bytes at `*data`/`*capacity`
 * (current live count `count`), growing by doubling (starting at 4). Returns
 * false (and leaves everything unchanged) only on allocation failure. */
bool vec_grow(void **data, size_t *capacity, size_t count, size_t elem_size);

/* Floor division (Python's `//`): C's `/` truncates toward zero, so this
 * disagrees with `/` only when exactly one operand is negative. Shared by
 * every faithful port of a Python `//` expression over possibly-negative
 * operands (contracts.c, profit_optimizer.c). `b` must be nonzero. */
int int_floor_div(int a, int b);

#endif /* FARM_VEC_UTIL_H */
