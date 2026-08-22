/* Small float-semantics helpers shared by every bit-exact physics port
 * (crop_growth.c, weather.c) -- pulled out of simulation/_fastplotmodule.c's
 * per-file copies so both new callers get the identical, already-proven
 * forms rather than a second hand-transcription of the same arithmetic.
 */
#ifndef FARM_PYFLOAT_H
#define FARM_PYFLOAT_H

/* Python's two-argument min(a, x) / max(a, x): returns x only on a *strict*
 * inequality, so a tie (or NaN, or -0.0 vs 0.0) keeps the first argument.
 * Written this way -- not fmin/fmax, not a comparison chain -- because that
 * is exactly what CPython's bytecode does; see _fastplotmodule.c's header
 * comment for why this is load-bearing for bit-exact replay. */
static inline double py_min(double limit, double x) { return (x < limit) ? x : limit; }
static inline double py_max(double limit, double x) { return (x > limit) ? x : limit; }

/* max(0.0, min(1.0, x)) -- the clamp spelled out across simulation/crop_growth.py
 * and simulation/weather.py, always in this literal max/min form. */
static inline double clamp01(double x) { return py_max(0.0, py_min(1.0, x)); }

/* Neumaier-compensated sum over `values[0..count)`, starting from the
 * integer 0 that CPython's builtin sum() starts from. Since Python 3.12,
 * sum() applies this compensation to float addends (Objects/bltinmodule.c);
 * plain accumulation shifts the last bits and silently breaks seed-for-seed
 * replay wherever a sum() call sits on the RNG-adjacent state path (here:
 * crop_growth.update_crop_stress's nutrient shortfall). Copied from
 * simulation/_fastplotmodule.c's neumaier_sum, which this replaces the
 * second hand-transcription of. */
double py_neumaier_sum(const double *values, int count);

/* CPython's round(x, ndigits) for ndigits >= 0: Objects/floatobject.c's
 * double_round converts x to the correctly-rounded (round-half-to-even)
 * decimal string with exactly `ndigits` digits after the point (via
 * _Py_dg_dtoa mode 3) and parses that string back with a correctly-rounded
 * strtod. This reproduces the same round-trip through libc's snprintf("%.*f")
 * and strtod instead of porting David Gay's dtoa outright: both glibc and
 * macOS's libc implement correctly-rounded (ties-to-even, under the
 * always-FE_TONEAREST default this codebase never changes) decimal
 * formatting and parsing for finite doubles, which is the exact property
 * CPython's dtoa/strtod pair relies on -- so the two round-trips agree
 * bit-for-bit. farm-c/tests/test_physics.c checks this against real
 * `round()` output recorded by tools/export_physics_fixtures.py rather than
 * assuming it. Only used for weather.py's round(temperature, 2) /
 * round(rainfall, 3) / round(evaporation, 3); ndigits is always small (<=3)
 * and non-negative in every caller, so that's all this needs to support. */
double py_round_ndigits(double x, int ndigits);

/* Bytes needed by py_float_hex, including the NUL: the longest output is a
 * negative subnormal, "-0x0.0000000000001p-1022" (24 chars). */
#define PY_FLOAT_HEX_BUFSIZE 32

/* CPython's float.hex() (Objects/floatobject.c float_hex), written into
 * `out` (at least PY_FLOAT_HEX_BUFSIZE bytes) and returned for convenient
 * use as a printf argument.
 *
 * Built from the IEEE-754 bit pattern rather than libc's "%a" on purpose:
 * C99 leaves the leading hex digit of "%a" unspecified (glibc prints 0.5 as
 * "0x1p-1", other libcs may pick "0x8p-4"), and it also trims trailing
 * mantissa zeros, while float.hex() always emits exactly 13 fractional hex
 * digits with a leading 1 for normals and 0 for subnormals. Depending on
 * "%a" would make the trajectory digest libc-dependent -- the precise thing
 * the multi-platform CI matrix exists to rule out.
 *
 * Signed zero is preserved ("-0x0.0p+0" vs "0x0.0p+0"), which is the whole
 * reason the digest hashes this text rather than comparing doubles with ==:
 * the literal max/min forms in crop_growth.c exist to keep that distinction
 * and nothing else would notice if they stopped. */
const char *py_float_hex(double x, char *out);

#endif /* FARM_PYFLOAT_H */
