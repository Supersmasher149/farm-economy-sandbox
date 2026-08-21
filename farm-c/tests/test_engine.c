#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "engine.h"

static char calls[32];
static size_t call_count;

static void mark(char c) {
    assert(call_count < sizeof(calls));
    calls[call_count++] = c;
}
static ItemId choose_crop(const Agent *self, const FarmState *state, const ResolvedConfig *config) {
    (void)self; (void)state; (void)config; mark('c'); return INVALID_ID;
}
static bool no_upgrade(const Agent *self, const FarmState *state, UpgradeId id) {
    (void)self; (void)state; (void)id; return false;
}
static bool no_plot_decision(const Agent *self, const FarmState *state, int index) {
    (void)self; (void)state; (void)index; return false;
}
static void choose_contracts(const Agent *self, const FarmState *state,
                             const ResolvedConfig *config, ContractDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out; mark('a');
}
static void choose_deliveries(const Agent *self, const FarmState *state,
                              DeliveryDecisionBuffer *out) {
    (void)self; (void)state; (void)out; mark('d');
}
static void choose_processing(const Agent *self, const FarmState *state,
                              const ResolvedConfig *config, ProcessingDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out; mark('p');
}
static void choose_sales(const Agent *self, const FarmState *state,
                         const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out; mark('s');
}
static bool no_fertilizer(const Agent *self, const FarmState *state, ItemId id) {
    (void)self; (void)state; (void)id; return false;
}

static const Agent TRACE_AGENT = {
    .name = "trace",
    .description = "engine test agent",
    .watering_diligence = 1.0,
    .choose_crop = choose_crop,
    .should_buy_upgrade = no_upgrade,
    .should_water = no_plot_decision,
    .should_fertilize = no_plot_decision,
    .choose_contracts = choose_contracts,
    .choose_contract_deliveries = choose_deliveries,
    .choose_processing = choose_processing,
    .choose_sales = choose_sales,
    .should_use_fertilizer = no_fertilizer,
};

static void load_world(ResolvedConfig *config) {
    ConfigError error;
    assert(config_load_directory("../config", config, &error));
}

static void test_order_and_bookkeeping(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    farm_state_init(&state, &config, 100.0, 2);
    FarmRng rng;
    rng_seed(&rng, 42);
    EngineError error;
    calls[0] = '\0';
    call_count = 0;
    assert(engine_run_day_observed(&state, &TRACE_AGENT, &rng, NULL, NULL, &error));
    assert(error.code == ENGINE_ERROR_NONE);
    assert(call_count == 5);
    assert(memcmp(calls, "adpsc", 5) == 0);
    assert(state.slot_days == 2);
    assert(state.occupied_slot_days == 0);
    assert(state.idle_days == 1);
    assert(state.day == 1);
    assert(state.has_highest_money && state.highest_money == state.money);
    assert(!state.bankrupt);
    farm_state_destroy(&state);
    config_destroy(&config);
}

/* --- Interleaving probe: records the order the engine asks about watering
 * and fertilizing. simulation/engine.py:176-183 handles both in ONE pass per
 * crop, so for two planted crops the decisions must come W,F,W,F -- not
 * W,W,F,F. Both actions spend money, so a split pass reorders the debits and
 * changes both the rounding of every later balance and, when cash is tight,
 * which actions are affordable at all. Neither is visible to the per-layer
 * fixture suites (they never run the engine loop) and neither moves the RNG,
 * which is how the split went undetected until the c-parity harness diffed a
 * whole run against Python. --- */

static char wf[32];
static size_t wf_count;

static void wf_mark(char c) {
    assert(wf_count < sizeof(wf) - 1);
    wf[wf_count++] = c;
    wf[wf_count] = '\0';
}
static ItemId first_crop_id = INVALID_ID;
static ItemId plant_first_crop(const Agent *self, const FarmState *state,
                               const ResolvedConfig *config) {
    (void)self; (void)state; (void)config; return first_crop_id;
}
static bool watch_water(const Agent *self, const FarmState *state, int index) {
    (void)self; (void)state; (void)index; wf_mark('W'); return false;
}
static bool watch_fertilize(const Agent *self, const FarmState *state, int index) {
    (void)self; (void)state; (void)index; wf_mark('F'); return false;
}

static const Agent INTERLEAVE_AGENT = {
    .name = "interleave",
    .description = "records water/fertilize decision order",
    .watering_diligence = 1.0,
    .choose_crop = plant_first_crop,
    .should_buy_upgrade = no_upgrade,
    .should_water = watch_water,
    .should_fertilize = watch_fertilize,
    .choose_contracts = choose_contracts,
    .choose_contract_deliveries = choose_deliveries,
    .choose_processing = choose_processing,
    .choose_sales = choose_sales,
    .should_use_fertilizer = no_fertilizer,
};

static void test_water_and_fertilize_interleave_per_crop(void) {
    ResolvedConfig config;
    load_world(&config);
    assert(config.crop_count > 0);
    first_crop_id = config.crops[0].item_id;
    FarmState state;
    farm_state_init(&state, &config, 1000.0, 2);
    FarmRng rng;
    rng_seed(&rng, 42);
    EngineError error;
    /* Day 1 fills both open slots (step 18 plants after the care pass). */
    assert(engine_run_day_observed(&state, &INTERLEAVE_AGENT, &rng, NULL, NULL, &error));
    assert(state.planted.count == 2);
    /* Day 2's care pass now has two crops to walk. */
    wf_count = 0;
    wf[0] = '\0';
    assert(engine_run_day_observed(&state, &INTERLEAVE_AGENT, &rng, NULL, NULL, &error));
    assert(strcmp(wf, "WFWF") == 0);
    farm_state_destroy(&state);
    config_destroy(&config);
}

static void test_repeatability_and_cleanup(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState a, b;
    farm_state_init(&a, &config, 1000.0, 2);
    farm_state_init(&b, &config, 1000.0, 2);
    FarmRng ra, rb;
    rng_seed(&ra, 123456);
    rng_seed(&rb, 123456);
    EngineError ea, eb;
    for (int day = 0; day < 8; day++) {
        assert(engine_run_day_observed(&a, &AGENT_FAST_SELLER, &ra, NULL, NULL, &ea));
        assert(engine_run_day_observed(&b, &AGENT_FAST_SELLER, &rb, NULL, NULL, &eb));
        assert(a.money == b.money);
        assert(a.day == b.day);
        assert(a.planted.count == b.planted.count);
        assert(a.inventory_lots.count == b.inventory_lots.count);
        assert(a.total_harvested == b.total_harvested);
    }
    farm_state_destroy(&a);
    farm_state_destroy(&b);
    config_destroy(&config);
}

int main(void) {
    test_order_and_bookkeeping();
    test_water_and_fertilize_interleave_per_crop();
    test_repeatability_and_cleanup();
    puts("engine tests passed");
    return 0;
}
