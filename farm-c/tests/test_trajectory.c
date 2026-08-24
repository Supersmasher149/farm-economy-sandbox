/* Unit tests for the per-day trajectory digest (src/trajectory.c) and the
 * float.hex() emitter it is built on (src/pyfloat.c).
 *
 * These check the digest's *sensitivity* -- that it actually notices the
 * things it exists to notice -- without needing Python. Whether the bytes
 * match Python is a separate question, answered by
 * `c_parity.py baseline`; a digest that were merely self-consistent would
 * pass everything here, which is why both gates exist.
 */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "blake2b.h"
#include "config.h"
#include "pyfloat.h"
#include "trajectory.h"

static int failures;

static void report(bool ok, const char *name) {
    if (!ok) {
        failures++;
        printf("FAIL %s\n", name);
    }
}

/* --- py_float_hex -------------------------------------------------------- */

static void check_hex(double value, const char *expected) {
    char buf[PY_FLOAT_HEX_BUFSIZE];
    py_float_hex(value, buf);
    if (strcmp(buf, expected) != 0) {
        failures++;
        printf("FAIL py_float_hex: got %s, want %s\n", buf, expected);
    }
}

static void test_float_hex(void) {
    /* Every one of these is literally what CPython's float.hex() returns.
     * The zero and subnormal spellings are the two libc "%a" would get
     * wrong, and the signed zero is the distinction the whole digest exists
     * to preserve. */
    check_hex(0.0, "0x0.0p+0");
    check_hex(-0.0, "-0x0.0p+0");
    check_hex(1.0, "0x1.0000000000000p+0");
    check_hex(-1.0, "-0x1.0000000000000p+0");
    check_hex(0.5, "0x1.0000000000000p-1");
    check_hex(6.5, "0x1.a000000000000p+2");
    check_hex(0.1, "0x1.999999999999ap-4");
    check_hex(5e-324, "0x0.0000000000001p-1022"); /* smallest subnormal */
    check_hex(2.2250738585072014e-308, "0x1.0000000000000p-1022"); /* smallest normal */

    /* Round-trips exactly, which is what lets a fixture loader use strtod. */
    char buf[PY_FLOAT_HEX_BUFSIZE];
    double value = 1234.56789;
    py_float_hex(value, buf);
    report(strtod(buf, NULL) == value, "py_float_hex round-trips through strtod");
}

/* --- digest construction -------------------------------------------------- */

static bool load_test_config(ResolvedConfig *config, SimulationSettings *settings) {
    ConfigError error;
    if (!config_load_directory("../config", config, &error)) {
        printf("FAIL config load: %s\n", error.message);
        failures++;
        return false;
    }
    if (!config_load_simulation_settings("../config", settings, &error)) {
        printf("FAIL settings load: %s\n", error.message);
        failures++;
        config_destroy(config);
        return false;
    }
    return true;
}

/* The bug this file was written after: the chain must start from a
 * zero-*length* prefix, not from eight zero bytes. Both are internally
 * consistent, so nothing but an explicit check -- or a cross-language diff --
 * catches the difference. Python's _Trajectory starts at b"", so after one
 * day the digest must equal blake2b(payload) with no prefix at all. */
static void test_chain_starts_from_empty_prefix(const ResolvedConfig *config) {
    FarmState state;
    report(farm_state_init(&state, config, 100.0, 3), "state init");

    Trajectory trajectory;
    trajectory_init(&trajectory, false);

    char digest_before[TRAJECTORY_DIGEST_HEX_SIZE];
    trajectory_digest(&trajectory, digest_before);
    report(strcmp(digest_before, "") == 0, "digest before any day is the empty string");

    /* The payload for this state, hashed directly with no prefix. */
    Trajectory scratch;
    trajectory_init(&scratch, false);
    const char *payload = trajectory_day_payload(&scratch, &state, NULL);
    report(payload != NULL, "payload built");
    uint8_t expected[8];
    blake2b_hash(payload, strlen(payload), expected, sizeof(expected));
    char expected_hex[TRAJECTORY_DIGEST_HEX_SIZE];
    for (size_t i = 0; i < sizeof(expected); i++) {
        snprintf(expected_hex + i * 2, 3, "%02x", expected[i]);
    }
    trajectory_destroy(&scratch);

    trajectory_observe_day(&state, NULL, &trajectory);
    char digest_after[TRAJECTORY_DIGEST_HEX_SIZE];
    trajectory_digest(&trajectory, digest_after);
    report(strcmp(digest_after, expected_hex) == 0,
           "day one digest == blake2b(payload) with no prefix");

    trajectory_destroy(&trajectory);
    farm_state_destroy(&state);
}

/* Helper: digest of a single day for whatever `state` currently holds. */
static void digest_of(const FarmState *state, char *out) {
    Trajectory trajectory;
    trajectory_init(&trajectory, false);
    trajectory_observe_day(state, NULL, &trajectory);
    trajectory_digest(&trajectory, out);
    trajectory_destroy(&trajectory);
}

static void test_digest_notices_state_changes(const ResolvedConfig *config) {
    FarmState state;
    report(farm_state_init(&state, config, 100.0, 3), "state init");

    char base[TRAJECTORY_DIGEST_HEX_SIZE];
    digest_of(&state, base);

    char other[TRAJECTORY_DIGEST_HEX_SIZE];

    /* Identical state, fresh Trajectory -> identical digest. */
    digest_of(&state, other);
    report(strcmp(base, other) == 0, "same state hashes the same");

    /* A one-cent difference must move it. This is the property the old
     * "round to 6dp" style of baseline gave up. */
    state.money += 0.01;
    digest_of(&state, other);
    report(strcmp(base, other) != 0, "money change moves the digest");
    state.money -= 0.01;

    /* One ulp on one plot's moisture, out of every field of every plot. */
    double saved = state.plots.moisture[0];
    state.plots.moisture[0] = nextafter(saved, 1.0);
    digest_of(&state, other);
    report(strcmp(base, other) != 0, "1-ulp soil change moves the digest");
    state.plots.moisture[0] = saved;

    /* -0.0 vs 0.0: equal under `==`, and the literal max/min forms in
     * crop_growth.c exist to keep them distinct. If the digest could not see
     * this, nothing else would. */
    state.plots.pest_pressure[0] = 0.0;
    digest_of(&state, base);
    state.plots.pest_pressure[0] = -0.0;
    digest_of(&state, other);
    report(strcmp(base, other) != 0, "-0.0 hashes differently from 0.0");
    state.plots.pest_pressure[0] = 0.0;

    /* A counter that never reaches the final CSV still has to be covered,
     * or a divergence in it is invisible until it changes something else. */
    digest_of(&state, base);
    state.total_harvest_events += 1;
    digest_of(&state, other);
    report(strcmp(base, other) != 0, "total_harvest_events moves the digest");
    state.total_harvest_events -= 1;

    /* Ordering, not just contents: the digest emits lists positionally
     * because a reordered inventory changes which lot a FIFO consume takes. */
    InventoryLot first = {0};
    first.item_id = 0;
    first.quantity = 3;
    first.quality = QUALITY_STANDARD;
    InventoryLot second = first;
    second.quantity = 5;
    report(inventory_lot_vec_push(&state.inventory_lots, first), "push lot 1");
    report(inventory_lot_vec_push(&state.inventory_lots, second), "push lot 2");
    digest_of(&state, base);
    InventoryLot swap = state.inventory_lots.data[0];
    state.inventory_lots.data[0] = state.inventory_lots.data[1];
    state.inventory_lots.data[1] = swap;
    digest_of(&state, other);
    report(strcmp(base, other) != 0, "inventory lot order moves the digest");

    farm_state_destroy(&state);
}

/* The chain is order-dependent: the same two days in the other order must
 * not collide. A digest over a *set* of days would miss a reordering. */
static void test_chain_is_order_dependent(const ResolvedConfig *config) {
    FarmState state;
    report(farm_state_init(&state, config, 100.0, 3), "state init");

    Trajectory forward;
    trajectory_init(&forward, true);
    state.money = 10.0;
    trajectory_observe_day(&state, NULL, &forward);
    state.money = 20.0;
    trajectory_observe_day(&state, NULL, &forward);
    char forward_hex[TRAJECTORY_DIGEST_HEX_SIZE];
    trajectory_digest(&forward, forward_hex);

    Trajectory backward;
    trajectory_init(&backward, true);
    state.money = 20.0;
    trajectory_observe_day(&state, NULL, &backward);
    state.money = 10.0;
    trajectory_observe_day(&state, NULL, &backward);
    char backward_hex[TRAJECTORY_DIGEST_HEX_SIZE];
    trajectory_digest(&backward, backward_hex);

    report(strcmp(forward_hex, backward_hex) != 0, "day order changes the chained digest");
    report(forward.day_count == 2 && forward.per_day_count == 2, "per-day digests recorded");
    /* Chained, so the first day's digest is shared only if that day matched. */
    report(strcmp(forward.per_day[0], backward.per_day[0]) != 0,
           "per-day digests differ from the first differing day");

    trajectory_destroy(&forward);
    trajectory_destroy(&backward);
    farm_state_destroy(&state);
}

int main(void) {
    test_float_hex();

    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_test_config(&config, &settings)) {
        printf("%d failed\n", failures);
        return 1;
    }
    test_chain_starts_from_empty_prefix(&config);
    test_digest_notices_state_changes(&config);
    test_chain_is_order_dependent(&config);
    config_destroy(&config);

    if (failures > 0) {
        printf("%d failed\n", failures);
        return 1;
    }
    puts("trajectory tests passed");
    return 0;
}
