/* Pure economic calculations shared by agents. Faithful, full-fidelity port
 * of simulation/economy_rules.py (all 18 functions) -- see
 * farm-c/README.md's scope boundary. Free of any FarmState mutation, same
 * as the Python module's own header comment promises, so agents can call
 * these to evaluate options without side effects.
 */
#ifndef FARM_ECONOMY_H
#define FARM_ECONOMY_H

#include "config.h"
#include "state.h"

/* economy_rules.py:10-18 */
bool economy_is_crop_unlocked(const CropDef *crop, const FarmState *state);

/* economy_rules.py:21-23 */
int economy_effective_growth_days(const CropDef *crop, const FarmState *state,
                                   const ResolvedConfig *config);

/* economy_rules.py:26-41. Returns false for an open-ended run
 * (state->has_total_days == false), leaving *out_day untouched -- callers
 * must treat that as "no bound", never as day -1. */
bool economy_last_executable_day(const FarmState *state, int *out_day);

/* economy_rules.py:44-52 */
int economy_effective_deadline(const FarmState *state, int deadline);

/* economy_rules.py:55-64 */
bool economy_matures_within_run(int growth_days, const FarmState *state);

/* economy_rules.py:67-79. Assumes reliable watering (no neglect) and prices
 * no soil-quality risk -- see quality_adjusted_profit_per_day below. */
double economy_expected_profit_per_day(const CropDef *crop, const FarmState *state,
                                        const ResolvedConfig *config);

/* economy_rules.py:82-87 */
double economy_operating_reserve(const FarmState *state);

/* economy_rules.py:90-91 */
bool economy_can_spend_with_reserve(const FarmState *state, double amount);

/* economy_rules.py:94-127. `config` is only used to look up the crop's own
 * base_price via its unified item entry (config_find_item) -- crop["base_price"]
 * in Python is the same dict as items_by_id[crop_id] (derived.py:253-254),
 * so this port reads it off ItemDef rather than duplicating the field onto
 * CropDef. */
double economy_fertilizer_expected_marginal_profit(const CropDef *crop,
                                                     const FertilizerConfig *fertilizer,
                                                     const ResolvedConfig *config);

/* economy_rules.py:130-143 */
double economy_fertilizer_safety_value(const CropDef *crop, const FertilizerConfig *fertilizer,
                                        const ResolvedConfig *config);

/* economy_rules.py:156-166. 1.0 for a player with no plots (e.g. a bare
 * FarmState built directly by a fixture, matching the Python doc-comment's
 * "or a player with no simulated plots" case). */
double economy_soil_health_factor(const FarmState *state);

/* economy_rules.py:169-199 */
double economy_soil_quality_risk(const CropDef *crop, const FarmState *state);

/* economy_rules.py:202-217 */
double economy_quality_adjusted_profit_per_day(const CropDef *crop, const FarmState *state,
                                                const ResolvedConfig *config);

/* economy_rules.py:220-228 */
bool economy_crop_seed_reserve_gate(const CropDef *crop, const FarmState *state,
                                     double reserve_fraction);

/* economy_rules.py:241-255. `candidates`/`candidate_count` is a caller-owned
 * array of CropDef pointers (not full CropDefs -- callers build this by
 * filtering config->crops). Returns NULL for an empty candidate list,
 * matching Python's `None`. */
const CropDef *economy_best_crop_by_expected_profit(const CropDef *const *candidates,
                                                      size_t candidate_count,
                                                      const FarmState *state,
                                                      const ResolvedConfig *config);

/* economy_rules.py:258-274. `reserve_fractions`/`fraction_count` mirrors the
 * Python default `(1.0, 0.5)` -- callers pass their own, e.g.
 * ProfitOptimizer's `(1.0, 0.5, 0.25)` (agents/profit_optimizer.py:75). */
const CropDef *economy_choose_crop_with_relaxed_reserve(const CropDef *const *candidates,
                                                          size_t candidate_count,
                                                          const FarmState *state,
                                                          const ResolvedConfig *config,
                                                          const double *reserve_fractions,
                                                          size_t fraction_count);

/* economy_rules.py:277-338. Returns false (and leaves *out_days untouched)
 * for every case Python returns None -- callers must treat that as
 * "unknown, skip this check", never as "free to buy" (same contract as the
 * Python docstring states explicitly). */
bool economy_upgrade_payback_days(const UpgradeDef *upgrade, const FarmState *state,
                                   const ResolvedConfig *config, double *out_days);

/* economy_rules.py:341-394. Defaults match the Python signature's:
 * cooldown_days=6, min_payback_multiple=2.0,
 * max_cumulative_spend_fraction=0.6, default_payback_horizon_days=60 --
 * exposed as named constants below rather than defaulted C parameters (C
 * has no default arguments), so every call site states them explicitly. */
#define ECONOMY_UPGRADE_COOLDOWN_DAYS_DEFAULT 6
#define ECONOMY_UPGRADE_MIN_PAYBACK_MULTIPLE_DEFAULT 2.0
#define ECONOMY_UPGRADE_MAX_CUMULATIVE_SPEND_FRACTION_DEFAULT 0.6
#define ECONOMY_UPGRADE_DEFAULT_PAYBACK_HORIZON_DAYS_DEFAULT 60

bool economy_should_buy_upgrade_within_budget(const FarmState *state, const UpgradeDef *upgrade,
                                               const ResolvedConfig *config, int cooldown_days,
                                               double min_payback_multiple,
                                               double max_cumulative_spend_fraction,
                                               int default_payback_horizon_days);

#endif /* FARM_ECONOMY_H */
