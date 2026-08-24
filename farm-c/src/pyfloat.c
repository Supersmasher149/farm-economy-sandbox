#include "pyfloat.h"

#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

double py_neumaier_sum(const double *values, int count) {
    double total = 0.0;
    double compensation = 0.0;
    for (int i = 0; i < count; i++) {
        double x = values[i];
        double t = total + x;
        if (fabs(total) >= fabs(x)) {
            compensation += (total - t) + x;
        } else {
            compensation += (x - t) + total;
        }
        total = t;
    }
    return total + compensation;
}

/* The reference round-trip: format to `ndigits` decimal places, parse back.
 * "%.*f" is a correctly-rounded (ties-to-even) decimal conversion on both
 * target libcs -- see pyfloat.h's header comment. 40 bytes covers the widest
 * case this ever sees (small ndigits, magnitude well under 1e6) with room to
 * spare.
 *
 * This is the definition of the operation and stays the fallback for every
 * input the exact integer path below declines. It is also, on Darwin,
 * unusable in a parallel batch: both snprintf and strtod reach
 * localeconv_l(), which takes a process-wide os_unfair_lock, so ~1.5M calls
 * across N threads serialize on one lock and burn more CPU spinning than the
 * simulation itself uses. Measured on an 8-core machine, an 8-worker batch
 * spent 1.42s of its 1.11s wall clock in the kernel and scaled only 1.6x;
 * with the lock gone it scales 4.5x. See include/batch.h. */
static double round_via_libc(double x, int ndigits) {
    char buf[64];
    int written = snprintf(buf, sizeof(buf), "%.*f", ndigits, x);
    if (written < 0 || (size_t)written >= sizeof(buf)) {
        return x; /* unreachable for this port's actual inputs */
    }
    return strtod(buf, NULL);
}

#if defined(__SIZEOF_INT128__)
/* Exact, allocation-free, locale-free equivalent of round_via_libc over the
 * domain this port actually uses, computed in 128-bit integers instead of
 * decimal text. It is not an approximation of the round-trip: it performs
 * the same two roundings on exact integers, so it returns the identical
 * double or declines and lets round_via_libc answer.
 *
 * `x` is a dyadic rational m * 2^e with |m| < 2^53, so x * 10^n is exactly
 * m * 5^n * 2^(e+n) -- an exact integer when e+n >= 0, and an exact integer
 * quotient plus remainder otherwise. Rounding *that* half-to-even is the
 * first of the round-trip's two roundings, and it is exact by construction.
 * The second, decimal I / 10^n back to the nearest double, is one IEEE
 * division: for |I| <= 2^53 and n <= 22 both operands are exactly
 * representable, and a correctly-rounded division of exact operands is the
 * nearest double to the true quotient -- which is what strtod returns.
 *
 * Every precondition those two steps need is checked; anything outside them
 * (huge ndigits, |x| large enough to push I past 2^53, exponents past the
 * 128-bit window) returns false rather than guessing. tests/test_pyfloat.c
 * is a differential test against round_via_libc, not a table of expected
 * values, so the equivalence is checked rather than asserted. */
#define ROUND_FAST_MAX_NDIGITS 18
static bool round_exact_int128(double x, int ndigits, double *out) {
    if (ndigits < 0 || ndigits > ROUND_FAST_MAX_NDIGITS) return false;

    /* 10^n and 5^n, both exact: 10^n as a double up to n = 22, 5^n as a
     * 64-bit integer up to n = 27. */
    static const double POW10[ROUND_FAST_MAX_NDIGITS + 1] = {
        1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9,
        1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18};
    static const uint64_t POW5[ROUND_FAST_MAX_NDIGITS + 1] = {
        1ULL, 5ULL, 25ULL, 125ULL, 625ULL, 3125ULL, 15625ULL, 78125ULL, 390625ULL,
        1953125ULL, 9765625ULL, 48828125ULL, 244140625ULL, 1220703125ULL, 6103515625ULL,
        30517578125ULL, 152587890625ULL, 762939453125ULL, 3814697265625ULL};

    /* x = frac * 2^exp2 with 0.5 <= |frac| < 1, so frac * 2^53 is an exact
     * integer of at most 53 bits. Exact for subnormals too: frexp
     * normalizes them and ldexp scales without loss. */
    int exp2 = 0;
    double frac = frexp(x, &exp2);
    int64_t mantissa = (int64_t)ldexp(frac, 53);
    int e = exp2 - 53;

    /* signbit, not `mantissa < 0`: round(-0.0, n) is -0.0 in Python and in
     * the libc round-trip ("%.2f" prints "-0.00"), but -0.0's mantissa is
     * zero, so reading the sign off the integer loses it. */
    bool negative = signbit(x);
    uint64_t magnitude64 = mantissa < 0 ? -(uint64_t)mantissa : (uint64_t)mantissa;
    unsigned __int128 magnitude = (unsigned __int128)magnitude64;
    magnitude *= POW5[ndigits]; /* < 2^53 * 5^18 < 2^95, no overflow */

    unsigned __int128 rounded;
    int shift = e + ndigits;
    if (shift >= 0) {
        /* Exactly an integer already. Bail rather than shift out of range;
         * the |I| <= 2^53 check below would reject anything this large
         * anyway. */
        if (shift >= 128) return false;
        rounded = magnitude << shift;
        if ((rounded >> shift) != magnitude) return false; /* overflowed */
    } else {
        /* Round half to even on the exact quotient magnitude / 2^k. */
        int k = -shift;
        if (k >= 128) return false;
        unsigned __int128 quotient = magnitude >> k;
        unsigned __int128 remainder = magnitude - (quotient << k);
        unsigned __int128 half = (unsigned __int128)1 << (k - 1);
        if (remainder > half || (remainder == half && (quotient & 1) != 0)) quotient++;
        rounded = quotient;
    }

    /* Beyond 2^53 the division below is no longer exact-operands-in, so the
     * "one correctly-rounded division" argument stops holding. */
    if (rounded > ((unsigned __int128)1 << 53)) return false;

    double result = (double)(uint64_t)rounded / POW10[ndigits];
    *out = negative ? -result : result;
    return true;
}
#endif /* __SIZEOF_INT128__ */

double py_round_ndigits(double x, int ndigits) {
    if (!isfinite(x)) {
        return x; /* round() only ever sees finite weather values in this
                   * port's callers; kept for the same reason isfinite
                   * guards exist elsewhere -- fail closed, not silently. */
    }
#if defined(__SIZEOF_INT128__)
    double fast = 0.0;
    if (round_exact_int128(x, ndigits, &fast)) return fast;
#endif
    return round_via_libc(x, ndigits);
}

const char *py_float_hex(double x, char *out) {
    /* Reinterpret via memcpy rather than a union or a pointer cast between
     * double and uint64_t: the cast is a strict-aliasing violation that -O2
     * is entitled to miscompile, and this file is built at -O2 in the
     * `profile` target. */
    uint64_t bits;
    memcpy(&bits, &x, sizeof(bits));

    const int sign = (int)(bits >> 63);
    const int biased_exponent = (int)((bits >> 52) & 0x7ff);
    const uint64_t mantissa = bits & 0xfffffffffffffULL;
    const char *minus = sign ? "-" : "";

    if (biased_exponent == 0x7ff) {
        /* float.hex() defers to repr() for these: "inf"/"-inf"/"nan".
         * CPython prints bare "nan" with no sign, even for a negative NaN. */
        if (mantissa != 0) {
            snprintf(out, PY_FLOAT_HEX_BUFSIZE, "nan");
        } else {
            snprintf(out, PY_FLOAT_HEX_BUFSIZE, "%sinf", minus);
        }
        return out;
    }

    if (biased_exponent == 0 && mantissa == 0) {
        /* The one case that is not 13 fractional digits: float.hex() spells
         * zero "0x0.0p+0", keeping the sign bit. */
        snprintf(out, PY_FLOAT_HEX_BUFSIZE, "%s0x0.0p+0", minus);
        return out;
    }

    /* Normals carry an implicit leading 1 and an exponent of
     * biased-1023; subnormals have a leading 0 and a fixed -1022. */
    const int lead = biased_exponent == 0 ? 0 : 1;
    const int exponent = biased_exponent == 0 ? -1022 : biased_exponent - 1023;

    /* Always exactly 13 fractional hex digits (52 mantissa bits), never
     * trimmed, and the exponent always carries an explicit sign -- both are
     * float.hex()'s format, not "%a"'s. */
    snprintf(out, PY_FLOAT_HEX_BUFSIZE, "%s0x%d.%013" PRIx64 "p%+d", minus, lead, mantissa,
             exponent);
    return out;
}
