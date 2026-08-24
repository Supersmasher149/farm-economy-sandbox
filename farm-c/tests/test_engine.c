#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "contracts.h"
#include "config.h"
#include "engine.h"
#include "inventory.h"
#include "markets.h"
#include "pyfloat.h"

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
    assert(engine_run_day(&state, &TRACE_AGENT, &rng, &error));
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

static void test_contract_ids_are_not_vector_positions(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 1000.0, 2));
    state.day = config.contracts.offer_interval_days;

    ContractRecord existing = {
        .id = 0,
        .buyer_id = INVALID_ID,
        .item_id = config.crops[0].item_id,
        .quantity = 1,
        .min_quality = QUALITY_STANDARD,
        .unit_price = 1.0,
        .offered_day = 0,
        .deadline_day = 100,
        .penalty_rate = 0.0,
        .accepted = true,
        .resolved = false,
    };
    assert(contract_vec_push(&state.active_contracts, existing));

    FarmRng rng;
    rng_seed(&rng, 42);
    contracts_generate_offers(&state, &config, &rng);
    assert(!state.allocation_failed);
    assert(state.contract_offers.count > 0);
    assert(state.contract_offers.data[0].id != existing.id);

    ContractId generated = state.contract_offers.data[0].id;
    assert(contracts_accept(&state, &config, generated));
    assert(state.active_contracts.count == 2);
    assert(state.active_contracts.data[0].id != state.active_contracts.data[1].id);

    farm_state_destroy(&state);
    config_destroy(&config);
}

static void test_python_min_max_keep_first_tie(void) {
    double negative_zero = -0.0;
    double positive_zero = 0.0;
    assert(signbit(py_min(negative_zero, positive_zero)));
    assert(!signbit(py_min(positive_zero, negative_zero)));
    assert(signbit(py_max(negative_zero, positive_zero)));
    assert(!signbit(py_max(positive_zero, negative_zero)));
}

static void test_market_rejects_invalid_item_ids(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 1000.0, 1));
    MarketQuote quote;
    ItemId invalid = (ItemId)config.item_count;
    assert(!markets_quote(&state, invalid, QUALITY_STANDARD, &config.channels[0], 1,
                          NULL, &quote));
    int sold = -1;
    assert(markets_sell(&state, invalid, 1, &config.channels[0], false,
                        QUALITY_REJECTED, false, QUALITY_REJECTED, &sold) == 0.0);
    assert(sold == 0);
    farm_state_destroy(&state);
    config_destroy(&config);
}

static void test_vector_overflow_is_rejected(void) {
    void *data = NULL;
    size_t capacity = 0;
    assert(!vec_reserve(&data, &capacity, SIZE_MAX, sizeof(uint64_t)));
    assert(data == NULL && capacity == 0);
}


/* ===================================================================
 * Ordering boundaries
 *
 * The 23 numbered steps in engine_run_day_observed are the Python run_day
 * order, and CLAUDE.md calls reordering them a breaking change for every
 * recorded seed. `test_order_and_bookkeeping` above pins the *agent callback*
 * order; these pin the boundaries between steps where the agent is not
 * consulted at all -- exactly the moves that no fixture suite can see because
 * they change neither the RNG draw order nor any agent decision. Each one
 * below is an adjacent-step swap that would still produce a plausible-looking
 * simulation.
 * =================================================================== */


/* A silent hoarder: plants, never sells, never waters, never upgrades. The
 * registered agents all sell promptly, which leaves dawn inventory empty and
 * makes any storage-billing assertion vacuous -- this one accumulates stock so
 * there is actually a bill to get wrong. Its decision hooks are the no-op
 * versions rather than TRACE_AGENT's, whose recording buffers are sized for a
 * single day. */
static void silent_contracts(const Agent *self, const FarmState *state,
                             const ResolvedConfig *config, ContractDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out;
}
static void silent_deliveries(const Agent *self, const FarmState *state,
                              DeliveryDecisionBuffer *out) {
    (void)self; (void)state; (void)out;
}
static void silent_processing(const Agent *self, const FarmState *state,
                              const ResolvedConfig *config, ProcessingDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out;
}
static void silent_sales(const Agent *self, const FarmState *state, const ResolvedConfig *config,
                         SalesDecisionBuffer *out) {
    (void)self; (void)state; (void)config; (void)out;
}

static const Agent HOARDER_AGENT = {
    .name = "hoarder",
    .description = "plants and never sells",
    .watering_diligence = 1.0,
    .choose_crop = plant_first_crop,
    .should_buy_upgrade = no_upgrade,
    .should_water = no_plot_decision,
    .should_fertilize = no_plot_decision,
    .choose_contracts = silent_contracts,
    .choose_contract_deliveries = silent_deliveries,
    .choose_processing = silent_processing,
    .choose_sales = silent_sales,
    .should_use_fertilizer = no_fertilizer,
};

/* Steps 1-2 before step 6: the slot census is taken at the top of the day, so
 * a crop harvested today still counts as having occupied its slot today.
 * Moving the census after the harvest silently under-counts every harvest day,
 * which feeds occupied_slot_days -> utilisation in the reports. */
static void test_slot_census_precedes_harvest(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 400.0, 3));
    FarmRng rng;
    rng_seed(&rng, 20240815);
    EngineError error;

    bool saw_a_harvest = false;
    for (int day = 0; day < 40; day++) {
        int occupied_before = (int)state.planted.count;
        int slots_before = state.slots_total;
        long occupied_days_before = state.occupied_slot_days;
        long slot_days_before = state.slot_days;
        int harvest_events_before = state.total_harvest_events;

        assert(engine_run_day_observed(&state, &AGENT_FAST_SELLER, &rng, NULL, NULL, &error));

        assert(state.occupied_slot_days - occupied_days_before == occupied_before);
        assert(state.slot_days - slot_days_before == slots_before);
        if (state.total_harvest_events > harvest_events_before) saw_a_harvest = true;
    }
    /* Without a harvest in the window the assertion above is vacuous: every
     * day would trivially agree. */
    assert(saw_a_harvest);

    farm_state_destroy(&state);
    config_destroy(&config);
}

/* Step 5 before step 6, step 20 after: the day's storage bill is computed
 * from the inventory as it stood at dawn and only debited at dusk. Capturing
 * it after the harvest would charge for crops picked the same morning; moving
 * the collection earlier would charge before the day's sales could pay for it.
 * Both are invisible to the mutator fixtures, which never run a whole day. */
static void test_storage_liability_captured_before_harvest(void) {
    ResolvedConfig config;
    load_world(&config);
    assert(config.crop_count > 0);
    first_crop_id = config.crops[0].item_id;
    FarmState state;
    assert(farm_state_init(&state, &config, 400.0, 3));
    FarmRng rng;
    rng_seed(&rng, 987654);
    EngineError error;

    bool charged_a_day_with_a_harvest = false;
    for (int day = 0; day < 40; day++) {
        /* No storage upgrade is owned in this window, so the effective
         * storage config is the raw one -- see engine.c's effective_storage. */
        assert(!state.upgrades_owned[2]);
        double expected = inventory_capture_storage_liability(&state, &config.storage);
        double money_before = state.money;
        double storage_before = state.expenses_by_category[EXPENSE_STORAGE];
        size_t lots_before = state.inventory_lots.count;

        assert(engine_run_day_observed(&state, &HOARDER_AGENT, &rng, NULL, NULL, &error));

        double charged = state.expenses_by_category[EXPENSE_STORAGE] - storage_before;
        /* collect_storage_liability never drives money negative, so a
         * cash-poor day may pay less than it owes; it may never pay more. */
        assert(charged <= expected + 1e-9);
        if (money_before > expected) {
            assert(fabs(charged - expected) < 1e-9);
            if (state.inventory_lots.count > lots_before) charged_a_day_with_a_harvest = true;
        }
    }
    /* The interesting case is a day whose inventory *grew*: the bill must
     * reflect the smaller dawn inventory, not the larger dusk one. */
    assert(charged_a_day_with_a_harvest);

    farm_state_destroy(&state);
    config_destroy(&config);
}

/* Step 17 before step 18: a crop planted today is not watered or fertilized
 * today. Planting before the care pass would give every crop a free first-day
 * watering, shifting both cash and moisture from day one of every planting. */
static void test_new_plantings_skip_todays_care_pass(void) {
    ResolvedConfig config;
    load_world(&config);
    assert(config.crop_count > 0);
    first_crop_id = config.crops[0].item_id;
    FarmState state;
    assert(farm_state_init(&state, &config, 1000.0, 2));
    FarmRng rng;
    rng_seed(&rng, 42);
    EngineError error;

    wf_count = 0;
    wf[0] = '\0';
    assert(engine_run_day_observed(&state, &INTERLEAVE_AGENT, &rng, NULL, NULL, &error));
    /* Two crops went into the ground on day one... */
    assert(state.planted.count == 2);
    /* ...and neither was asked about care, because the care pass had already
     * run over an empty farm. */
    assert(wf_count == 0);

    /* Day two asks about both, confirming the emptiness above was ordering
     * and not a mute agent. */
    assert(engine_run_day_observed(&state, &INTERLEAVE_AGENT, &rng, NULL, NULL, &error));
    assert(strcmp(wf, "WFWF") == 0);

    farm_state_destroy(&state);
    config_destroy(&config);
}

/* Step 23 before the callback: the observer sees the day it just finished,
 * numbered as complete. The trajectory digest and `--verbose` per-day lines
 * both key off this, so an off-by-one here shifts every recorded day. */
static int observed_days[8];
static size_t observed_count;
static void record_day(const FarmState *state, const WeatherDay *weather, void *context) {
    (void)weather;
    (void)context;
    if (observed_count < sizeof(observed_days) / sizeof(observed_days[0]))
        observed_days[observed_count++] = state->day;
}

static void test_callback_observes_the_completed_day(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 200.0, 2));
    FarmRng rng;
    rng_seed(&rng, 7);
    EngineError error;

    observed_count = 0;
    for (int day = 0; day < 5; day++)
        assert(engine_run_day_observed(&state, &AGENT_FAST_SELLER, &rng, record_day, NULL, &error));

    assert(observed_count == 5);
    for (size_t i = 0; i < observed_count; i++) assert(observed_days[i] == (int)i + 1);

    farm_state_destroy(&state);
    config_destroy(&config);
}

/* Step 21: idle_days counts days on which nothing at all happened. The `acted`
 * flag is threaded through every mutating step, so this doubles as a check
 * that planting counts as action. */
static void test_idle_day_accounting(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    /* No money: nothing is affordable, nothing gets planted, the day is idle. */
    assert(farm_state_init(&state, &config, 0.0, 2));
    FarmRng rng;
    rng_seed(&rng, 11);
    EngineError error;
    assert(engine_run_day_observed(&state, &AGENT_FAST_SELLER, &rng, NULL, NULL, &error));
    assert(state.planted.count == 0);
    assert(state.idle_days == 1);
    farm_state_destroy(&state);

    /* Money to plant with: the same first day is no longer idle. */
    assert(farm_state_init(&state, &config, 500.0, 2));
    rng_seed(&rng, 11);
    assert(engine_run_day_observed(&state, &AGENT_FAST_SELLER, &rng, NULL, NULL, &error));
    assert(state.planted.count > 0);
    assert(state.idle_days == 0);
    farm_state_destroy(&state);
    config_destroy(&config);
}

/* ===================================================================
 * Allocation-failure cleanup
 *
 * docs/c-port-plan.md Section 9 requires every failure path to leave a
 * destroyable object and to free what it owns. These run under ASan, which is
 * what turns "the buffer was freed" from an unobservable claim into a
 * detectable one -- a leaked decision buffer fails the process, not the assert.
 * =================================================================== */

/* A latched state-level allocation failure must abort the day with
 * ENGINE_ERROR_ALLOCATION rather than continuing over a half-applied state. */
static void test_latched_state_allocation_failure_aborts_the_day(void) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 500.0, 2));
    FarmRng rng;
    rng_seed(&rng, 3);
    EngineError error = {ENGINE_ERROR_NONE, ""};

    farm_state_mark_allocation_failed(&state);
    int day_before = state.day;
    assert(!engine_run_day_observed(&state, &AGENT_FAST_SELLER, &rng, NULL, NULL, &error));
    assert(error.code == ENGINE_ERROR_ALLOCATION);
    assert(error.message[0] != '\0');
    /* The day must not advance past a failure -- a caller that retried would
     * otherwise skip a day silently. */
    assert(state.day == day_before);
    assert(state.allocation_failed);

    farm_state_destroy(&state);
    config_destroy(&config);
}

/* Each of the four decision buffers, failed in turn. The engine has a separate
 * ENGINE_CHECK_ALLOC site per buffer, so one working site says nothing about
 * the other three. */
static void fail_contracts(const Agent *self, const FarmState *state,
                           const ResolvedConfig *config, ContractDecisionBuffer *out) {
    (void)self; (void)state; (void)config; out->allocation_failed = true;
}
static void fail_deliveries(const Agent *self, const FarmState *state,
                            DeliveryDecisionBuffer *out) {
    (void)self; (void)state; out->allocation_failed = true;
}
static void fail_processing(const Agent *self, const FarmState *state,
                            const ResolvedConfig *config, ProcessingDecisionBuffer *out) {
    (void)self; (void)state; (void)config; out->allocation_failed = true;
}
static void fail_sales(const Agent *self, const FarmState *state, const ResolvedConfig *config,
                       SalesDecisionBuffer *out) {
    (void)self; (void)state; (void)config; out->allocation_failed = true;
}

static void run_one_failing_buffer(const Agent *agent, const char *what) {
    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 500.0, 2));
    FarmRng rng;
    rng_seed(&rng, 5);
    EngineError error = {ENGINE_ERROR_NONE, ""};

    assert(!engine_run_day_observed(&state, agent, &rng, NULL, NULL, &error));
    assert(error.code == ENGINE_ERROR_ALLOCATION);
    assert(strstr(error.message, what) != NULL);
    assert(strstr(error.message, "buffer allocation failed") != NULL);
    assert(state.day == 0);

    farm_state_destroy(&state);
    config_destroy(&config);
}

static void test_each_decision_buffer_failure_is_reported_and_freed(void) {
    Agent failing = TRACE_AGENT;
    failing.choose_contracts = fail_contracts;
    run_one_failing_buffer(&failing, "contract decision");

    failing = TRACE_AGENT;
    failing.choose_contract_deliveries = fail_deliveries;
    run_one_failing_buffer(&failing, "delivery decision");

    failing = TRACE_AGENT;
    failing.choose_processing = fail_processing;
    run_one_failing_buffer(&failing, "processing decision");

    failing = TRACE_AGENT;
    failing.choose_sales = fail_sales;
    run_one_failing_buffer(&failing, "sales decision");
}

/* Teardown must tolerate the shapes a failure leaves behind, per the
 * ownership rules: a zeroed object, and a double destroy. */
static void test_destroy_is_safe_on_partial_objects(void) {
    FarmState zeroed;
    memset(&zeroed, 0, sizeof(zeroed));
    farm_state_destroy(&zeroed);

    ResolvedConfig config;
    load_world(&config);
    FarmState state;
    assert(farm_state_init(&state, &config, 100.0, 1));
    farm_state_destroy(&state);
    /* destroy zeroes the object, so a second call is a no-op rather than a
     * double free -- the property that makes the engine's error paths safe to
     * unwind through. */
    farm_state_destroy(&state);
    config_destroy(&config);
}

int main(void) {
    test_order_and_bookkeeping();
    test_water_and_fertilize_interleave_per_crop();
    test_repeatability_and_cleanup();
    test_contract_ids_are_not_vector_positions();
    test_python_min_max_keep_first_tie();
    test_market_rejects_invalid_item_ids();
    test_vector_overflow_is_rejected();

    test_slot_census_precedes_harvest();
    test_storage_liability_captured_before_harvest();
    test_new_plantings_skip_todays_care_pass();
    test_callback_observes_the_completed_day();
    test_idle_day_accounting();

    test_latched_state_allocation_failure_aborts_the_day();
    test_each_decision_buffer_failure_is_reported_and_freed();
    test_destroy_is_safe_on_partial_objects();
    puts("engine tests passed");
    return 0;
}
