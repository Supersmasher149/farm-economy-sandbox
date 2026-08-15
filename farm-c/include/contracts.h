/* Contract offer evaluation. Faithful port of the functions ported agents
 * call from simulation/contracts.py -- `is_offer_profitable`,
 * `is_offer_feasible`, `forecast_committed_supply`, `best_market_alternative`
 * -- and every private helper they transitively need
 * (`_item_capacity`/`_future_crop_arrivals`/`_input_supply`/
 * `_schedule_batches`/`_slot_free_days`/`_inventory_quantity`), all kept in
 * this one file exactly as Python keeps them in one module.
 *
 * SCOPE NOTE: `_future_crop_arrivals`' grade ceiling for already-planted
 * crops (contracts.py:200-213 `_best_possible_grade`, which calls
 * `crop_growth.harvest_multipliers`) needs live weather/soil stress physics
 * this agents-only port doesn't have. This port's stand-in assumes every
 * already-planted crop of the matching item can reach QUALITY_STANDARD --
 * see the `SIMPLIFIED` comment in contracts.c. Everything else in this file
 * is a faithful, unsimplified port. `generate_offers`, `accept`, `deliver`,
 * and `resolve_expired` are out of scope entirely (no ported agent calls
 * them).
 */
#ifndef FARM_CONTRACTS_H
#define FARM_CONTRACTS_H

#include "config.h"
#include "state.h"

/* simulation/contracts.py:129-147 */
double contracts_best_market_alternative(const FarmState *state, const ResolvedConfig *config,
                                          const ContractRecord *contract);

/* simulation/contracts.py:150-152 */
bool contracts_is_offer_profitable(const FarmState *state, const ResolvedConfig *config,
                                    const ContractRecord *contract);

/* simulation/contracts.py:573-590 */
bool contracts_is_offer_feasible(const FarmState *state, const ResolvedConfig *config,
                                  const ContractRecord *contract);

/* simulation/contracts.py:530-570 */
double contracts_forecast_committed_supply(const FarmState *state, const ResolvedConfig *config,
                                            const ContractRecord *contract);

#endif /* FARM_CONTRACTS_H */
