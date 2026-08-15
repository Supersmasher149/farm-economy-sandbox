# Pure C Port Plan

## Recommended Port Strategy

Do not translate Python dictionaries and lists directly into C. The clean port is:

- Immutable, indexed configuration.
- Mutable per-run simulation state.
- Integer IDs instead of string lookups during simulation.
- Explicit dynamic arrays instead of Python lists.
- Fixed daily orchestration.
- Agents exposed through function-pointer interfaces.
- One isolated RNG/state object per run.

The current implementation is divided into:

| Python area | C responsibility |
|---|---|
| `simulation/configuration.py` | Load, resolve defaults, and validate configuration |
| `simulation/derived.py` | Build indexed lookup tables and cached profiles |
| `simulation/state.py` | Runtime state structures |
| `simulation/actions.py` | Validated state mutations |
| `simulation/crop_growth.py` | Pure crop/stress calculations |
| `simulation/weather.py` | Weather generation and soil/crop updates |
| `simulation/inventory.py` | Lot aging, spoilage, FEFO consumption |
| `simulation/markets.py` | Prices, quotes, and sales |
| `simulation/contracts.py` | Offers, deliveries, forecasting, penalties |
| `simulation/processing.py` | Processing jobs |
| `simulation/engine.py` | Fixed daily order |
| `agents/*.py` | Strategy callbacks |
| `runner/*.py` | Single runs and batches |
| `metrics/*.py` | Results and aggregation |

The `vectorized/` directory is a separate simplified simulator. Do not include it in the first C port.

## 1. Configuration Model

Configuration should be loaded once and then treated as immutable.

Use integer indexes internally:

```c
typedef uint32_t ItemId;
typedef uint32_t CropId;
typedef uint32_t UpgradeId;
typedef uint32_t RecipeId;
typedef uint32_t BuyerId;
typedef uint32_t ChannelId;

#define INVALID_ID UINT32_MAX
```

String IDs should only be used while loading configuration or printing reports.

### Unified Item Table

Crops and processed products participate in the same inventory and market systems. Create one unified item table:

```c
typedef enum {
    ITEM_CROP,
    ITEM_PRODUCT
} ItemType;

typedef struct {
    ItemId id;
    ItemType type;

    char *external_id;
    char *name;

    double base_price;
    double price_variation;
    double seasonal_demand[4];
} ItemDef;
```

Each crop also has crop-specific production fields:

```c
typedef struct {
    ItemId item_id;

    char *role;
    char *family;

    double seed_cost;
    int growth_days;
    int min_yield;
    int max_yield;
    double loss_chance;
    int water_interval_days;
    int shelf_life_days;

    double temperature_low;
    double temperature_high;
    double ph_low;
    double ph_high;
    double min_moisture;

    double pest_susceptibility;
    double disease_susceptibility;

    double nutrient_demand[3];

    enum {
        UNLOCK_NONE,
        UNLOCK_REVENUE,
        UNLOCK_UPGRADE
    } unlock_type;

    double unlock_revenue;
    UpgradeId unlock_upgrade;
} CropDef;
```

Relationships:

```text
CropDef.item_id
    |
    v
ItemDef
    |
    +--> market_prices[item_id]
    +--> market_supply[item_id]
    +--> InventoryLot.item_id
    +--> Recipe.input_item_id/output_item_id
    +--> Buyer.allowed_items[]
    +--> Contract.item_id
```

### Upgrades

Represent upgrade effects as a tagged union:

```c
typedef enum {
    EFFECT_CAPACITY,
    EFFECT_GROWTH_TIME_REDUCTION,
    EFFECT_STORAGE,
    EFFECT_PROCESSING_CAPACITY
} UpgradeEffectType;

typedef struct {
    UpgradeEffectType type;

    union {
        int capacity;
        double growth_time_reduction;

        struct {
            int capacity_bonus;
            double shelf_life_multiplier;
        } storage;

        int processing_capacity;
    };
} UpgradeEffect;

typedef struct {
    UpgradeId id;
    char *external_id;
    char *name;
    double cost;
    UpgradeEffect effect;
} UpgradeDef;
```

Owned upgrades should be represented by a boolean array or bitset:

```c
bool upgrades_owned[upgrade_count];
```

The shipped project has only four upgrades, so a `uint64_t` bitset is also sufficient.

### Recipes

```c
typedef struct {
    RecipeId id;
    char *external_id;

    ItemId input_item_id;
    ItemId output_item_id;

    int input_quantity;
    int output_quantity;
    int processing_days;
    int min_quality;
    int shelf_life_days;

    double cost;
} RecipeDef;
```

### Buyers and Contracts

```c
typedef struct {
    BuyerId id;
    char *external_id;
    char *name;

    ItemId *allowed_items;
    size_t allowed_item_count;

    int quantity_min;
    int quantity_max;
    int min_quality;

    double contract_price_multiplier;
    int deadline_days;
    double penalty_rate;
    double min_reputation;
    double relationship_bonus_rate;
} BuyerDef;
```

A runtime contract contains indexes, not strings:

```c
typedef struct {
    uint64_t contract_number;

    BuyerId buyer_id;
    ItemId item_id;

    int quantity;
    int delivered;
    int min_quality;

    double unit_price;
    double penalty_rate;

    int offered_day;
    int deadline_day;

    bool accepted;
    bool resolved;
} ContractState;
```

Keep offers and active contracts in separate vectors, as the Python implementation does.

## 2. Runtime State

Each simulation run gets its own `FarmState`. It owns all mutable run data and only references immutable configuration.

```c
typedef struct {
    double moisture;
    double nitrogen;
    double phosphorus;
    double potassium;
    double ph;
    double soil_health;
    double pest_pressure;
    double disease_pressure;

    int previous_crop_family;
    int planted_index;
} PlotState;
```

Use `INVALID_ID` for an empty plot.

### Planted Crops

```c
typedef struct {
    ItemId crop_item_id;
    int day_planted;
    int growth_days_required;

    int last_watered_day;
    int neglect_days;

    bool fertilized;
    int plot_index;

    double water_stress;
    double nutrient_stress;
    double temperature_stress;
    double pest_stress;
    double disease_stress;

    double accrued_cost;
} PlantedCrop;
```

Use a dynamic array:

```c
typedef struct {
    PlantedCrop *data;
    size_t count;
    size_t capacity;
} PlantedCropVec;
```

The Python implementation maintains both `player.planted` and `plot.crop`. Do not duplicate the crop object in C. Store the crop in `planted[]` and keep only its index in the plot. When removing or compacting planted crops, update the corresponding plot indexes.

Preserve planted-array order. Harvest RNG calls occur in planted-list order. Iterating plots instead could change random draw order after plots are replanted.

### Inventory Lots

```c
typedef enum {
    QUALITY_REJECTED = 0,
    QUALITY_PROCESSING = 1,
    QUALITY_STANDARD = 2,
    QUALITY_PREMIUM = 3
} Quality;

typedef struct {
    ItemId item_id;
    int quantity;
    Quality quality;

    int produced_day;
    int age_days;
    int shelf_life_days;
    int effective_shelf_life_days;

    double unit_cost;
    ItemType item_type;
} InventoryLot;
```

Use a dynamic vector:

```c
typedef struct {
    InventoryLot *data;
    size_t count;
    size_t capacity;
} InventoryLotVec;
```

Consumption is FEFO, not insertion order:

```text
lowest remaining shelf life first
then quality rank
```

Sales use a slightly different quality tie-breaker. Preserve the two existing sort rules rather than creating one generic inventory sort.

### Processing Jobs

```c
typedef struct {
    RecipeId recipe_id;
    ItemId output_item_id;

    int output_quantity;
    int completion_day;
    int shelf_life_days;

    double unit_cost;
} ProcessingJob;
```

### Main Farm State

```c
typedef struct {
    double money;
    double operating_reserve;

    int day;
    int total_days;
    int slots_total;

    PlotState *plots;
    size_t plot_count;

    PlantedCropVec planted;
    InventoryLotVec inventory_lots;
    ProcessingJobVec processing_jobs;

    int *seed_inventory;
    int fertilizer_inventory;
    double water_units;

    bool *upgrades_owned;
    int *upgrade_purchase_days;
    int *crop_plant_counts;

    ContractVec active_contracts;
    ContractVec contract_offers;

    double *buyer_relationships;
    double reputation;

    double *market_prices;
    double *market_supply;
    int *channel_capacity_used;

    FarmMetrics metrics;

    bool bankrupt;
    int bankruptcy_day;
    char *bankruptcy_reason;
} FarmState;
```

Arrays are sized from loaded configuration:

```text
seed_inventory[crop_index]
crop_plant_counts[crop_index]
market_prices[item_index]
market_supply[item_index]
channel_capacity_used[channel_index]
buyer_relationships[buyer_index]
```

## 3. Metrics

Use fixed indexes instead of string-keyed dictionaries:

```c
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
```

```c
typedef struct {
    double expenses[EXPENSE_COUNT];
    double revenue_by_channel[channel_count];

    int quality_harvested[4];

    int total_planted;
    int total_harvested;
    int total_sold;
    int total_crops_lost;
    int total_harvest_events;

    int total_waterings;
    int total_fertilizer_bought;
    int total_fertilizer_applied;
    int total_spoiled;
    int total_processed;

    int contracts_completed;
    int contracts_failed;

    double contract_penalties;
    double processing_revenue;
    double total_revenue;
    double total_expenses;

    int slot_days;
    int occupied_slot_days;
    int idle_days;

    double lowest_money;
    double highest_money;
} FarmMetrics;
```

Crop decision observations should be arrays indexed by crop:

```c
typedef struct {
    int opportunities;
    int unlocked;
    int affordable;
    int selected;
    int blocked_locked;
    int blocked_unaffordable;
} CropDecisionObservation;
```

## 4. Derived Configuration

The Python `derived.py` layer should become a `ResolvedConfig` or `WorldLookups` structure built once after loading:

```c
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

    CropProfile *crop_profiles;
    MarketProfile *market_profiles;
    WeatherParams weather;
    SoilDynamics soil_dynamics;
} ResolvedConfig;
```

Resolve during initialization:

- Crop defaults.
- Product defaults.
- Soil defaults.
- Weather season arrays.
- Quality ranks.
- Crop profiles.
- Market profiles.
- Item references.
- Recipe indexes.
- Buyer item indexes.
- Channel indexes.

Avoid hash tables in the simulation loop. A small linear ID lookup during configuration loading is acceptable. Once loaded, simulation code should use integer indexes.

If using a hash table for loading, never iterate it to determine simulation order. Preserve original JSON array order because that order controls RNG draw order.

## 5. Daily Engine

The actual `simulation/engine.py` order is authoritative. Port this order exactly:

1. Add `slots_total` to `slot_days`.
2. Add planted count to `occupied_slot_days`.
3. Generate weather.
4. Apply weather to every plot and growing crop.
5. Capture storage liability.
6. Harvest mature crops.
7. Age inventory and spoil it.
8. Complete processing jobs.
9. Enforce storage capacity again for newly completed jobs.
10. Update daily market prices.
11. Generate contract offers.
12. Agent accepts contracts.
13. Agent delivers contracts.
14. Agent starts processing jobs.
15. Agent submits sales.
16. Agent buys upgrades.
17. Agent waters crops.
18. Agent fertilizes crops.
19. Plant open slots.
20. Resolve expired contracts.
21. Collect captured storage liability.
22. Finish bookkeeping.
23. Check bankruptcy.
24. Increment the day.

Do not move price generation earlier merely because an older design document describes it that way. Moving RNG-consuming phases changes results.

## 6. Agent Interface

Replace Python inheritance with a vtable:

```c
typedef struct Agent Agent;

typedef struct {
    const char *name;
    const char *description;
    double watering_diligence;

    ItemId (*choose_crop)(
        const Agent *,
        const FarmState *,
        const ResolvedConfig *
    );

    bool (*should_buy_upgrade)(
        const Agent *,
        const FarmState *,
        UpgradeId
    );

    bool (*should_water)(
        const Agent *,
        const FarmState *,
        int planted_index
    );

    bool (*should_fertilize)(
        const Agent *,
        const FarmState *,
        int planted_index
    );

    void (*choose_contracts)(
        const Agent *,
        const FarmState *,
        ContractDecisionBuffer *
    );

    void (*choose_deliveries)(
        const Agent *,
        const FarmState *,
        DeliveryDecisionBuffer *
    );

    void (*choose_processing)(
        const Agent *,
        const FarmState *,
        ProcessingDecisionBuffer *
    );

    void (*choose_sales)(
        const Agent *,
        const FarmState *,
        SalesDecisionBuffer *
    );

    bool (*should_use_fertilizer)(
        const Agent *,
        const FarmState *,
        CropId
    );
} AgentVTable;
```

Agents receive `const FarmState *`. They return decisions into temporary buffers. They must never mutate state.

```c
typedef struct {
    ItemId item_id;
    ChannelId channel_id;
    Quality quality;
    int quantity;
} SaleDecision;
```

The engine validates and applies every decision.

Port the roster in this order:

1. `FastSeller`
2. `NoUpgradePlayer`
3. `NeglectfulGrower`
4. `RecklessSpender`
5. `RiskAverseGrower`
6. `Diversifier`
7. `UpgradeRusher`
8. `ProgressionPlayer`
9. `ProfitOptimizer`
10. `FertilizerMaximalist`
11. `RandomAgent`

Control agents should share function implementations where possible, as they inherit from `ProfitOptimizer` in Python.

## 7. RNG and Floating-Point Behavior

This is the most important compatibility decision.

C's `rand()` is not compatible with Python's `random.Random`. If C must reproduce Python seeds, implement:

- Python-compatible MT19937 state.
- Python-compatible integer generation.
- Python-compatible `_randbelow` behavior.
- Python-compatible uniform generation.
- Python-compatible choice behavior.
- Python-compatible seed initialization.

The event RNG is one stream per run. Preserve draw order exactly:

```text
weather temperature
rain chance
rainfall if raining
market price for each item
contract item choice
contract quantity
harvest loss roll
harvest yield roll
watering diligence roll
agent-independent event rolls
```

Do not add convenience random calls or use separate RNGs without intentionally changing behavior.

For floating point:

- Use `double`.
- Disable fast-math compiler options.
- Disable floating-point contraction if exact replay matters.
- Preserve arithmetic order.
- Implement Neumaier summation where Python currently relies on compensated `sum()`.
- Implement Python-style round-to-even behavior.
- Avoid replacing Python's literal `max`/`min` behavior with `fmax`/`fmin` without checking signed-zero and NaN behavior.
- Compare float output with `%a` formatting or equivalent hexadecimal representation.

There are two valid targets:

| Target | Requirement |
|---|---|
| Behavioral C port | Same rules and similar results, with C becoming the new reference |
| Bit-compatible Python port | Same seeds, RNG stream, floating-point operations, rounding, and trajectory |

The second target is substantially more work. Decide this before implementation.

The Python `RandomAgent` also uses a deterministic hash-based policy stream rather than the event RNG. Either port the existing BLAKE2b-based behavior or define a new stable C hash and accept changed random-agent results.

## 8. Processing Forecasts

Do not simplify contract forecasting into:

```text
future inputs / recipe input quantity
```

The existing `_item_capacity()` logic is timeline-aware. It tracks:

- Existing inventory arriving today.
- Future harvest arrival days.
- Processing slot availability.
- Recipe duration.
- Completion deadlines.
- Recipes sharing the same input.
- Input quantities consumed by earlier planned recipes.
- Seed cash needed for future plantings.
- Run-horizon cutoff.

Represent forecast arrivals in C as:

```c
typedef struct {
    int day;
    double quantity;
    bool is_future;
} InputArrival;
```

Then model each processing slot's next-free day:

```c
int slot_free_day[processing_capacity];
```

A batch is feasible only if:

```text
inputs have arrived
a processing slot is free
start_day + processing_days <= deadline
```

This is one place where an apparently simpler port would produce materially different contracts and agent decisions.

## 9. Ownership and Memory

Use a clear ownership model:

| Object | Owner |
|---|---|
| Configuration strings and arrays | `Config` |
| Derived profiles | `ResolvedConfig` |
| Per-run arrays and vectors | `FarmState` |
| Agent policy data | Agent instance or static constants |
| Decision buffers | Engine call scope |
| Run result | Result object or output callback |
| Reports | Reporting layer |

Rules:

- `FarmState` must not free configuration memory.
- Agents must not own or free state.
- Do not store pointers into vectors that may later `realloc`.
- Prefer indexes or handles over pointers.
- Centralize vector growth and destruction.
- Add `config_destroy()`, `resolved_config_destroy()`, `farm_state_destroy()`, and `run_result_destroy()`.
- Use AddressSanitizer and UndefinedBehaviorSanitizer during development.

## 10. Configuration Loading Options

Three practical choices:

1. Keep JSON and write or use a small C JSON loader.
2. Convert configuration into generated C initializer tables.
3. Replace JSON with a simpler custom text format.

Recommended sequence:

- Initially generate or embed C configuration tables so the simulator can be built and tested quickly.
- Keep the original JSON files as the balance-authoring source.
- Add a JSON loader only after simulation behavior is stable, if runtime configuration editing is required.

The configuration layer should expose the same resolved structures regardless of how data was loaded.

Validation must still enforce:

- Required fields.
- Unknown-field rejection.
- Unique IDs.
- Valid enum values.
- Numeric ranges.
- Valid cross-references.
- Valid crop, recipe, product, buyer, channel, and upgrade relationships.
- Finite numeric values.

## 11. Batch Runner

Implement the batch runner only after a correct sequential single-run implementation exists.

The Python runner has two determinism guarantees:

- Per-run seeds are generated in agent-major order.
- Worker count does not affect the generated seed sequence or results.

C batch design:

```text
base_seed
    |
    v
seed generator
    |
    +--> strategy 1, run 1
    +--> strategy 1, run 2
    +--> strategy 2, run 1
    ...
```

Each run receives:

- A fresh `FarmState`.
- A fresh RNG state.
- A fresh agent instance or immutable agent descriptor.
- Read-only shared configuration.

Start with sequential batches. Add parallel execution later using pthreads or C11 threads.

The aggregator should consume results incrementally rather than storing every run. Maintain:

- Running sums.
- Compensated sums.
- Min/max values.
- Per-strategy counts.
- Crop totals.
- Revenue totals.
- Expense totals.
- Bankruptcy counts.
- Fixed-size median reservoirs if required.

## 12. Reporting and CLI

Port these in stages.

### First Version

- `single`
- `replay`
- `batch`
- CSV output
- Per-run result structure
- Basic terminal summary

### Later Version

- JSON summary
- Markdown report
- Warning detection
- `view`
- Before/after run diffs
- HTML dashboard

Charts are not part of the simulation core. Keep reporting dependent on serialized results, not on `FarmState` internals.

## 13. Suggested C Project Layout

```text
farm-c/
  include/
    farm_types.h
    config.h
    state.h
    rng.h
    inventory.h
    growth.h
    weather.h
    markets.h
    contracts.h
    processing.h
    actions.h
    economy.h
    agent.h
    metrics.h

  src/
    config_loader.c
    config_validation.c
    config_derived.c
    state.c
    rng.c
    inventory.c
    crop_growth.c
    weather.c
    markets.c
    contracts.c
    processing.c
    actions.c
    economy.c
    engine.c
    metrics.c
    runner.c
    agents/
      fast_seller.c
      profit_optimizer.c
      ...

  tests/
    test_rng.c
    test_inventory.c
    test_growth.c
    test_markets.c
    test_contracts.c
    test_processing.c
    test_engine.c
    test_agents.c
    test_replay.c

  config/
    crops.json
    upgrades.json
    ...

  Makefile
  README.md
```

## 14. Recommended Implementation Order

1. Freeze the Python reference configuration and representative seeds.
2. Decide whether C must be bit-compatible with Python.
3. Define all C types and ownership rules.
4. Implement dynamic vectors and cleanup functions.
5. Implement configuration structures and ID resolution.
6. Implement configuration validation.
7. Implement the RNG layer.
8. Implement `FarmState`, plots, planted crops, and inventory.
9. Implement basic actions: seeds, planting, watering, fertilizer, upgrades.
10. Implement crop stress and harvesting.
11. Implement weather and soil regeneration.
12. Implement inventory aging, FEFO, and storage.
13. Implement markets and sales.
14. Implement processing jobs.
15. Implement contracts and timeline-aware forecasting.
16. Implement the fixed daily engine.
17. Port agents through the vtable interface.
18. Implement single-run output and history callbacks.
19. Implement metrics and per-run results.
20. Implement sequential batch aggregation.
21. Add replay and trajectory comparison.
22. Add parallel batches.
23. Add reporting and charts if still needed.

## 15. Verification Plan

Use the Python implementation as an oracle during the port.

Test in layers:

- Configuration fixtures with invalid fields and references.
- RNG sequences for fixed seeds.
- Weather sequences.
- Crop stress updates.
- Harvest outcomes.
- FEFO inventory consumption.
- Shelf-life downgrade and spoilage.
- Storage capacity overflow.
- Market price sequences.
- Contract generation and penalties.
- Processing capacity and completion.
- Agent decisions.
- Full daily traces.
- Bankruptcy boundaries.
- Sequential versus parallel batches.

For each reference run, record:

```text
strategy
seed
money as hexadecimal float
revenue and expenses
planted crops
inventory lots
soil values
market prices
contracts
processing jobs
bankruptcy status
```

Hash the complete daily trajectory, not only the final result. A run can finish with the same money while taking a different path.

The most important implementation rule is to preserve the current separation:

```text
Agent decides
    |
    v
Engine validates and applies
    |
    v
FarmState changes
```

That separation, together with immutable indexed configuration and explicit ownership, will make the C version considerably easier to reason about than a direct dictionary-to-struct translation.
