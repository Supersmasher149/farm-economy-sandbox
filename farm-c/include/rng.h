/* A drop-in, bit-exact port of the subset of CPython's random.Random that
 * simulation/random_events.py:RandomEvents actually uses.
 *
 * Scope, deliberately: this repo only ever seeds with a non-negative
 * integer that fits in 64 bits (runner/batch_run.py mints per-run seeds via
 * `seed_rng.randrange(2**32)`; `--seed` on the CLI is a plain int nobody
 * hands a 200-digit value) and only ever calls randint/uniform/random/
 * choice with small ranges (crop yields, contract quantities, probabilities
 * in [0,1]). So this port implements:
 *   - MT19937 seeded exactly as CPython seeds it for an int argument
 *     (abs(seed) split into little-endian 32-bit words, init_by_array).
 *   - getrandbits(k) for 1 <= k <= 32 only (the "fast path" in
 *     _randommodule.c; nothing here ever needs more than 32 bits).
 *   - _randbelow, randint, uniform, random(), and a choice-index helper.
 * It does not implement getrandbits(k>32), seeding from bytes/str/None,
 * gauss/normalvariate, or any other random.Random method -- none of those
 * are reachable from this codebase's RNG usage. See docs/c-port-plan.md
 * Section 7 ("RNG and Floating-Point Behavior") for why bit-exactness here
 * is the load-bearing decision for the whole port.
 */
#ifndef FARM_RNG_H
#define FARM_RNG_H

#include <stdbool.h>
#include <stdint.h>

#define FARM_RNG_MT_N 624

typedef struct {
    uint32_t mt[FARM_RNG_MT_N];
    int index; /* mirrors CPython RandomObject.index: MT_N once exhausted,
                * forcing a regeneration on the next draw. */
} FarmRng;

/* Seeds exactly as `random.Random(seed)` does for a non-negative int seed
 * (see the header comment above for the scope this covers). */
void rng_seed(FarmRng *rng, uint64_t seed);

/* --- Primitives, matching the named CPython/random.Random routines --- */

/* _randommodule.c's genrand_uint32 fast path: `random.getrandbits(k)` for
 * 1 <= k <= 32. */
uint32_t rng_getrandbits(FarmRng *rng, int k);

/* random.Random()._randbelow(n): a uniform int in [0, n). n must fit in
 * uint32_t; n == 0 returns 0 (matching `_randbelow(0)`). */
uint32_t rng_randbelow(FarmRng *rng, uint32_t n);

/* random.Random().random(): a float in [0.0, 1.0), 53 bits of resolution. */
double rng_random(FarmRng *rng);

/* random.Random().randint(a, b): a uniform int in [a, b] inclusive. b - a
 * must fit in uint32_t. */
int64_t rng_randint(FarmRng *rng, int64_t a, int64_t b);

/* random.Random().uniform(a, b): a + (b - a) * random(), arithmetic order
 * preserved exactly (see docs/c-port-plan.md Section 7 on preserving
 * arithmetic order for float bit-exactness). */
double rng_uniform(FarmRng *rng, double a, double b);

/* Index-selection half of random.Random().choice(seq): seq[rng_choice_index
 * (rng, len(seq))]. Callers own the sequence and index into it themselves
 * (C has no generic sequence type to hand back an element of). */
uint32_t rng_choice_index(FarmRng *rng, uint32_t length);

/* --- simulation/random_events.py:RandomEvents, ported 1:1 --- */

int rng_roll_yield(FarmRng *rng, int min_yield, int max_yield);
double rng_roll_price(FarmRng *rng, double base_price, double variation);
bool rng_roll_loss(FarmRng *rng, double loss_chance);
bool rng_roll_watering(FarmRng *rng, double diligence);
bool rng_chance(FarmRng *rng, double probability);
double rng_uniform_event(FarmRng *rng, double minimum, double maximum);

/* random.Random().randrange(2**32): the one draw runner/batch_run.py's
 * seed_rng.randrange(2**32) makes per job to mint each run's seed from a
 * batch's base seed (see src/batch.c). n = 2**32 has bit_length() 33, one
 * bit past the k<=32 fast path everything else in this file uses -- see
 * rng.c for how the two-word getrandbits(33) this needs is built from two
 * genrand_uint32() draws. */
uint32_t rng_randrange_2_32(FarmRng *rng);

/* /dev/urandom-backed seed generation, with a time(NULL)-based fallback if
 * /dev/urandom can't be opened or read in full. Shared by runner.c (single
 * runs) and batch.c (batch base seeds) -- the one seed-generation helper
 * both need, so it lives alongside rng_seed/rng_randrange_2_32 rather than
 * being duplicated per file. Always returns true (the fallback can't fail);
 * the bool return exists so callers can propagate a seed-generation error
 * uniformly if a future fallback path ever can. */
bool rng_fresh_seed(uint64_t *seed);

#endif /* FARM_RNG_H */
