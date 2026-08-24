#include "markets.h"

#include <stdlib.h>

#include "pyfloat.h"

const double QUALITY_MULTIPLIERS[QUALITY_COUNT] = {
    [QUALITY_REJECTED] = 0.0,
    [QUALITY_PROCESSING] = 0.65,
    [QUALITY_STANDARD] = 1.0,
    [QUALITY_PREMIUM] = 1.35,
};

#define min2 py_min
#define max2 py_max

bool markets_quote(const FarmState *state, ItemId item_id, Quality quality,
                    const ChannelDef *channel, int quantity, const int *capacity_used,
                    MarketQuote *out) {
    if (state == NULL || state->config == NULL || channel == NULL || out == NULL ||
        item_id >= state->config->item_count || quality < 0 || quality >= QUALITY_COUNT ||
        channel->channel_id >= state->config->channel_count ||
        !state->has_market_price[item_id] || quantity <= 0) {
        return false;
    }
    if (quality < channel->min_quality_rank) {
        return false;
    }
    if (state->reputation < channel->min_reputation) {
        return false;
    }

    int used = capacity_used != NULL ? capacity_used[channel->channel_id]
                                      : state->channel_capacity_used[channel->channel_id];
    int capacity = channel->has_daily_capacity ? channel->daily_capacity : quantity;
    int room = capacity - used;
    if (room < 0) {
        room = 0;
    }
    int accepted = quantity < room ? quantity : room;
    if (accepted <= 0) {
        return false;
    }

    double unit_price = state->market_prices[item_id] * channel->price_multiplier *
                         QUALITY_MULTIPLIERS[quality] *
                         (1.0 + min2(0.25, state->reputation * channel->reputation_bonus));
    double gross = unit_price * accepted;
    double fee = channel->flat_fee + gross * channel->fee_rate;
    if (gross <= fee) {
        return false;
    }

    out->quantity = accepted;
    out->unit_price = unit_price;
    out->gross = gross;
    out->fee = fee;
    out->net = gross - fee;
    return true;
}

const ChannelDef *markets_best_channel(const FarmState *state, ItemId item_id, Quality quality,
                                        const ChannelDef *const *channels, size_t channel_count,
                                        int quantity) {
    const ChannelDef *best = NULL;
    double best_score = 0.0;
    for (size_t i = 0; i < channel_count; i++) {
        MarketQuote offer;
        if (!markets_quote(state, item_id, quality, channels[i], quantity, NULL, &offer)) {
            continue;
        }
        double score = offer.net / offer.quantity;
        if (best == NULL || score > best_score) {
            best = channels[i];
            best_score = score;
        }
    }
    return best;
}

/* --- simulation/markets.py:15-39 update_daily_prices --- */

void markets_update_daily_prices(FarmState *state, const ResolvedConfig *config, FarmRng *rng) {
    double minimum_supply = config->markets.minimum_supply_multiplier;
    double supply_decay = config->markets.supply_decay;

    /* Draw order fixed: one rng_uniform per item, in config->items' array
     * order -- the same order items_by_id iterates in Python, which is what
     * makes the draw sequence (and therefore every recorded seed) agree. */
    for (size_t i = 0; i < config->item_count; i++) {
        const ItemDef *item = &config->items[i];
        double seasonal = item->seasonal_demand[state->current_season];
        double supply = state->market_supply[item->id];
        double saturation = max2(minimum_supply, 1.0 - supply * 0.01);
        double price = max2(0.01, item->base_price * seasonal * saturation *
                                       rng_uniform(rng, 1 - item->price_variation,
                                                   1 + item->price_variation));
        state->market_prices[item->id] = price;
        state->has_market_price[item->id] = true;
        state->market_supply[item->id] = supply * supply_decay;
    }
    for (size_t i = 0; i < config->channel_count; i++) {
        state->channel_capacity_used[config->channels[i].channel_id] = 0;
    }
}

/* --- simulation/markets.py:87-168 sell --- */

typedef struct {
    size_t lot_index;
    int remaining_shelf_life;
    Quality quality;
} SellCandidate;

static int cmp_sell_candidate(const void *a, const void *b) {
    const SellCandidate *ca = a;
    const SellCandidate *cb = b;
    /* Sort key (remaining_shelf_life, -QUALITY_ORDER[quality]) ascending:
     * soonest-to-expire first, and on a shelf-life tie the *higher*-quality
     * lot goes first -- the opposite tie-break from inventory.c's consume()
     * (see that module's docstring on why the two rules differ). */
    if (ca->remaining_shelf_life != cb->remaining_shelf_life) {
        return ca->remaining_shelf_life < cb->remaining_shelf_life ? -1 : 1;
    }
    if (ca->quality != cb->quality) {
        return ca->quality > cb->quality ? -1 : 1;
    }
    return (ca->lot_index > cb->lot_index) - (ca->lot_index < cb->lot_index);
}

/* Mirrors Python's `planned` list: (lot_index, take) pairs, applied only
 * once the sale is confirmed profitable below -- a rejected sale (gross
 * <= fee) must leave every lot untouched. One struct instead of two
 * parallel arrays -- same tuples, same order, same values, half the
 * scratch buffers to manage. */
typedef struct {
    size_t lot_index;
    int take;
} PlannedSale;

double markets_sell(FarmState *state, ItemId item_id, int quantity, const ChannelDef *channel,
                     bool has_quality, Quality quality, bool has_min_quality, Quality min_quality,
    int *out_sold) {
    if (out_sold == NULL) return 0.0;
    *out_sold = 0;

    if (state == NULL || state->config == NULL || channel == NULL ||
        item_id >= state->config->item_count ||
        channel->channel_id >= state->config->channel_count || quantity <= 0 ||
        (has_quality && (quality < 0 || quality >= QUALITY_COUNT)) ||
        (has_min_quality && (min_quality < 0 || min_quality >= QUALITY_COUNT)) ||
        !state->has_market_price[item_id] ||
        state->reputation < channel->min_reputation ||
        (has_quality && quality < channel->min_quality_rank)) {
        return 0.0;
    }
    Quality minimum = has_min_quality ? min_quality : channel->min_quality_rank;

    int used = state->channel_capacity_used[channel->channel_id];
    int room = (channel->has_daily_capacity ? channel->daily_capacity : quantity) - used;
    if (room < 0) {
        room = 0;
    }
    quantity = quantity < room ? quantity : room;

    size_t lot_count = state->inventory_lots.count;
    if (lot_count > SIZE_MAX / sizeof(SellCandidate)) {
        farm_state_mark_allocation_failed(state);
        return 0.0;
    }
    SellCandidate *candidates =
        scratch_buffer_reserve(&state->scratch_sell_candidates, lot_count * sizeof(SellCandidate));
    if (lot_count > 0 && candidates == NULL) {
        farm_state_mark_allocation_failed(state);
        return 0.0;
    }
    size_t candidate_count = 0;
    for (size_t i = 0; i < lot_count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        if (lot->item_id != item_id) {
            continue;
        }
        bool eligible = has_quality ? (lot->quality == quality) : (lot->quality >= minimum);
        if (!eligible) {
            continue;
        }
        candidates[candidate_count++] = (SellCandidate){
            .lot_index = i,
            .remaining_shelf_life = inventory_lot_remaining_shelf_life(lot),
            .quality = lot->quality,
        };
    }
    qsort(candidates, candidate_count, sizeof(SellCandidate), cmp_sell_candidate);

    double reputation_multiplier = 1.0 + min2(0.25, state->reputation * channel->reputation_bonus);
    int sold = 0;
    double gross = 0.0;
    if (candidate_count > SIZE_MAX / sizeof(PlannedSale)) {
        farm_state_mark_allocation_failed(state);
        return 0.0;
    }
    PlannedSale *planned =
        scratch_buffer_reserve(&state->scratch_sell_planned, candidate_count * sizeof(PlannedSale));
    if (candidate_count > 0 && planned == NULL) {
        farm_state_mark_allocation_failed(state);
        return 0.0;
    }
    size_t planned_count = 0;
    bool first_is_product = false;

    for (size_t i = 0; i < candidate_count && sold < quantity; i++) {
        InventoryLot *lot = &state->inventory_lots.data[candidates[i].lot_index];
        int take = lot->quantity < quantity - sold ? lot->quantity : quantity - sold;
        double unit_price = state->market_prices[item_id] * channel->price_multiplier *
                             QUALITY_MULTIPLIERS[lot->quality] * reputation_multiplier;
        planned[planned_count] = (PlannedSale){.lot_index = candidates[i].lot_index, .take = take};
        planned_count++;
        if (planned_count == 1) {
            first_is_product = lot->item_type == ITEM_PRODUCT;
        }
        sold += take;
        gross += unit_price * take;
    }

    double revenue = 0.0;
    if (sold) {
        double fee = channel->flat_fee + gross * channel->fee_rate;
        if (gross > fee) {
            revenue = gross - fee;
            for (size_t i = 0; i < planned_count; i++) {
                state->inventory_lots.data[planned[i].lot_index].quantity -= planned[i].take;
            }
            /* `[lot for lot in inventory_lots if lot.quantity > 0]` */
            size_t write = 0;
            for (size_t read = 0; read < state->inventory_lots.count; read++) {
                InventoryLot lot = state->inventory_lots.data[read];
                if (lot.quantity > 0) {
                    state->inventory_lots.data[write++] = lot;
                }
            }
            state->inventory_lots.count = write;

            state->channel_capacity_used[channel->channel_id] = used + sold;
            state->money += revenue;
            farm_state_track_peak_cash(state);
            state->total_revenue += revenue;
            state->total_sold += sold;
            state->revenue_by_channel[channel->channel_id] += revenue;
            if (first_is_product) {
                state->processing_revenue += revenue;
            }
            state->market_supply[item_id] += sold;
            *out_sold = sold;
        } else {
            sold = 0;
        }
    }
    return sold ? revenue : 0.0;
}
