/* Faithful port of PlayerState.decision_random (simulation/state.py:183-187),
 * used only by RandomAgent's four policy-decision hash draws
 * (agents/random_agent.py:24-46). Python:
 *
 *     def decision_random(self, *context) -> float:
 *         payload = repr((self.run_seed if self.run_seed is not None else 0,
 *                          self.day, context))
 *         digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
 *         return int.from_bytes(digest, "big") / 2**64
 *
 * This is NOT a general Python repr() formatter -- it only reproduces the
 * exact tuple shapes RandomAgent's four call sites pass, over ASCII
 * identifiers with no embedded quote/backslash characters (every config id
 * in this project). See rng_hash.c's string-repr comment for the one piece
 * of Python's quoting algorithm it does still implement in full (which
 * quote character to use), since that's cheap and removes any doubt.
 */
#ifndef FARM_RNG_HASH_H
#define FARM_RNG_HASH_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    REPR_INT,
    REPR_STR,
    REPR_STR_TUPLE,
} ReprValueKind;

typedef struct {
    ReprValueKind kind;
    long int_value;             /* REPR_INT */
    const char *str_value;      /* REPR_STR */
    const char *const *tuple_items; /* REPR_STR_TUPLE */
    size_t tuple_count;              /* REPR_STR_TUPLE */
} ReprValue;

static inline ReprValue repr_int(long value) {
    return (ReprValue){.kind = REPR_INT, .int_value = value};
}
static inline ReprValue repr_str(const char *value) {
    return (ReprValue){.kind = REPR_STR, .str_value = value};
}
static inline ReprValue repr_str_tuple(const char *const *items, size_t count) {
    return (ReprValue){.kind = REPR_STR_TUPLE, .tuple_items = items, .tuple_count = count};
}

/* `run_seed` mirrors `self.run_seed if self.run_seed is not None else 0` --
 * pass has_run_seed=false for a FarmState with no run seed set (matching a
 * bare unit-test PlayerState, where run_seed defaults to None). */
double rng_decision_random(bool has_run_seed, int64_t run_seed, int day, const ReprValue *context,
                            size_t context_count);

#endif /* FARM_RNG_HASH_H */
