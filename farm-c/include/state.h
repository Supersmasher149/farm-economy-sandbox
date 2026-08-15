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
     * (simulation/state.py:185). */
    bool has_run_seed;
    int64_t run_seed;
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
void farm_state_init(FarmState *state, const ResolvedConfig *config, double money,
                      int slots_total);
void farm_state_destroy(FarmState *state);

#endif /* FARM_STATE_H */
