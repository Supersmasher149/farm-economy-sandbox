/* Config-derived lookups. Faithful port of the two functions ported agents
 * actually call from simulation/derived.py: effective_growth_days and
 * nutrient_demand_total. The Python versions memoize on config object
 * identity (derived.py:442-491) purely because they sit on a per-plot,
 * per-simulated-day hot path re-reading a mutable-dict config; a static C
 * ResolvedConfig needs no such cache -- see farm-c/README.md.
 */
#ifndef FARM_DERIVED_H
#define FARM_DERIVED_H

#include "config.h"

/* simulation/derived.py:445-478. Folds every owned upgrade's
 * growth_time_reduction effect, in ResolvedConfig.upgrades' own order (not
 * upgrades_owned's -- unordered by construction), each rounding to a whole
 * day and flooring at 1, exactly matching Python's fold order requirement
 * (derived.py:466-467's comment: reproducible rounding needs a fixed fold
 * order). Takes an owned-upgrades bool array (config->upgrade_count-sized,
 * indexed by UpgradeId) rather than a FarmState, matching
 * economy_rules.effective_growth_days's delegation to
 * `derived.effective_growth_days(crop, player.upgrades_owned,
 * upgrades_by_id)` -- economy_rules.upgrade_payback_days needs to price a
 * *hypothetical* extra owned upgrade, which this signature lets it do by
 * passing a scratch copy of the array rather than mutating a FarmState. */
int derived_effective_growth_days(const CropDef *crop, const bool *upgrades_owned,
                                   const ResolvedConfig *config);

/* simulation/derived.py:417-425: sum of a crop's resolved nitrogen +
 * phosphorus + potassium demand (DEFAULT_NUTRIENT_DEMAND already folded in
 * at config-load time in this port, unlike Python's lazy per-call default). */
double derived_nutrient_demand_total(const CropDef *crop);

#endif /* FARM_DERIVED_H */
