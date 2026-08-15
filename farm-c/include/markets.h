/* Channel quoting. Faithful port of the parts of simulation/markets.py that
 * agents call: `quote`, `best_channel`, and the QUALITY_MULTIPLIERS table.
 * `update_daily_prices` and `sell` are out of scope -- no agent calls them
 * directly (see farm-c/README.md's scope boundary); `best_market_alternative`
 * lives in contracts.h, matching where Python defines it
 * (simulation/contracts.py:129-147, not markets.py).
 */
#ifndef FARM_MARKETS_H
#define FARM_MARKETS_H

#include "config.h"
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

#endif /* FARM_MARKETS_H */
