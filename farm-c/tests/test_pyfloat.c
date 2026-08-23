/* py_round_ndigits: differential test against its own definition.
 *
 * pyfloat.c answers round(x, n) from exact 128-bit integer arithmetic where
 * it can, and falls back to the snprintf("%.*f")/strtod round-trip
 * everywhere else -- see that file for why (the libc round-trip takes a
 * process-wide locale lock on Darwin, which serializes a parallel batch).
 *
 * The fast path is only allowed to exist if it is indistinguishable from the
 * round-trip, so this file does not assert a table of expected values: it
 * recomputes the reference here, the same way pyfloat.c's fallback does, and
 * demands bit-identical results over a wide sweep. Comparison is by bit
 * pattern, never `==`, because 0.0 == -0.0 and round(-0.0001, 2) must stay
 * negative zero exactly as Python's round() leaves it.
 *
 * `make test` runs this before test_physics, which is the *other* half of the
 * check: test_physics compares whole weather days against values recorded
 * from real CPython round(), so between them the chain
 * "fast path == libc round-trip == CPython round()" is closed at both links. */
#include <inttypes.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pyfloat.h"

static long checks;
static long failures;

/* The reference: exactly what pyfloat.c's round_via_libc does. */
static double reference_round(double x, int ndigits) {
    char buf[64];
    int written = snprintf(buf, sizeof(buf), "%.*f", ndigits, x);
    if (written < 0 || (size_t)written >= sizeof(buf)) return x;
    return strtod(buf, NULL);
}

static uint64_t bits_of(double x) {
    uint64_t bits;
    memcpy(&bits, &x, sizeof(bits));
    return bits;
}

static void check(double x, int ndigits) {
    double got = py_round_ndigits(x, ndigits);
    double want = reference_round(x, ndigits);
    checks++;
    if (bits_of(got) == bits_of(want)) return;
    if (failures < 10) {
        printf("FAIL round(%.17g, %d): got %.17g (%016" PRIx64 "), want %.17g (%016" PRIx64 ")\n",
               x, ndigits, got, bits_of(got), want, bits_of(want));
    }
    failures++;
}

/* xorshift64*, so the sweep is the same on every machine and every run --
 * a differential test that samples a different million values each time
 * turns a reproducible failure into a flaky one. */
static uint64_t rng_state = 0x9e3779b97f4a7c15ULL;
static uint64_t next_random(void) {
    rng_state ^= rng_state >> 12;
    rng_state ^= rng_state << 25;
    rng_state ^= rng_state >> 27;
    return rng_state * 0x2545f4914f6cdd1dULL;
}

/* The ranges weather.c actually rounds: round(temperature, 2),
 * round(rainfall, 3), round(evaporation, 3). Swept densely, because these
 * are the only inputs a recorded seed's replay depends on. */
static void test_weather_ranges(void) {
    for (long i = -4000; i <= 6000; i++) check((double)i * 0.0137, 2);
    for (long i = 0; i <= 10000; i++) check((double)i * 0.0091, 3);
    for (long i = 0; i <= 10000; i++) check((double)i * 0.00037, 3);
}

/* Exact halfway cases -- the only inputs where the two roundings can
 * disagree, and the reason the fast path implements half-to-even by hand
 * rather than calling rint(). k/2^j with a small j lands exactly on a
 * trailing 5 (0.125 -> round(_, 2) is a true tie), which a
 * multiply-and-nearbyint shortcut gets wrong about half the time. */
static void test_exact_ties(void) {
    for (int ndigits = 0; ndigits <= 6; ndigits++) {
        for (long k = -20000; k <= 20000; k++) {
            /* x = k / 2^(ndigits+1) * 10^-... constructed so x * 10^ndigits
             * is a half-integer whenever k is odd. */
            double x = ldexp((double)k, -1) / pow(10.0, ndigits);
            check(x, ndigits);
            check(x + ldexp((double)k, -1) * 1e-9, ndigits);
        }
    }
}

static void test_random_magnitudes(void) {
    for (long i = 0; i < 300000; i++) {
        /* Uniform mantissa, exponent swept across the whole finite range in
         * the low half and clustered near 1.0 in the high half, so both the
         * fast path and its declines get exercised. */
        uint64_t r = next_random();
        int exponent = (i % 2 == 0) ? (int)(r % 2100) - 1050 : (int)(r % 80) - 40;
        double mantissa = (double)(next_random() >> 11) / (double)(1ULL << 53);
        double x = ldexp(mantissa, exponent);
        if (next_random() & 1) x = -x;
        for (int ndigits = 0; ndigits <= 6; ndigits++) check(x, ndigits);
    }
}

/* Everything the fast path is required to decline, plus the values whose
 * sign or class the round-trip preserves in a way `==` cannot see. */
static void test_edges_and_declines(void) {
    static const double specials[] = {
        0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 0.005, -0.005, 0.015, -0.015,
        2.675, -2.675, 1e-300, -1e-300, 1e300, -1e300, 4.9406564584124654e-324,
        -4.9406564584124654e-324, 2.2250738585072014e-308, 9007199254740992.0,
        9007199254740993.0, 1.7976931348623157e308, -1.7976931348623157e308,
    };
    for (size_t i = 0; i < sizeof(specials) / sizeof(specials[0]); i++) {
        for (int ndigits = 0; ndigits <= 18; ndigits++) check(specials[i], ndigits);
        /* Negative and oversized ndigits: the fast path must decline, and
         * py_round_ndigits must still agree with the round-trip. */
        check(specials[i], -1);
        check(specials[i], -3);
        check(specials[i], 19);
        check(specials[i], 40);
    }

    /* Non-finite input is returned unchanged, including the NaN payload and
     * the sign of an infinity -- the one case that never reaches either
     * rounding path. */
    double nan_value = NAN;
    if (bits_of(py_round_ndigits(nan_value, 2)) != bits_of(nan_value)) {
        printf("FAIL round(nan, 2) did not return its argument unchanged\n");
        failures++;
    }
    checks++;
    if (py_round_ndigits(INFINITY, 3) != INFINITY ||
        py_round_ndigits(-INFINITY, 3) != -INFINITY) {
        printf("FAIL round(+/-inf, 3) did not return its argument unchanged\n");
        failures++;
    }
    checks++;
}

int main(void) {
    test_weather_ranges();
    test_exact_ties();
    test_random_magnitudes();
    test_edges_and_declines();
    printf("pyfloat tests: %ld checks, %ld failed\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
