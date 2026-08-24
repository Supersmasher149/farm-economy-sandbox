/* Chained per-day digest of a run: the C counterpart to
 * ../.claude/skills/replay-guard/scripts/golden_replay.py's `_Trajectory`.
 *
 * WHY THIS EXISTS. `tests/test_engine.c` checks step ordering and same-seed
 * repeatability, and `c-parity` compares 20 end-of-run scalars per run. Both
 * miss a divergence that cancels out before the last day -- a crop lost on
 * day 9 and an extra one harvested on day 22 can land on the identical final
 * money. Hashing every simulated day makes any single-day difference
 * terminal, and chaining the hash makes the *first* differing day findable
 * (every later day differs by construction, so `golden trace` bisects by
 * eye).
 *
 * THE PAYLOAD IS A CROSS-LANGUAGE CONTRACT. The identical bytes are built
 * from a Python `PlayerState` by `_day_payload` in
 * ../.claude/skills/c-parity/scripts/c_parity.py, so the digest committed in
 * tests/golden_baseline.json is checkable from either language. Any edit
 * here is a coordinated edit to that function, and invalidates the committed
 * baseline -- `make golden-capture` re-records it, and `c_parity.py check`
 * is what proves the re-recorded value still equals Python's.
 *
 * Two rules make that contract reproducible in both languages:
 *
 *   * **Dict-shaped state is emitted sorted by key, skipping defaults.**
 *     Python's `market_supply`/`seed_inventory`/... are dicts whose key set
 *     depends on which items got touched (markets.py:36 creates a 0.0 entry
 *     for every item it loops over); the C stores the same state densely and
 *     cannot distinguish "absent" from 0.0 at all (see state.h's
 *     market_supply comment). Emitting only non-default entries makes both
 *     sides agree by construction instead of coupling the digest to Python's
 *     dict-population order. The cost is that a genuine 0.0 is invisible,
 *     which is why the scalars that could legitimately be 0.0 are in the
 *     fixed section instead.
 *
 *   * **Floats go through py_float_hex, never "%g" or "%a".** Exact, and it
 *     keeps -0.0 distinct from 0.0 -- the distinction crop_growth.c's
 *     literal max/min forms exist to preserve and that `==` cannot see.
 *
 * List-shaped state (lots, jobs, contracts, plots) is emitted positionally,
 * so ordering differences are caught rather than sorted away.
 */
#ifndef FARM_TRAJECTORY_H
#define FARM_TRAJECTORY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "state.h"
#include "weather.h"

/* Bytes for a rendered digest, including the NUL: 8 raw bytes -> 16 hex. */
#define TRAJECTORY_DIGEST_HEX_SIZE 17

typedef struct {
    uint8_t running[8];
    /* 0 until the first day is folded in, 8 thereafter. The chain starts
     * from a *zero-length* prefix, not from eight zero bytes: Python's
     * _Trajectory starts at b"", so day one hashes the payload alone. Eight
     * zero bytes instead produces a perfectly self-consistent chain that
     * disagrees with Python from the very first day -- and since every day's
     * payload still matches, nothing but the digest itself reveals it. */
    size_t running_len;
    long day_count;

    /* Per-day digests, retained only when trajectory_init was asked for
     * them (`golden trace`). A capture/check run holds thousands of runs and
     * wants only the final value, so the default keeps nothing. */
    bool keep_per_day;
    char (*per_day)[TRAJECTORY_DIGEST_HEX_SIZE];
    size_t per_day_count;
    size_t per_day_capacity;

    /* Latched on any allocation failure while building a payload. A digest
     * from a run that hit this is meaningless, so callers must check it
     * rather than comparing a silently-truncated hash. */
    bool failed;

    /* Reused across days so a 300-day run does one growing allocation
     * rather than 300. */
    char *scratch;
    size_t scratch_len;
    size_t scratch_capacity;
} Trajectory;

void trajectory_init(Trajectory *trajectory, bool keep_per_day);
void trajectory_destroy(Trajectory *trajectory);

/* A RunDayCallback (runner.h): pass this as `on_day` with the Trajectory as
 * `context`. Folds one day's payload into the running digest. */
void trajectory_observe_day(const FarmState *state, const WeatherDay *weather, void *context);

/* Renders the chained digest into `out` (TRAJECTORY_DIGEST_HEX_SIZE bytes)
 * and returns it. Returns the literal "unavailable" if `failed` is set. */
const char *trajectory_digest(const Trajectory *trajectory, char *out);

/* The exact payload text for one day, for debugging a digest mismatch that
 * `golden trace` has already localized to a day -- this is what gets hashed.
 * Returns a pointer into `trajectory->scratch`, valid until the next call.
 * NULL on allocation failure. */
const char *trajectory_day_payload(Trajectory *trajectory, const FarmState *state,
                                   const WeatherDay *weather);

#endif /* FARM_TRAJECTORY_H */
