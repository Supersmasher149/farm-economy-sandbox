#include "markets.h"

const double QUALITY_MULTIPLIERS[QUALITY_COUNT] = {
    [QUALITY_REJECTED] = 0.0,
    [QUALITY_PROCESSING] = 0.65,
    [QUALITY_STANDARD] = 1.0,
    [QUALITY_PREMIUM] = 1.35,
};

static double min2(double a, double b) {
    return a < b ? a : b;
}

bool markets_quote(const FarmState *state, ItemId item_id, Quality quality,
                    const ChannelDef *channel, int quantity, const int *capacity_used,
                    MarketQuote *out) {
    if (!state->has_market_price[item_id] || quantity <= 0) {
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
