# Full Crop and Market Simulation Design

## Goal

Expand the deterministic farm economy sandbox into a configurable simulation
that gives crop production and selling strategy equal weight. A successful
implementation lets automated strategies make meaningful choices about crop
care, inventory timing, sales channels, contracts, and processing while batch
reports explain the economic consequences.

## Scope

The first complete version includes:

- Plot soil moisture, fertility, pH, crop rotation, pests, and disease.
- Seasonal weather with temperature and rainfall.
- Crop-specific environmental preferences and shelf life.
- Harvest lots with quantity, quality, age, and production cost.
- Storage capacity, operating costs, quality decline, and spoilage.
- Daily crop prices shared by all sellers in a run.
- Spot, wholesale, farm-stand, processor, and specialty sales channels.
- Generated contracts with quantity, quality, deadline, reward, and penalty.
- Buyer reputation earned or lost through contract performance.
- Configurable processing recipes and capacity-limited processing jobs.
- Agent hooks for care, contracts, processing, sales, planting, and upgrades.
- Metrics and reports for production, losses, channels, contracts, and processing.

The simulator remains headless, configuration-driven, reproducible from one
seed, and usable through the existing single, replay, and batch CLI commands.

## Architecture

`engine.py` owns the fixed daily order but delegates calculations and mutations.
Pure domain calculations live in focused modules; state changes remain in
action/service functions. Agents return decisions and never mutate state.

The daily order is:

1. Generate the day's weather and market prices.
2. Apply rainfall, soil changes, crop stress, pests, and disease.
3. Harvest mature crops into graded inventory lots.
4. Age stored lots, downgrade quality, and discard spoiled goods.
5. Complete processing jobs and generate new contract offers.
6. Apply agent contract, processing, and sales decisions.
7. Buy upgrades and farm inputs.
8. Plant open plots and apply crop care.
9. Resolve expired contracts and storage costs.
10. Record bookkeeping and advance the day.

## State

Each growing slot is represented by a `PlotState`, which holds soil state and an
optional `PlantedCrop`. `PlantedCrop` records accumulated stress, care, and the
growth duration fixed at planting time. `InventoryLot` records crop or product,
quantity, quality grade, harvest/production day, shelf life, and unit cost.

`PlayerState` owns plots, lots, inputs, processing jobs, contracts, reputation,
and aggregate metrics. Compatibility properties expose planted crops,
`slots_total`, and aggregate crop inventory to existing consumers.

## Crop Production

Environmental fit produces separate yield and quality multipliers. Moisture,
N/P/K, pH, temperature, pests, disease, neglect, rotation, and fertilizer are
capped so no single factor creates unbounded outcomes. Harvest loss remains a
seeded roll. Surviving crops receive a quality score mapped to premium,
standard, processing, or rejected grades.

Weather is generated once per day from seasonal configuration. Rain updates all
plots; temperature affects crops according to crop-specific preferred ranges.
Care actions consume configured water or fertilizer and update expenses.

## Inventory and Processing

Inventory is first-in, first-out within a quality grade. Lots downgrade as they
age and eventually spoil. Storage configuration defines capacity, daily cost,
and shelf-life multiplier. Overflow spoils immediately.

Processing recipes accept specified crops and minimum grades, consume lots,
charge a processing cost, occupy capacity for a fixed duration, and produce a
new product lot. Processed products participate in markets and contracts through
the same inventory interface.

## Markets and Selling

One daily reference price is generated per crop or product, using seasonal
demand, bounded volatility, and accumulated market supply pressure. Sales
channels quote against that shared reference price and define accepted grades,
daily capacity, price multiplier, fees, and reputation requirements.

Agents submit sales decisions containing the item, channel, and quantity. The
transaction service validates availability and channel rules, consumes eligible
lots, and records net revenue by channel. Unsold goods remain in storage.

## Contracts and Reputation

Contract offers are generated deterministically at configured intervals. An
accepted contract reserves no inventory automatically; the agent must plan
production and delivery. A delivery consumes matching lots and updates delivered
quantity. Completion pays the contract price and raises reputation. Expiry
charges a bounded shortfall penalty and lowers reputation.

## Agents

The base agent provides safe defaults for new decision hooks so custom agents
only need to override decisions they care about. Existing strategies retain
their identities:

- Fast seller favors short crops and immediate spot sales.
- Profit optimizer compares expected production and selling value.
- Progression player favors balanced production, contracts, and upgrades.

## Configuration and Validation

New configuration files define weather, soil, markets, buyers, contracts,
processing, and storage. Load-time validation checks required IDs and references.
Defaults are applied only for optional fields, allowing test fixtures and simple
custom configurations to remain concise.

## Metrics and Testing

Metrics include revenue by channel, quality distribution, spoilage, crop losses,
water/fertilizer use, contract outcomes, processing margin, reputation, and
existing profitability measures.

Tests cover pure calculations, state transitions, deterministic replay, daily
ordering, and invariants: no negative money from invalid actions, no negative
inventory, no duplicated sales, no overfilled processing capacity, and identical
results for identical seeds and decisions.

## Delivery Order

Implementation proceeds through state/config foundations, production, inventory,
markets, contracts/processing, agents/engine, metrics, and verification. Each
layer is usable by the next and can be tested independently.
