/* Bit-exact MT19937 port of CPython's random.Random. See include/rng.h for
 * scope. The generator core (init_genrand/init_by_array/genrand_uint32) is
 * the standard 2002/1/26 Matsumoto/Nishimura reference algorithm exactly as
 * CPython's Modules/_randommodule.c implements it -- variable names below
 * intentionally match that file so the two can be diffed side by side.
 */
#include "rng.h"

#include <stddef.h>

#define MT_N FARM_RNG_MT_N
#define MT_M 397
#define MATRIX_A 0x9908b0dfU
#define UPPER_MASK 0x80000000U
#define LOWER_MASK 0x7fffffffU

static void init_genrand(FarmRng *rng, uint32_t s) {
    uint32_t *mt = rng->mt;
    mt[0] = s;
    for (int mti = 1; mti < MT_N; mti++) {
        /* Knuth TAOCP Vol2. 3rd Ed. P.106 multiplier. */
        mt[mti] = 1812433253U * (mt[mti - 1] ^ (mt[mti - 1] >> 30)) + (uint32_t)mti;
    }
    rng->index = MT_N;
}

static void init_by_array(FarmRng *rng, const uint32_t *init_key, size_t key_length) {
    init_genrand(rng, 19650218U);
    uint32_t *mt = rng->mt;
    size_t i = 1, j = 0;
    for (size_t k = (MT_N > key_length ? MT_N : key_length); k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + init_key[j] + (uint32_t)j;
        i++;
        j++;
        if (i >= MT_N) {
            mt[0] = mt[MT_N - 1];
            i = 1;
        }
        if (j >= key_length) {
            j = 0;
        }
    }
    for (size_t k = MT_N - 1; k; k--) {
        mt[i] = (mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i;
        i++;
        if (i >= MT_N) {
            mt[0] = mt[MT_N - 1];
            i = 1;
        }
    }
    mt[0] = 0x80000000U; /* MSB is 1, assuring a non-zero initial array. */
}

void rng_seed(FarmRng *rng, uint64_t seed) {
    /* CPython's random_seed(): bits = abs(seed).bit_length(); keymax =
     * max(1, ceil(bits/32)); key[] = abs(seed)'s little-endian 32-bit words.
     * seed is already unsigned/non-negative here (see header comment), so
     * "abs" is a no-op and this only ever needs at most 2 words. */
    uint32_t key[2];
    size_t keymax;
    uint32_t hi = (uint32_t)(seed >> 32);
    if (hi != 0) {
        key[0] = (uint32_t)(seed & 0xffffffffU);
        key[1] = hi;
        keymax = 2;
    } else {
        key[0] = (uint32_t)seed; /* covers seed == 0 too: bits == 0, keymax == 1, key[0] == 0 */
        keymax = 1;
    }
    init_by_array(rng, key, keymax);
}

static uint32_t genrand_uint32(FarmRng *rng) {
    static const uint32_t mag01[2] = {0x0U, MATRIX_A};
    uint32_t *mt = rng->mt;

    if (rng->index >= MT_N) {
        int kk;
        for (kk = 0; kk < MT_N - MT_M; kk++) {
            uint32_t y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + MT_M] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        for (; kk < MT_N - 1; kk++) {
            uint32_t y = (mt[kk] & UPPER_MASK) | (mt[kk + 1] & LOWER_MASK);
            mt[kk] = mt[kk + (MT_M - MT_N)] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        uint32_t y = (mt[MT_N - 1] & UPPER_MASK) | (mt[0] & LOWER_MASK);
        mt[MT_N - 1] = mt[MT_M - 1] ^ (y >> 1) ^ mag01[y & 0x1U];
        rng->index = 0;
    }

    uint32_t y = mt[rng->index++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680U;
    y ^= (y << 15) & 0xefc60000U;
    y ^= (y >> 18);
    return y;
}

uint32_t rng_getrandbits(FarmRng *rng, int k) {
    /* Only the k<=32 fast path from _randommodule.c's random_getrandbits --
     * see include/rng.h scope note. k == 0 (returns 0 in CPython) is never
     * hit here: _randbelow only calls this with k == n.bit_length() for
     * n > 0, which is always >= 1. */
    return genrand_uint32(rng) >> (32 - k);
}

static int bit_length_u32(uint32_t n) {
    int bits = 0;
    while (n) {
        bits++;
        n >>= 1;
    }
    return bits;
}

uint32_t rng_randbelow(FarmRng *rng, uint32_t n) {
    /* random.py's _randbelow_with_getrandbits. */
    if (n == 0) {
        return 0;
    }
    int k = bit_length_u32(n);
    uint32_t r = rng_getrandbits(rng, k);
    while (r >= n) {
        r = rng_getrandbits(rng, k);
    }
    return r;
}

double rng_random(FarmRng *rng) {
    /* genrand_res53: 27 bits from `a`, 26 from `b`, combined into a 53-bit
     * numerator over 2**53. Draw order (a then b) is load-bearing. */
    uint32_t a = genrand_uint32(rng) >> 5;
    uint32_t b = genrand_uint32(rng) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

int64_t rng_randint(FarmRng *rng, int64_t a, int64_t b) {
    /* randint(a, b) == randrange(a, b + 1) == a + _randbelow(b - a + 1). */
    uint64_t width = (uint64_t)(b - a + 1);
    return a + (int64_t)rng_randbelow(rng, (uint32_t)width);
}

double rng_uniform(FarmRng *rng, double a, double b) {
    return a + (b - a) * rng_random(rng);
}

uint32_t rng_choice_index(FarmRng *rng, uint32_t length) {
    return rng_randbelow(rng, length);
}

/* --- simulation/random_events.py:RandomEvents --- */

int rng_roll_yield(FarmRng *rng, int min_yield, int max_yield) {
    return (int)rng_randint(rng, min_yield, max_yield);
}

double rng_roll_price(FarmRng *rng, double base_price, double variation) {
    double factor = 1.0 + rng_uniform(rng, -variation, variation);
    double value = base_price * factor;
    /* Python: max(0.01, value) -- a literal comparison, not fmax(), to
     * match max()'s exact tie/signed-zero/NaN behavior (see
     * docs/c-port-plan.md Section 7 and crop_growth.c's header comment on
     * the same requirement). max(0.01, value) is 0.01 unless value is
     * strictly greater. */
    return value > 0.01 ? value : 0.01;
}

bool rng_roll_loss(FarmRng *rng, double loss_chance) {
    return rng_random(rng) < loss_chance;
}

bool rng_roll_watering(FarmRng *rng, double diligence) {
    return rng_random(rng) < diligence;
}

bool rng_chance(FarmRng *rng, double probability) {
    return rng_random(rng) < probability;
}

double rng_uniform_event(FarmRng *rng, double minimum, double maximum) {
    return rng_uniform(rng, minimum, maximum);
}

uint32_t rng_randrange_2_32(FarmRng *rng) {
    /* random.py's _randbelow_with_getrandbits(2**32): n = 2**32 has
     * bit_length() 33, so CPython draws getrandbits(33) and rejects/redraws
     * whenever the result is >= n. _randommodule.c's k>32 path packs that
     * 33-bit draw from two genrand_uint32() calls into a little-endian word
     * array: word 0 holds a full 32 bits (k=33 >= 32, no shift), word 1
     * holds the remaining k-32=1 bit, taken as that draw's top bit
     * (`r >> (32 - k)` with k=1). Since n is an exact power of two, r >= n
     * exactly when that top bit is 1 -- i.e. exactly when word 1 is
     * nonzero -- so the reject/redraw loop reduces to: draw both words
     * again whenever the second one's top bit is set. */
    for (;;) {
        uint32_t low = genrand_uint32(rng);
        uint32_t high_bit = genrand_uint32(rng) >> 31;
        if (high_bit == 0) return low;
    }
}
