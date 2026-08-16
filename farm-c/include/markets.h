/* Faithful port of simulation/markets.py: `quote`, `best_channel`, and the
 * QUALITY_MULTIPLIERS table (Phase 0, the decision-time reads ported agents
 * call directly), plus Phase 2's mutators `update_daily_prices` and `sell`.
 * `best_market_alternative` lives in contracts.h, matching where Python
 * defines it (simulation/contracts.py:129-147, not markets.py).
 */
#ifndef FARM_MARKETS_H
#define FARM_MARKETS_H

#include "config.h"
#include "rng.h"
#include "state.h"

/* simulation/markets.py:7-12, indexed by Quality. */
extern const double QUALITY_MULTIPLIERS[QUALITY_COUNT];

typedef struct {
    int quantity;
    double unit_price;
    double gross;
    double fee;
    double net;
} MarketQuote;

/* simulation/markets.py:42-84. `capacity_used`, if non-NULL, is a
 * config->channel_count-sized array of already-planned per-channel usage
 * (route_sales_by_best_price's `planned_capacity`, agents/base.py:79) used
 * instead of `state->channel_capacity_used` -- mirrors Python's
 * `capacity_used: dict | None = None` override parameter exactly. Returns
 * false (Python: None) wherever the offer doesn't clear -- unknown item
 * price, non-positive quantity, quality/reputation below the channel's
 * floor, no remaining capacity, or gross <= fee. */
bool markets_quote(const FarmState *state, ItemId item_id, Quality quality,
                    const ChannelDef *channel, int quantity, const int *capacity_used,
                    MarketQuote *out);

/* simulation/markets.py:171-177. Returns NULL if no channel in
 * `channels`/`channel_count` can quote the lot at all. */
const ChannelDef *markets_best_channel(const FarmState *state, ItemId item_id, Quality quality,
                                        const ChannelDef *const *channels, size_t channel_count,
                                        int quantity);

/* simulation/markets.py:15-39. Prices every item in `config->items` (its
 * array order is items_by_id's iteration order, which fixes the sequence of
 * `rng_uniform` draws below -- load-bearing, exactly as
 * derived._build_market_profiles' own docstring states), writes
 * `state->market_prices`/`has_market_price` for every item, decays
 * `state->market_supply`, and resets `state->channel_capacity_used` to all
 * zero. Reads `state->current_season` for the per-item seasonal_demand
 * lookup. Python returns the computed prices dict; no caller in the modern
 * engine path uses that return value (engine.py:129), so this is void. */
void markets_update_daily_prices(FarmState *state, const ResolvedConfig *config, FarmRng *rng);

/* simulation/markets.py:87-168. `has_quality`/`quality` mirrors Python's
 * `quality: str | None = None` (an exact-grade sale, used by
 * Agent.route_sales_by_best_price -- see markets.py:155-163's comment on
 * why executing against the quoted grade matters); `has_min_quality`/
 * `min_quality` mirrors `min_quality: str | None = None`, which the modern
 * engine's own call site (engine.py:163-169) never passes, but the function
 * accepts either way. Returns revenue (0.0 on any rejection) and writes
 * `*out_sold`. */
double markets_sell(FarmState *state, ItemId item_id, int quantity, const ChannelDef *channel,
                     bool has_quality, Quality quality, bool has_min_quality, Quality min_quality,
                     int *out_sold);

#endif /* FARM_MARKETS_H */
