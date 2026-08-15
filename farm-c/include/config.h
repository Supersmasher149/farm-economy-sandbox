/* Immutable, indexed configuration -- loaded once, never mutated for the
 * lifetime of a ResolvedConfig. See docs/c-port-plan.md Sections 1 and 4.
 *
 * This header covers only what the 11 ported agents (and the shared
 * economy_rules/markets/inventory/contracts helpers they call) actually
 * read. It intentionally omits ItemDef.seasonal_demand's Weather/RNG
 * consumers, recipe validation fields, buyer offer-generation fields, etc.
 * -- those belong to the full engine port, not this agents-only slice.
 */
#ifndef FARM_CONFIG_H
#define FARM_CONFIG_H

#include <stddef.h>

#include "farm_types.h"

typedef enum {
    ITEM_CROP,
    ITEM_PRODUCT
} ItemType;

/* simulation/derived.py:52 DEFAULT_NUTRIENT_DEMAND order: nitrogen,
 * phosphorus, potassium. A crop whose config omits nutrient_demand gets
 * this default at load time -- callers here never see "no demand
 * specified", only a resolved value, matching
 * derived.nutrient_demand_total's contract. */
typedef struct {
    double nitrogen;
    double phosphorus;
    double potassium;
} NutrientDemand;

#define DEFAULT_NUTRIENT_DEMAND ((NutrientDemand){0.02, 0.01, 0.01})

typedef enum {
    UNLOCK_NONE,
    UNLOCK_REVENUE,  /* economy_rules.is_crop_unlocked: total_revenue gate */
    UNLOCK_UPGRADE   /* economy_rules.is_crop_unlocked: upgrade-owned gate */
} UnlockType;

/* Crop "role", read by ProgressionPlayer.choose_crop
 * (agents/progression_player.py:72) to prefer the "standard" crop over a
 * merely-safe one. Free-form in the Python config (crop.get("role")); the
 * agent only ever compares against the literal string "standard", so this
 * only needs to distinguish "standard" from "anything else" plus the
 * separate "fast" role ProfitOptimizer-descended agents check when deciding
 * whether to fertilize (agents/progression_player.py:87).
 */
typedef enum {
    CROP_ROLE_OTHER,
    CROP_ROLE_STANDARD,
    CROP_ROLE_FAST
} CropRole;

typedef struct {
    ItemId id;
    ItemType type;

    const char *external_id;
    const char *name;

    double base_price;
    double price_variation;
    /* seasonal_demand omitted: agents never read it directly (only
     * markets.update_daily_prices does, which is out of scope here). */
} ItemDef;

typedef struct {
    UnlockType type;
    double revenue_threshold; /* UNLOCK_REVENUE */
    UpgradeId upgrade;        /* UNLOCK_UPGRADE */
} UnlockRequirement;

typedef struct {
    ItemId item_id; /* index into ResolvedConfig.items -- every crop is also
                      * an item, per docs/c-port-plan.md's unified item table */

    CropRole role;
    const char *family; /* NULL if the crop has no family */

    double seed_cost;
    int growth_days;
    int min_yield;
    int max_yield;
    double loss_chance;
    int water_interval_days;

    NutrientDemand nutrient_demand;

    UnlockRequirement unlock_requirement; /* .type == UNLOCK_NONE if absent */
} CropDef;

typedef enum {
    EFFECT_CAPACITY,
    EFFECT_GROWTH_TIME_REDUCTION,
    EFFECT_STORAGE,
    EFFECT_PROCESSING_CAPACITY
} UpgradeEffectType;

typedef struct {
    UpgradeEffectType type;
    union {
        int capacity;                    /* EFFECT_CAPACITY: slots added */
        double growth_time_reduction;    /* EFFECT_GROWTH_TIME_REDUCTION: amount in (0,1) */
        struct {
            int capacity_bonus;
            double shelf_life_multiplier;
        } storage;                        /* EFFECT_STORAGE */
        int processing_capacity;          /* EFFECT_PROCESSING_CAPACITY: amount */
    } as;
} UpgradeEffect;

typedef struct {
    UpgradeId id;
    const char *external_id;
    const char *name;
    double cost;
    UpgradeEffect effect;
} UpgradeDef;

typedef struct {
    RecipeId id;
    const char *external_id;

    ItemId input_item_id;
    ItemId output_item_id;

    int input_quantity;
    int output_quantity;
    int processing_days;
    Quality min_quality; /* recipe.get("min_quality", "processing") */

    double cost;
} RecipeDef;

/* Exactly simulation/derived.py:155-186 ChannelProfile's fields --
 * markets.quote reads a channel's terms from a cached profile rather than
 * the raw config dict; this struct *is* that cache, statically. */
typedef struct {
    ChannelId channel_id;
    const char *external_id; /* the JSON config's string "id" (e.g. "spot") --
                               * kept alongside the resolved ChannelId so
                               * agents/base.c's default choose_sales (which
                               * Python hardcodes to the literal string
                               * "spot") and the fixture loader can resolve
                               * it without a name->id table of their own. */
    Quality min_quality_rank;
    double min_reputation;
    bool has_daily_capacity; /* false == uncapped (quote() then falls back
                               * to the caller's own requested quantity) */
    int daily_capacity;
    double price_multiplier;
    double reputation_bonus;
    double flat_fee;
    double fee_rate;
} ChannelDef;

typedef struct {
    BuyerId id;
    const char *external_id;
    const char *name;
    double min_reputation;
    double relationship_bonus_rate;
    /* Offer-generation fields (quantity_range, contract_price_multiplier,
     * deadline_days, penalty_rate, allowed items) are intentionally omitted:
     * agents never generate offers, only evaluate ones already on
     * FarmState.contract_offers -- see state.h ContractRecord. */
} BuyerDef;

/* simulation/economy_rules.py reads fertilizer_config["cost"],
 * ["loss_chance_reduction"], and ["yield_bonus_pct"] -- a single global
 * config (config/fertilizer.json), not indexed by anything. */
typedef struct {
    double cost;
    double loss_chance_reduction;
    double yield_bonus_pct;
} FertilizerConfig;

/* simulation/contracts.py module-level defaults, the subset ported agents'
 * feasibility/profitability checks read via `player.contract_config.get(...)`:
 * DEFAULT_FALLBACK_PRICE_MULTIPLIER (contracts.py:12),
 * PRODUCTION_SAFETY_FACTOR (contracts.py:7), DEFAULT_OFFER_EXPIRY_DAYS
 * (contracts.py:8). Relationship-bonus fields are omitted: nothing in the
 * ported agent decision surface reads them (only generate_offers/deliver do,
 * both out of scope -- see farm-c/README.md). */
typedef struct {
    double fallback_price_multiplier; /* default 1.15 */
    double production_safety_factor;  /* default 0.45 */
    int offer_expiry_days;            /* default 3 */
} ContractsConfig;

typedef struct {
    ItemDef *items;
    size_t item_count;

    CropDef *crops;
    size_t crop_count;

    UpgradeDef *upgrades;
    size_t upgrade_count;

    RecipeDef *recipes;
    size_t recipe_count;

    BuyerDef *buyers;
    size_t buyer_count;

    ChannelDef *channels;
    size_t channel_count;

    FertilizerConfig fertilizer;
    ContractsConfig contracts;
} ResolvedConfig;

/* --- Lookups (linear scan; config is small and this is test/decision-time
 * code, not the per-plot-per-day hot path the Python id()-keyed caches in
 * simulation/derived.py exist for -- see farm-c/README.md). --- */

const CropDef *config_find_crop(const ResolvedConfig *config, ItemId item_id);
const ItemDef *config_find_item(const ResolvedConfig *config, ItemId item_id);
const UpgradeDef *config_find_upgrade(const ResolvedConfig *config, UpgradeId upgrade_id);
const RecipeDef *config_find_recipe(const ResolvedConfig *config, RecipeId recipe_id);
const ChannelDef *config_find_channel(const ResolvedConfig *config, ChannelId channel_id);
/* Returns INVALID_ID if no channel has this external_id. */
ChannelId config_channel_id_by_external_id(const ResolvedConfig *config, const char *external_id);

#endif /* FARM_CONFIG_H */
