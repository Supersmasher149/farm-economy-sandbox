#include "pyfloat.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

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
