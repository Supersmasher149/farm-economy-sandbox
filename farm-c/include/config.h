/* Immutable, indexed configuration -- loaded once, never mutated for the
 * lifetime of a ResolvedConfig. See docs/c-port-plan.md Sections 1 and 4.
 *
 * Originally covered only what the 11 ported agents (and the shared
 * economy_rules/markets/inventory/contracts decision helpers they call)
 * read; Phase 1 (crop_growth.c/weather.c) and Phase 2 (actions.c/
 * inventory.c/markets.c/processing.c/contracts.c's day-loop mutators) each
 * added the further fields their own ported functions need -- see the
 * per-field comments below for which phase introduced what. Recipe
 * validation fields and anything only `config_validation.py`-equivalent
 * loading logic would need are still out of scope (Phase 3). */
#ifndef FARM_CONFIG_H
#define FARM_CONFIG_H

#include <stddef.h>

#include "farm_types.h"

typedef enum {
    ITEM_CROP,
    ITEM_PRODUCT
} ItemType;

/* simulation/weather.py:5 SEASONS -- order is load-bearing (indexes
 * WeatherParams.by_season and ItemDef.seasonal_demand below, and is itself
 * derived from `day // season_length % 4`, matching Python's tuple indexing
 * exactly). Declared up here (rather than alongside WeatherParams further
 * down) so ItemDef can size seasonal_demand by it. */
typedef enum {
    SEASON_SPRING,
    SEASON_SUMMER,
    SEASON_AUTUMN,
    SEASON_WINTER,
    SEASON_COUNT
} Season;

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
    /* derived.py:_build_market_profiles: `item.get("seasonal_demand", {})`,
     * indexed by Season (declared further down this file) and resolved to
     * 1.0 per season the JSON doesn't mention -- markets.update_daily_prices
     * reads `seasonal_demand.get(season, 1.0)`, so an entirely-absent dict
     * and one naming every season at 1.0 are indistinguishable to any
     * reader, and this stores the already-resolved result either way. */
    double seasonal_demand[SEASON_COUNT];
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

    /* simulation/derived.py:101-152 CropProfile's remaining fields (every
     * one CropProfile resolves besides water_interval_days/min_moisture,
     * already present above/below, and nutrient_demand, already above) --
     * folded directly onto CropDef rather than a separate profile struct,
     * matching how nutrient_demand's own default is already resolved here
     * at load time instead of read lazily per call. Defaults, applied when
     * the JSON field is absent: temperature_range [10, 30], ph_range
     * [5.8, 7.0], min_moisture 0.35, *_susceptibility 1.0. */
    double temperature_low;
    double temperature_high;
    double ph_low;
    double ph_high;
    double min_moisture;
    double pest_susceptibility;
    double disease_susceptibility;

    /* crop.get("shelf_life_days", 7) -- Phase 2 (actions.c) field:
     * harvest_mature stamps a newly-harvested lot's shelf life with this. */
    int shelf_life_days;
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
    /* recipe.get("shelf_life_days", 30) -- Phase 2 (processing.c) field:
     * start_job stamps a completing job's output lot with this. */
    int shelf_life_days;
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

    /* Offer-generation fields (simulation/contracts.py:58-106
     * generate_offers), needed by Phase 2's contracts_generate_offers --
     * the agent-decision slice never read these (agents only evaluate
     * offers already on FarmState.contract_offers), which is why they
     * weren't here before. */
    ItemId *allowed_items; /* buyer.get("items", []), resolved to ids */
    size_t allowed_item_count;
    int quantity_min; /* buyer.get("quantity_range", [5, 12])[0] */
    int quantity_max; /* buyer.get("quantity_range", [5, 12])[1] */
    Quality min_quality;                /* buyer.get("min_quality", "standard") */
    double contract_price_multiplier;   /* buyer.get("contract_price_multiplier", 1.2) */
    int deadline_days;                  /* buyer.get("deadline_days", 10) */
    /* buyer.get("penalty_rate", contract_config.get("default_penalty_rate",
     * 0.35)) -- the ContractsConfig default is already folded in here at
     * load time, so generate_offers never needs to consult it itself. */
    double penalty_rate;
} BuyerDef;

/* simulation/economy_rules.py reads fertilizer_config["cost"],
 * ["loss_chance_reduction"], and ["yield_bonus_pct"] -- a single global
 * config (config/fertilizer.json), not indexed by anything. */
typedef struct {
    double cost;
    double loss_chance_reduction;
    double yield_bonus_pct;
    /* config/fertilizer.json's "quality_bonus"; crop_growth.harvest_multipliers
     * falls back to DEFAULT_FERTILIZER_QUALITY_BONUS (0.05) when the JSON
     * field is absent, folded in at load time same as every other default
     * on this page. Agents never read this field (see farm-c/README.md's
     * scope note), which is why it wasn't here before crop_growth.c needed
     * it. */
    double quality_bonus;
    /* config/fertilizer.json's "nutrients_added"; actions.py's plant_seed
     * and fertilize_crop both fall back to {"nitrogen": 0.25, "phosphorus":
     * 0.15, "potassium": 0.15} when the JSON field is absent -- folded in at
     * load time like every other default here. Phase 2 (actions.c) field. */
    NutrientDemand nutrients_added;
} FertilizerConfig;
#define DEFAULT_FERTILIZER_NUTRIENTS_ADDED ((NutrientDemand){0.25, 0.15, 0.15})

/* config/watering_settings.json, read by crop_growth.compute_harvest_outcome
 * (the first four fields) and simulation/actions.py's water_crop/harvest_mature
 * (all six -- cost_per_plot/moisture_added are Phase 2 (actions.c) fields,
 * kept here alongside the rest of the same JSON file rather than splitting
 * one config object across two structs). */
typedef struct {
    double neglect_loss_chance_penalty_per_day;
    double neglect_yield_penalty_per_day;
    double max_neglect_loss_chance_bonus;
    double max_neglect_yield_penalty;
    double cost_per_plot;
    double moisture_added;
} WateringConfig;

/* config/soil.json's "dynamics" section (simulation/derived.py:62-81
 * DEFAULT_SOIL_DYNAMICS) -- resolved with every default already folded in,
 * same discipline as the rest of this file. Field order matches
 * DEFAULT_SOIL_DYNAMICS exactly, though nothing here depends on that order. */
typedef struct {
    double harvest_soil_health_cost;
    double min_soil_health;
    double fallow_pest_decay;
    double fallow_disease_decay;
    double fallow_soil_health_regen;
    double pest_growth_per_day;
    double disease_growth_per_rainfall;
    double max_pest_pressure;
    double max_disease_pressure;
    double same_family_yield_penalty;
    double same_family_quality_penalty;
    double soil_health_yield_floor;
    double soil_health_yield_span;
} SoilDynamics;

/* config/soil.json's "regen_per_day" section (simulation/weather.py:84-91).
 * Every field defaults to 0.0 when the JSON key (or the whole "soil"
 * section) is absent, matching `plot_regen.get(name, 0.0) if plot_regen
 * else 0.0` exactly -- a zero-valued field and an absent one are always
 * treated identically downstream, so no separate "has_*" flags are needed. */
typedef struct {
    double moisture;
    double nitrogen;
    double phosphorus;
    double potassium;
    double soil_health;
    double pest_pressure;
    double disease_pressure;
} PlotRegen;

/* One config/weather.json "seasons.<name>" entry, resolved
 * (simulation/derived.py:349-369 WeatherParams.by_season's per-season
 * 6-tuple) with defaults folded in: temperature_range [12, 24], rain_chance
 * 0.25, rainfall_range [0.08, 0.25], evaporation 0.08. */
typedef struct {
    double temperature_low;
    double temperature_high;
    double rain_chance;
    double rainfall_low;
    double rainfall_high;
    double evaporation;
} SeasonWeather;

typedef struct {
    int season_length_days; /* default 15 */
    SeasonWeather by_season[SEASON_COUNT];
} WeatherParams;

/* simulation/contracts.py module-level defaults, read via
 * `player.contract_config.get(...)` throughout that module.
 * DEFAULT_FALLBACK_PRICE_MULTIPLIER (contracts.py:12),
 * PRODUCTION_SAFETY_FACTOR (contracts.py:7), and DEFAULT_OFFER_EXPIRY_DAYS
 * (contracts.py:8) were already here for the agent-decision slice's
 * feasibility/profitability checks; the remaining five back Phase 2's
 * generate_offers/deliver/resolve_expired, which the agent-decision slice
 * never needed. */
typedef struct {
    double fallback_price_multiplier; /* default 1.15 */
    double production_safety_factor;  /* default 0.45 */
    int offer_expiry_days;            /* default 3 */
    int offer_interval_days;          /* default 7 -- contracts.py:62 */
    double default_penalty_rate;      /* default 0.35 -- contracts.py:101 */
    double relationship_gain_per_delivery;  /* default 6.0 -- DEFAULT_RELATIONSHIP_GAIN */
    double relationship_loss_per_failure;   /* default 5.0 -- DEFAULT_RELATIONSHIP_LOSS */
    double relationship_bonus_cap;          /* default 0.25 -- DEFAULT_RELATIONSHIP_BONUS_CAP */
} ContractsConfig;

/* config/markets.json's top-level fields (not per-channel -- see ChannelDef
 * for those). Phase 2 (markets.c) field: markets_update_daily_prices reads
 * both to compute each item's saturation-adjusted price. */
typedef struct {
    double minimum_supply_multiplier; /* default 0.65 */
    double supply_decay;              /* default 0.75 */
} MarketsConfig;

/* config/storage.json (simulation/inventory.py's storage_config parameter).
 * Phase 2 (inventory.c) fields: capture_storage_liability/age_and_spoil read
 * daily_cost and shelf_life_multiplier; age_and_spoil/enforce_storage_capacity
 * read capacity. */
typedef struct {
    double daily_cost;           /* default 0.0 */
    int capacity;                /* default 100 */
    double shelf_life_multiplier; /* default 1.0 */
} StorageConfig;

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
    WateringConfig watering;
    SoilDynamics soil_dynamics;
    PlotRegen plot_regen;
    WeatherParams weather;
    MarketsConfig markets;
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
