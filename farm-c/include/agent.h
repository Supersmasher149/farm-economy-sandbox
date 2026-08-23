/* Agent decision interface: a C vtable replacing Python inheritance, per
 * docs/c-port-plan.md Section 6. Agents receive `const FarmState *` and
 * `const ResolvedConfig *` and return decisions into buffers the caller
 * owns -- they must never mutate state (the engine is the only thing that
 * applies decisions; see agents/base.py's own docstring,
 * which this mirrors).
 *
 * All 11 ported agents are stateless singletons (every Python subclass only
 * ever varies class-level constants: name, description, watering_diligence,
 * and which methods are overridden -- none carries per-instance data), so
 * unlike the design doc's separate opaque `Agent` handle, the vtable *is*
 * the agent instance here: `Agent` and `AgentVTable` are the same type, and
 * every function pointer's first argument is a pointer back to its own
 * table. This also makes the "control agents share function implementations
 * where possible" note (docs/c-port-plan.md:662) literal: NeglectfulGrower's
 * table copies ProfitOptimizer's function pointers field-for-field and only
 * overwrites `watering_diligence`, which farm-c/tests/test_agents.c checks
 * by asserting those function pointers are still `==` -- the C analogue of
 * tests/test_strategy_controls.py's `NeglectfulGrower.choose_crop is
 * ProfitOptimizer.choose_crop` identity assertions.
 */
#ifndef FARM_AGENT_H
#define FARM_AGENT_H

#include <stddef.h>

#include "config.h"
#include "farm_types.h"
#include "state.h"

typedef struct AgentVTable AgentVTable;
typedef AgentVTable Agent;

/* The decision-buffer types (ContractDecisionBuffer, ...) and their
 * push/free functions moved to state.h, because FarmState now owns one
 * of each for the engine to reuse across days -- see "Decision buffers"
 * there. This header still declares the vtable that consumes them, and
 * still includes state.h, so every agent sees them exactly as before. */

struct AgentVTable {
    const char *name;
    const char *description;
    double watering_diligence;

    /* Returns INVALID_ID to leave the slot open (Python: return None). */
    ItemId (*choose_crop)(const Agent *self, const FarmState *state, const ResolvedConfig *config);

    bool (*should_buy_upgrade)(const Agent *self, const FarmState *state, UpgradeId upgrade_id);

    bool (*should_water)(const Agent *self, const FarmState *state, int planted_index);

    bool (*should_fertilize)(const Agent *self, const FarmState *state, int planted_index);

    void (*choose_contracts)(const Agent *self, const FarmState *state,
                              const ResolvedConfig *config, ContractDecisionBuffer *out);

    void (*choose_contract_deliveries)(const Agent *self, const FarmState *state,
                                        DeliveryDecisionBuffer *out);

    void (*choose_processing)(const Agent *self, const FarmState *state,
                               const ResolvedConfig *config, ProcessingDecisionBuffer *out);

    void (*choose_sales)(const Agent *self, const FarmState *state, const ResolvedConfig *config,
                          SalesDecisionBuffer *out);

    bool (*should_use_fertilizer)(const Agent *self, const FarmState *state, ItemId crop_item_id);
};

/* --- The 11 agents + the registry, mirroring main.py's AGENT_REGISTRY. --- */

extern const Agent AGENT_FAST_SELLER;
extern const Agent AGENT_PROFIT_OPTIMIZER;
extern const Agent AGENT_PROGRESSION_PLAYER;
extern const Agent AGENT_NEGLECTFUL_GROWER;
extern const Agent AGENT_RECKLESS_SPENDER;
extern const Agent AGENT_RANDOM_AGENT;
extern const Agent AGENT_NO_UPGRADE_PLAYER;
extern const Agent AGENT_FERTILIZER_MAXIMALIST;
extern const Agent AGENT_DIVERSIFIER;
extern const Agent AGENT_RISK_AVERSE_GROWER;
extern const Agent AGENT_UPGRADE_RUSHER;

typedef struct {
    const char *strategy_name; /* e.g. "fast_seller", matches main.py's key */
    const Agent *agent;
} AgentRegistryEntry;

/* NULL-name-terminated, in the same order as docs/c-port-plan.md's "port
 * the roster in this order" list. */
extern const AgentRegistryEntry AGENT_REGISTRY[];
const Agent *agent_registry_find(const char *strategy_name);

/* --- agents/base.c: shared default implementations every vtable can point
 * at directly, exactly as Python subclasses inherit agents/base.py's
 * concrete methods without overriding them. --- */

bool agent_base_should_water(const Agent *self, const FarmState *state, int planted_index);
void agent_base_choose_contract_deliveries(const Agent *self, const FarmState *state,
                                            DeliveryDecisionBuffer *out);
void agent_base_choose_processing(const Agent *self, const FarmState *state,
                                   const ResolvedConfig *config, ProcessingDecisionBuffer *out);
/* base.py's naive default: dump every lot at "spot", full lot quantity,
 * quality untouched (SALE_QUALITY_ANY). */
void agent_base_choose_sales(const Agent *self, const FarmState *state,
                              const ResolvedConfig *config, SalesDecisionBuffer *out);
bool agent_base_should_use_fertilizer(const Agent *self, const FarmState *state,
                                       ItemId crop_item_id);
/* Never overridden to return true by any of the 11 agents except through
 * should_fertilize's own default (also false) -- kept distinct from
 * should_use_fertilizer per agents/base.py:34-35 vs. :56-61. */
bool agent_base_should_fertilize(const Agent *self, const FarmState *state, int planted_index);
void agent_base_choose_contracts(const Agent *self, const FarmState *state,
                                  const ResolvedConfig *config, ContractDecisionBuffer *out);

/* agents/base.py:63-126 route_sales_by_best_price -- real logic (not a
 * stub), opted into by every agent whose Python class calls
 * `self.route_sales_by_best_price(...)` from its own choose_sales. */
void agent_route_sales_by_best_price(const FarmState *state, const ResolvedConfig *config,
                                      SalesDecisionBuffer *out);

#endif /* FARM_AGENT_H */
