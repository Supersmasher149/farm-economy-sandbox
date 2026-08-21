/* Faithful port of agents/base.py's concrete (non-abstract) methods --
 * every agent's vtable points at these directly for whichever decisions it
 * doesn't override, exactly as a Python subclass inherits them unchanged.
 */
#include "agent.h"

#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "economy.h"
#include "markets.h"
#include "vec_util.h"

/* agents/base.py:31-32 */
bool agent_base_should_water(const Agent *self, const FarmState *state, int planted_index) {
    (void)self;
    const PlantedCrop *planted = &state->planted.data[planted_index];
    const CropDef *crop = config_find_crop(state->config, planted->crop_item_id);
    int water_interval_days = crop != NULL ? crop->water_interval_days : 3; /* crop.get(..., 3) */
    return (state->day - planted->last_watered_day) >= water_interval_days;
}

/* agents/base.py:34-35 */
bool agent_base_should_fertilize(const Agent *self, const FarmState *state, int planted_index) {
    (void)self;
    (void)state;
    (void)planted_index;
    return false;
}

/* agents/base.py:37-38 */
void agent_base_choose_contracts(const Agent *self, const FarmState *state,
                                  const ResolvedConfig *config, ContractDecisionBuffer *out) {
    (void)self;
    (void)state;
    (void)config;
    (void)out; /* leave empty -- nothing accepted */
}

/* agents/base.py:40-45 */
void agent_base_choose_contract_deliveries(const Agent *self, const FarmState *state,
                                            DeliveryDecisionBuffer *out) {
    (void)self;
    for (size_t i = 0; i < state->active_contracts.count; i++) {
        const ContractRecord *contract = &state->active_contracts.data[i];
        if (!contract->resolved) {
            delivery_decision_push(
                out, (DeliveryDecision){.contract_id = contract->id,
                                         .quantity = contract_remaining(contract)});
        }
    }
}

/* agents/base.py:47-48 */
void agent_base_choose_processing(const Agent *self, const FarmState *state,
                                   const ResolvedConfig *config, ProcessingDecisionBuffer *out) {
    (void)self;
    (void)state;
    (void)config;
    (void)out; /* leave empty -- nothing scheduled */
}

/* agents/base.py:50-54 -- one decision per lot, not aggregated, no quality
 * field (SALE_QUALITY_ANY: see agent.h). */
void agent_base_choose_sales(const Agent *self, const FarmState *state,
                              const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self;
    ChannelId spot = config->spot_channel_id;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        sale_decision_push(out, (SaleDecision){.item_id = lot->item_id,
                                                .channel_id = spot,
                                                .quality = SALE_QUALITY_ANY,
                                                .quantity = lot->quantity});
    }
}

/* agents/base.py:56-61 */
bool agent_base_should_use_fertilizer(const Agent *self, const FarmState *state,
                                       ItemId crop_item_id) {
    (void)self;
    (void)state;
    (void)crop_item_id;
    return false;
}

/* --- agents/base.py:63-126 route_sales_by_best_price --- */

typedef struct {
    ItemId item_id;
    int by_quality[QUALITY_COUNT];
} ItemQuantities;

typedef struct {
    ItemQuantities *data;
    size_t count;
    size_t capacity;
} ItemQuantitiesVec;

static ItemQuantities *find_or_create(ItemQuantitiesVec *items, ItemId item_id) {
    for (size_t i = 0; i < items->count; i++) {
        if (items->data[i].item_id == item_id) {
            return &items->data[i];
        }
    }
    vec_grow((void **)&items->data, &items->capacity, items->count, sizeof(ItemQuantities));
    ItemQuantities *entry = &items->data[items->count++];
    entry->item_id = item_id;
    memset(entry->by_quality, 0, sizeof(entry->by_quality));
    return entry;
}

typedef struct {
    ItemId item_id;
    Quality quality;
    ChannelId channel_id;
    int quantity;
} RouteAccum;

typedef struct {
    RouteAccum *data;
    size_t count;
    size_t capacity;
} RouteAccumVec;

static void route_accum_add(RouteAccumVec *routes, ItemId item_id, Quality quality,
                             ChannelId channel_id, int quantity) {
    for (size_t i = 0; i < routes->count; i++) {
        RouteAccum *entry = &routes->data[i];
        if (entry->item_id == item_id && entry->quality == quality &&
            entry->channel_id == channel_id) {
            entry->quantity += quantity;
            return;
        }
    }
    vec_grow((void **)&routes->data, &routes->capacity, routes->count, sizeof(RouteAccum));
    routes->data[routes->count++] =
        (RouteAccum){.item_id = item_id, .quality = quality, .channel_id = channel_id,
                      .quantity = quantity};
}

void agent_route_sales_by_best_price(const FarmState *state, const ResolvedConfig *config,
                                      SalesDecisionBuffer *out) {
    int *planned_capacity = malloc(config->channel_count * sizeof(int));
    memcpy(planned_capacity, state->channel_capacity_used, config->channel_count * sizeof(int));

    /* Group inventory by item_id (first-seen order, matching Python dict
     * insertion order) x quality. */
    ItemQuantitiesVec items = {0};
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        ItemQuantities *entry = find_or_create(&items, lot->item_id);
        entry->by_quality[lot->quality] += lot->quantity;
    }

    RouteAccumVec routes = {0};

    for (size_t ii = 0; ii < items.count; ii++) {
        ItemId item_id = items.data[ii].item_id;
        /* Highest quality (by QUALITY_MULTIPLIERS) first -- for this
         * project's fixed table that's simply descending Quality rank, see
         * agent.h's Quality enum ordering. */
        for (int q = QUALITY_PREMIUM; q >= QUALITY_REJECTED; q--) {
            int remaining = items.data[ii].by_quality[q];
            while (remaining > 0) {
                const ChannelDef *best_channel = NULL;
                MarketQuote best_quote = {0};
                double best_score = 0.0;
                for (size_t c = 0; c < config->channel_count; c++) {
                    MarketQuote quote;
                    if (!markets_quote(state, item_id, (Quality)q, &config->channels[c],
                                        remaining, planned_capacity, &quote)) {
                        continue;
                    }
                    double score = quote.net / quote.quantity;
                    if (best_channel == NULL || score > best_score) {
                        best_channel = &config->channels[c];
                        best_quote = quote;
                        best_score = score;
                    }
                }
                if (best_channel == NULL) {
                    break;
                }
                int sold = best_quote.quantity;
                route_accum_add(&routes, item_id, (Quality)q, best_channel->channel_id, sold);
                planned_capacity[best_channel->channel_id] += sold;
                remaining -= sold;
            }
        }
    }

    for (size_t i = 0; i < routes.count; i++) {
        const RouteAccum *route = &routes.data[i];
        sale_decision_push(out, (SaleDecision){.item_id = route->item_id,
                                                .channel_id = route->channel_id,
                                                .quality = route->quality,
                                                .quantity = route->quantity});
    }

    free(planned_capacity);
    free(items.data);
    free(routes.data);
}
