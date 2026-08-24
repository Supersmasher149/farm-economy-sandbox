# farm-c `run_day` Engine

## Goal

Add the modern, world-driven daily engine to `farm-c` while preserving the
Python simulator's load-bearing 24-step order, mutation semantics, decision
buffer boundaries, and deterministic RNG draw order.

The legacy `world=None` Python path is explicitly out of scope. In
particular, this work does not add `water_farm` or `sell_all`.

## Public API

Add `farm-c/include/engine.h` with:

```c
typedef enum {
    ENGINE_ERROR_NONE,
    ENGINE_ERROR_ARGUMENT,
    ENGINE_ERROR_ALLOCATION
} EngineErrorCode;

typedef struct {
    EngineErrorCode code;
    char message[256];
} EngineError;

bool engine_run_day(FarmState *state, const Agent *agent, FarmRng *rng,
                    EngineError *error);
```

The engine reads the borrowed `ResolvedConfig` from `state->config`. The
caller owns the state, agent, RNG, and configuration. A false return indicates
an invalid argument or decision-buffer allocation failure; ordinary rejected
agent decisions are not engine errors and do not abort the day. Allocation
failure is not rolled back, matching the existing mutator ownership model, but
all temporary decision buffers are freed before returning.

## State Additions

Extend `FarmState` with the run bookkeeping currently maintained by Python's
`PlayerState` and `_finish_day`:

- `slot_days` and `occupied_slot_days`;
- `lowest_money`;
- `bankrupt`, `bankruptcy_day`, and an owned bankruptcy reason string;
- `idle_days` remains the existing acted-day counter and is updated by the
  final bookkeeping path; no additional acted-day field is needed.

`farm_state_init` initializes these exactly as a fresh Python `PlayerState`
does, and `farm_state_destroy` frees any owned reason string. The engine never
owns configuration or agent memory.

## Exact Daily Order

`engine_run_day` contains explicit, numbered blocks in this order. No helper
may reorder, parallelize, or combine RNG-consuming phases:

1. Add `slots_total` to `slot_days`.
2. Add planted count to `occupied_slot_days`.
3. Generate weather for `state->day`.
4. Apply weather to plots and growing crops.
5. Capture storage liability using effective storage capacity/terms.
6. Harvest mature crops.
7. Age inventory and spoil it without charging storage a second time.
8. Complete processing jobs.
9. Enforce storage capacity again for outputs completed after aging.
10. Update daily market prices.
11. Generate contract offers.
12. Ask the agent for contract acceptances and apply them in buffer order.
13. Ask for contract deliveries and apply them in buffer order.
14. Ask for processing decisions and start valid jobs in buffer order.
15. Ask for sales decisions and execute valid sales in buffer order.
16. Evaluate and buy upgrades in configuration order.
17. Evaluate and perform watering decisions in planted-list order, including
    the agent diligence RNG roll before applying a watering action.
18. Evaluate fertilizer decisions in planted-list order, buying one unit when
    required and applying it only when valid.
19. Plant open slots using the same crop observation, affordability, unlock,
    fertilizer, seed-purchase, and effective-growth-day rules as Python.
20. Resolve expired contracts.
21. Collect the liability captured in step 5.
22. Perform no-op bookkeeping when no action occurred during the day.
23. Update low/high cash and evaluate the no-viable-reinvestment bankruptcy
    condition.
24. Increment `state->day`.

All agent decision buffers are created immediately before their corresponding
decision phase, consumed in order, and freed immediately afterward. The
engine applies decisions through existing action/market/contract/processing
mutators; agents never receive mutable state and never mutate it directly.

## Derived Configuration

The loader's resolved fields are used directly. The engine computes effective
storage and processing capacity from owned upgrades without rebuilding lookup
tables each day. These calculations must not consume RNG or alter iteration
order.

The engine sets the state references/fields needed by existing decision and
mutator code before step 3, including processing capacity and current resolved
world settings. It does not copy configuration dictionaries or retain cJSON
objects.

## Determinism and Failure Handling

Weather, market, contract, harvest, and watering randomness uses the single
`FarmRng` passed to the engine. No convenience draw may be introduced. The
existing item, crop, upgrade, planted, buyer, channel, and decision-buffer
orders are preserved.

Rejected decisions are ordinary simulation outcomes. A successful mutation,
including a partial sale or delivery, marks the day as acted; failed or empty
decisions do not. The final no-op action and bookkeeping follow Python's
`_finish_day` semantics.

## Tests

Add an engine test binary to `make test` covering:

- exact step ordering with a fixture agent that records calls and requests
  actions in every decision phase;
- weather-before-harvest and age-before-processing behavior;
- post-processing capacity enforcement before contracts/sales;
- decision buffer application order and rejected decisions;
- watering diligence RNG use and planted-list order;
- fertilizer purchase/application atomicity and planting behavior;
- storage liability capture/collection and acted/no-op bookkeeping;
- bankruptcy boundary, day increment, and state cleanup;
- same-seed repeatability for a full multi-day run with a registered agent.

The existing RNG, physics, mutation, and configuration suites must remain
green. If a full Python-vs-C daily fixture is available, compare the complete
state snapshot after each day, not only final cash.
