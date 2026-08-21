/* Mutable per-run simulation state. See docs/c-port-plan.md Section 2.
 *
 * A FarmState only ever references a ResolvedConfig -- it never owns or
 * frees it (docs/c-port-plan.md Section 9's ownership rules). Every
 * config-indexed array here (upgrades_owned, market_prices, ...) is sized
 * from that ResolvedConfig's counts and is dense (indexed directly by
 * ItemId/UpgradeId/... rather than a hash map), matching
 * docs/c-port-plan.md's "avoid hash tables in the simulation loop" guidance.
 */
#ifndef FARM_STATE_H
#define FARM_STATE_H

#include <stddef.h>

#include "config.h"
#include "farm_types.h"
#include "vec_util.h"

typedef struct {
    double moisture;
    double nitrogen;
    double phosphorus;
    double potassium;
    double ph;
    double soil_health;
    double pest_pressure;
    double disease_pressure;

    const char *previous_crop_family; /* NULL if never planted */
    int planted_index;                /* index into FarmState.planted, or -1 */
} PlotState;

typedef struct {
    ItemId crop_item_id;
    int day_planted;
    int growth_days_required;

    int last_watered_day;
    int neglect_days;

    bool fertilized;
    int plot_index; /* -1 if not tracked (see contracts.c's SIMPLIFIED note --
                      * the grade-ceiling stand-in never dereferences this) */

    double water_stress;
    double nutrient_stress;
    double temperature_stress;
    double pest_stress;
    double disease_stress;

    double accrued_cost;
} PlantedCrop;

typedef struct {
    PlantedCrop *data;
    size_t count;
    size_t capacity;
} PlantedCropVec;

typedef struct {
    ItemId item_id;
    int quantity;
    Quality quality;

    int produced_day;
    int age_days;
    int shelf_life_days;
    int effective_shelf_life_days; /* <=0 means "unset", falls back to shelf_life_days,
                                     * matching InventoryLot.remaining_shelf_life
                                     * (simulation/state.py:61-64) */

    double unit_cost;
    ItemType item_type;
} InventoryLot;

typedef struct {
    InventoryLot *data;
    size_t count;
    size_t capacity;
} InventoryLotVec;

static inline int inventory_lot_remaining_shelf_life(const InventoryLot *lot) {
    int shelf_life = lot->effective_shelf_life_days > 0 ? lot->effective_shelf_life_days
                                                          : lot->shelf_life_days;
    return shelf_life - lot->age_days;
}

typedef struct {
    RecipeId recipe_id;
    ItemId output_item_id;

    int output_quantity;
    int completion_day;
    int shelf_life_days;

    double unit_cost;
} ProcessingJob;

typedef struct {
    ProcessingJob *data;
    size_t count;
    size_t capacity;
} ProcessingJobVec;

/* Offers and active contracts share one representation, per
 * simulation/state.py:68-84 ContractState -- FarmState keeps them in
 * separate vectors (contract_offers / active_contracts) exactly as the
 * Python engine does, rather than one list with a state flag. */
typedef struct {
    ContractId id; /* index into whichever vector holds this record --
                     * not a stable cross-vector identity (see farm_types.h) */
    BuyerId buyer_id;
    ItemId item_id;

    int quantity;
    int delivered;
    Quality min_quality;

    double unit_price;
    double penalty_rate;

    int offered_day;
    int deadline_day;

    bool accepted;
    bool resolved;
} ContractRecord;

static inline int contract_remaining(const ContractRecord *contract) {
    int remaining = contract->quantity - contract->delivered;
    return remaining > 0 ? remaining : 0;
}

typedef struct {
    ContractRecord *data;
    size_t count;
    size_t capacity;
} ContractVec;

/* simulation/state.py:204-208 PlayerState.record_expense's categories --
 * fixed-index array instead of a string-keyed dict, per
 * docs/c-port-plan.md Section 3. Only "upgrades" is read by any ported
 * agent (should_buy_upgrade_within_budget's cumulative-spend cap), but the
 * full set is kept so a FarmState fixture can be populated faithfully from
 * a Python PlayerState's expenses_by_category without silently dropping
 * fields a future caller might need. */
typedef enum {
    EXPENSE_SEEDS,
    EXPENSE_WATERING,
    EXPENSE_FERTILIZER,
    EXPENSE_UPGRADES,
    EXPENSE_STORAGE,
    EXPENSE_PROCESSING,
    EXPENSE_CONTRACT_PENALTIES,
    EXPENSE_COUNT
} ExpenseCategory;

typedef struct {
    const ResolvedConfig *config; /* borrowed, never freed by FarmState */

    double money;
    double operating_reserve;

    int day;
    bool has_total_days; /* false == open-ended run;
                           * economy_rules.last_executable_day returns NULL then */
    int total_days;
    int slots_total;

    PlotState *plots;
    size_t plot_count;

    PlantedCropVec planted;
    InventoryLotVec inventory_lots;
    ProcessingJobVec processing_jobs;

    /* Dense, config->item_count-sized arrays indexed by CropId -- CropId and
     * ItemId share one index space (farm_types.h), so these are sized like
     * every other ItemId-indexed array below rather than by crop_count;
     * slots for non-crop items simply go unused. */
    int *seed_inventory;
    int *crop_plant_counts;

    int fertilizer_inventory;
    double water_units;

    /* Dense, config->upgrade_count-sized arrays indexed by UpgradeId. */
    bool *upgrades_owned;
    int *upgrade_purchase_days; /* INVALID_DAY means "never bought" */

    /* simulation/contracts.py's `_processing_capacity(player)` also falls
     * back to `player.contract_config.get("processing_capacity", 0)` when
     * this is unset -- out of scope here (contract_config is never
     * populated outside the full engine; see farm-c/README.md), so
     * `has_processing_capacity == false` resolves straight to 0. */
    bool has_processing_capacity;
    int processing_capacity;

    ContractVec active_contracts;
    ContractVec contract_offers;
    ContractId next_contract_id;

    /* Set by a mutator when an internal allocation cannot be completed. The
     * engine turns this latch into an allocation error before continuing with
     * a partially applied day. */
    bool allocation_failed;

    /* Dense, config->buyer_count-sized array indexed by BuyerId. */
    double *buyer_relationships;
    double reputation;

    /* Dense, config->item_count-sized arrays indexed by ItemId. */
    double *market_prices;
    bool *has_market_price; /* false == item_id not in player.market_prices
                              * (Python dict absence), distinct from a real 0.0 price */

    /* Dense, config->channel_count-sized array indexed by ChannelId. */
    int *channel_capacity_used;

    double total_revenue;
    double expenses_by_category[EXPENSE_COUNT];

    double highest_money; /* mirrors PlayerState.highest_money; has_highest_money
                            * distinguishes "never sold anything yet" (None in
                            * Python) from a real 0.0 peak */
    bool has_highest_money;

    int total_planted; /* PlayerState.total_planted: running count of every
                         * crop ever planted this run, read only by
                         * RandomAgent's choose_crop hash context. */

    /* PlayerState.run_seed: int | None -- feeds rng_hash's decision_random,
     * used only by RandomAgent. has_run_seed == false resolves to 0, per
     * `self.run_seed if self.run_seed is not None else 0`
     * (simulation/state.py:185). Seeds are non-negative in the C CLI/API,
     * matching the uint64_t runner seed representation. */
    bool has_run_seed;
    uint64_t run_seed;

    /* --- Phase 2 fields: the rest of simulation/state.py's PlayerState,
     * read/written by actions.c/inventory.c/markets.c/processing.c/
     * contracts.c's day-loop mutators. Nothing in Phase 0's agent-decision
     * slice or Phase 1's physics needed these. --- */

    /* Dense, config->item_count-sized array indexed by ItemId --
     * PlayerState.market_supply. Every entry starts at 0.0, matching
     * Python's dict-absence default (`.get(item_id, 0.0)`), so there is no
     * separate has_* flag: 0.0 and "never touched" are the same state. */
    double *market_supply;

    /* PlayerState.current_weather["season"], defaulting to SEASON_SPRING to
     * match `.get("season", "spring")` for a run whose first weather hasn't
     * been generated yet. Set by weather_generate (Phase 1) each day in the
     * real engine loop (Phase 4); markets_update_daily_prices reads it
     * directly. */
    Season current_season;

    /* Dense, config->channel_count-sized array indexed by ChannelId --
     * PlayerState.revenue_by_channel's real-channel entries
     * (markets.sell). The two pseudo-channel string keys Python's dict also
     * uses get their own scalar instead: "contract" (contracts.deliver)
     * below; "spot" (the legacy actions.sell_all) is out of scope per the
     * plan's scope decision, so it has no field here. */
    double *revenue_by_channel;
    double contract_channel_revenue;

    double total_expenses; /* PlayerState.total_expenses; kept alongside
                             * expenses_by_category rather than derived by
                             * summing it, matching Python's own two
                             * separately-updated fields exactly. */
    int total_sold;
    int idle_days;

    /* Modern engine run bookkeeping, matching PlayerState/_finish_day. */
    int slot_days;
    int occupied_slot_days;
    double lowest_money;
    bool bankrupt;
    int bankruptcy_day;
    char *bankruptcy_reason;

    int total_waterings;
    int total_harvest_events;
    int total_harvested; /* PlayerState.total_harvested: total units harvested */
    int total_crops_lost;
    int total_fertilizer_bought;
    int total_fertilizer_applied;
    int total_spoiled;
    int total_processed;
    double processing_revenue;

    int contracts_completed;
    int contracts_failed;
    double contract_penalties;

    /* PlayerState.quality_harvested, indexed by Quality (actions.
     * harvest_mature only ever writes premium/standard/processing -- a
     * rejected harvest takes the rejected_quality_units path below instead
     * -- but the array is sized by QUALITY_COUNT for generality). */
    int quality_harvested[QUALITY_COUNT];

    /* PlayerState.losses_by_cause's three keys (actions.harvest_mature,
     * inventory.age_and_spoil/enforce_storage_capacity) as fixed-index
     * fields instead of a string-keyed dict -- same discipline as
     * ExpenseCategory above. */
    int crop_loss_events;
    int rejected_quality_units;
    int spoilage_units;

    /* Reusable scratch allocations backing per-call decorate-sort buffers
     * (see vec_util.h's ScratchBuffer), replacing a fresh malloc+qsort+free
     * every call with one realloc'd-as-needed buffer per FarmState, freed
     * once in farm_state_destroy. Named by the group of call sites that
     * share them; grouped only where those sites are never concurrently
     * live on the call stack (see each field's comment for why): */
    ScratchBuffer scratch_lot_sort;      /* inventory.c: inventory_consume's
                                           * EligibleLot array and
                                           * trim_to_capacity's TrimLot array
                                           * -- neither function calls the
                                           * other, directly or transitively,
                                           * so they never nest. */
    ScratchBuffer scratch_sell_candidates; /* markets.c markets_sell's
                                             * SellCandidate array. */
    ScratchBuffer scratch_sell_planned;    /* markets.c markets_sell's
                                             * planned-sale array -- kept
                                             * separate from
                                             * scratch_sell_candidates
                                             * because both are concurrently
                                             * live in the same call
                                             * (candidates[i] is read while
                                             * the plan is written). */
    /* contracts.c's equivalent buffer is deliberately not here -- it sits
     * behind a const-FarmState call chain; see its comment in that file. */
} FarmState;

/* --- Vector push/free, backed by vec_util.c (see its header for why these
 * aren't hand-rolled per type). Push returns false only on allocation
 * failure, leaving the vector unchanged. --- */

bool planted_crop_vec_push(PlantedCropVec *vec, PlantedCrop item);
void planted_crop_vec_free(PlantedCropVec *vec);

bool inventory_lot_vec_push(InventoryLotVec *vec, InventoryLot item);
void inventory_lot_vec_free(InventoryLotVec *vec);

bool processing_job_vec_push(ProcessingJobVec *vec, ProcessingJob item);
void processing_job_vec_free(ProcessingJobVec *vec);

bool contract_vec_push(ContractVec *vec, ContractRecord item);
void contract_vec_free(ContractVec *vec);

/* Allocates every FarmState array (seed_inventory, upgrades_owned, ...)
 * sized from `config`'s counts, zero-initialized, with
 * upgrade_purchase_days set to INVALID_DAY and has_market_price/
 * has_highest_money to false -- the C equivalent of a fresh
 * PlayerState's field defaults (simulation/state.py:98-177). Vectors
 * (planted, inventory_lots, ...) start empty; callers push onto them
 * directly. `config` is borrowed, not copied or freed here.
 */
bool farm_state_init(FarmState *state, const ResolvedConfig *config, double money,
                     int slots_total);
void farm_state_destroy(FarmState *state);

static inline void farm_state_mark_allocation_failed(FarmState *state) {
    state->allocation_failed = true;
}

/* --- Phase 2 mutation helpers, backed by state.c --- */

/* simulation/state.py:204-208 PlayerState.record_expense. No-op for
 * amount <= 0 (matching Python exactly: a zero-cost action, e.g. a free
 * crop's seed purchase, must not record a spurious expense entry). */
void farm_state_record_expense(FarmState *state, ExpenseCategory category, double amount);

/* simulation/state.py:210-222 PlayerState.track_peak_cash. Must be called
 * immediately after crediting `state->money` from any revenue source (sale,
 * contract delivery, ...) -- see the Python docstring this mirrors. */
void farm_state_track_peak_cash(FarmState *state);

/* simulation/state.py:189-191 PlayerState.open_slots -- a property in
 * Python, a plain function here since C has none. */
static inline int farm_state_open_slots(const FarmState *state) {
    return state->slots_total - (int)state->planted.count;
}

/* simulation/state.py:193-195 PlayerState.add_slots: grows slots_total and
 * appends `amount` freshly-defaulted plots (state.c's farm_state_init
 * plot-default values). Returns false only on allocation failure, leaving
 * `state` unchanged, matching every other growable structure in this port. */
bool farm_state_add_slots(FarmState *state, int amount);

#endif /* FARM_STATE_H */
