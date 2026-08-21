/* Contract offer evaluation and mutation. Faithful port of
 * simulation/contracts.py: the read-only decision helpers ported agents call
 * (`is_offer_profitable`, `is_offer_feasible`, `forecast_committed_supply`,
 * `best_market_alternative`) and every private helper they transitively need
 * (`_item_capacity`/`_future_crop_arrivals`/`_input_supply`/
 * `_schedule_batches`/`_slot_free_days`/`_inventory_quantity`,
 * `_best_possible_grade` -- the last of these now a faithful port using
 * Phase 1's crop_growth.c, not the QUALITY_STANDARD stand-in this port
 * shipped with before crop_growth.c existed), plus the Phase 2 day-loop
 * mutators (`generate_offers`, `accept`, `deliver`, `resolve_expired`,
 * `visible_offers`, `offer_expiry_day`, `is_offer_expired`), all kept in
 * this one file exactly as Python keeps them in one module.
 */
#ifndef FARM_CONTRACTS_H
#define FARM_CONTRACTS_H

#include "config.h"
#include "rng.h"
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

/* --- Phase 2: day-loop mutators. No ported agent calls any of these
 * directly (agents only evaluate offers already on FarmState.contract_offers/
 * active_contracts), which is why they weren't here before. --- */

/* simulation/contracts.py:24-28. `player` is unused in Python beyond
 * reading `contract_config` (which this port already has resolved as
 * `config->contracts`), so this takes `config` only, not `state` --
 * matching the port's existing practice of dropping genuinely-dead
 * parameters (see contracts.c's note on `_item_capacity`'s `seen`). */
int contracts_offer_expiry_day(const ResolvedConfig *config, const ContractRecord *offer);

/* simulation/contracts.py:31-32 */
bool contracts_is_offer_expired(const FarmState *state, const ResolvedConfig *config,
                                 const ContractRecord *offer);

/* simulation/contracts.py:35-42. Writes a filtered copy of `source`'s
 * not-resolved, not-expired offers into `*out` (caller frees via
 * contract_vec_free, even on a false return -- see vec_util.h). `source ==
 * NULL` mirrors Python's `offers if offers is not None else
 * player.contract_offers`. Returns false only on allocation failure. */
bool contracts_visible_offers(const FarmState *state, const ResolvedConfig *config,
                               const ContractVec *source, ContractVec *out);

/* simulation/contracts.py:58-106. Generates at most one new offer per
 * buyer, appended to `state->contract_offers` (from which resolved and
 * expired offers are first dropped, achieving what Python's
 * `player.contract_offers = visible_offers(player)` does, but compacted in
 * place rather than rebuilt) -- a no-op
 * outside the buyer-offer day (`state->day == 0` or not a multiple of
 * `config->contracts.offer_interval_days`). Python's own `player.
 * contract_config = contract_config` bookkeeping line has no equivalent
 * here: every downstream helper already reads `config->contracts`/
 * `config->recipes` directly rather than through a player-stashed
 * reference. The RNG draw order (one `rng_choice_index` then one
 * `rng_roll_yield`, per eligible buyer in `config->buyers` order) is
 * load-bearing. */
void contracts_generate_offers(FarmState *state, const ResolvedConfig *config, FarmRng *rng);

/* simulation/contracts.py:109-126. Finds the offer in `state->contract_offers`
 * with a matching, unresolved `id` (a linear scan, not direct indexing --
 * `id` is a stable per-record identifier assigned at generation time, not a
 * live array position; see farm_types.h's ContractId comment and
 * contracts_generate_offers). Removes it from `contract_offers` either way
 * (matching Python's `.remove(contract)` in both branches); only moves it
 * into `state->active_contracts` (marked accepted) when not expired. */
bool contracts_accept(FarmState *state, const ResolvedConfig *config, ContractId contract_id);

/* simulation/contracts.py:593-623. `requested = min(quantity,
 * contract_remaining(contract))`; consumes via `inventory_consume` (FEFO).
 * Returns revenue (0.0 on any rejection) and writes `*out_delivered`. */
double contracts_deliver(FarmState *state, const ResolvedConfig *config, ContractId contract_id,
                          int quantity, int *out_delivered);

/* simulation/contracts.py:626-663. Penalizes every active contract past its
 * deadline, drops resolved ones from `state->active_contracts`, and drops
 * resolved/expired offers from `state->contract_offers` -- both compacted
 * in place. */
void contracts_resolve_expired(FarmState *state, const ResolvedConfig *config);

#endif /* FARM_CONTRACTS_H */
