#include "pyfloat.h"

#include <inttypes.h>
#include <math.h>
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

double py_round_ndigits(double x, int ndigits) {
    if (!isfinite(x)) {
        return x; /* round() only ever sees finite weather values in this
                   * port's callers; kept for the same reason isfinite
                   * guards exist elsewhere -- fail closed, not silently. */
    }
    /* "%.*f" is a correctly-rounded (ties-to-even) decimal conversion on
     * both target libcs -- see pyfloat.h's header comment. 40 bytes covers
     * the widest case this ever sees (small ndigits, magnitude well under
     * 1e6) with room to spare. */
    char buf[64];
    int written = snprintf(buf, sizeof(buf), "%.*f", ndigits, x);
    if (written < 0 || (size_t)written >= sizeof(buf)) {
        return x; /* unreachable for this port's actual inputs */
    }
    return strtod(buf, NULL);
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
