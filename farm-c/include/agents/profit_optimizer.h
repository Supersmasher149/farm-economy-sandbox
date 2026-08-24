/* ProfitOptimizer's individual decision functions, given external linkage
 * (not `static`) purely so NoUpgradePlayer/NeglectfulGrower/
 * FertilizerMaximalist can copy ProfitOptimizer's AGENT_PROFIT_OPTIMIZER
 * vtable and override exactly the fields their Python classes override --
 * the same function pointers, not reimplementations, matching Python
 * inheritance (agents/no_upgrade_player.py, agents/neglectful_grower.py,
 * agents/fertilizer_maximalist.py all subclass ProfitOptimizer directly).
 * See agent.h's header comment on why the vtable itself doubles as the
 * agent instance.
 */
#ifndef FARM_AGENTS_PROFIT_OPTIMIZER_H
#define FARM_AGENTS_PROFIT_OPTIMIZER_H

#include "agent.h"

ItemId profit_optimizer_choose_crop(const Agent *self, const FarmState *state,
                                     const ResolvedConfig *config);
bool profit_optimizer_should_buy_upgrade(const Agent *self, const FarmState *state,
                                          UpgradeId upgrade_id);
bool profit_optimizer_should_fertilize(const Agent *self, const FarmState *state,
                                        int planted_index);
void profit_optimizer_choose_contracts(const Agent *self, const FarmState *state,
                                        const ResolvedConfig *config, ContractDecisionBuffer *out);
void profit_optimizer_choose_processing(const Agent *self, const FarmState *state,
                                         const ResolvedConfig *config,
                                         ProcessingDecisionBuffer *out);
void profit_optimizer_choose_sales(const Agent *self, const FarmState *state,
                                    const ResolvedConfig *config, SalesDecisionBuffer *out);
bool profit_optimizer_should_use_fertilizer(const Agent *self, const FarmState *state,
                                             ItemId crop_item_id);

#endif /* FARM_AGENTS_PROFIT_OPTIMIZER_H */
