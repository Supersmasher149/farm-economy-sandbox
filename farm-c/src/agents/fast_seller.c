/* Faithful port of agents/fast_seller.py. */
#include "agent.h"

#include <stdlib.h>

#include "config.h"
#include "economy.h"
#include "vec_util.h"

/* agents/fast_seller.py:11-12 */
#define CHEAP_UPGRADE_COST_CEILING 150.0
#define AFFORDABILITY_BUFFER 1.2

/* agents/fast_seller.py:21-29 */
static ItemId fast_seller_choose_crop(const Agent *self, const FarmState *state,
                                       const ResolvedConfig *config) {
    (void)self;
    const CropDef *best = NULL;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state) && state->money >= crop->seed_cost) {
            if (best == NULL || crop->growth_days < best->growth_days) {
                best = crop;
            }
        }
    }
    return best != NULL ? best->item_id : INVALID_ID;
}

/* agents/fast_seller.py:31-34 */
static bool fast_seller_should_buy_upgrade(const Agent *self, const FarmState *state,
                                            UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    if (upgrade->cost > CHEAP_UPGRADE_COST_CEILING) {
        return false;
    }
    return state->money >= upgrade->cost * AFFORDABILITY_BUFFER;
}

typedef struct {
    ItemId item_id;
    int quantity;
} ItemQty;

/* agents/fast_seller.py:36-43 -- aggregated by item_id (unlike base.py's
 * per-lot default), still dumped at "spot". */
static void fast_seller_choose_sales(const Agent *self, const FarmState *state,
                                      const ResolvedConfig *config, SalesDecisionBuffer *out) {
    (void)self;
    ChannelId spot = config->spot_channel_id;

    ItemQty *items = NULL;
    size_t count = 0, capacity = 0;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        ItemQty *entry = NULL;
        for (size_t j = 0; j < count; j++) {
            if (items[j].item_id == lot->item_id) {
                entry = &items[j];
                break;
            }
        }
        if (entry == NULL) {
            vec_grow((void **)&items, &capacity, count, sizeof(ItemQty));
            entry = &items[count++];
            entry->item_id = lot->item_id;
            entry->quantity = 0;
        }
        entry->quantity += lot->quantity;
    }
    for (size_t i = 0; i < count; i++) {
        sale_decision_push(out, (SaleDecision){.item_id = items[i].item_id,
                                                .channel_id = spot,
                                                .quality = SALE_QUALITY_ANY,
                                                .quantity = items[i].quantity});
    }
    free(items);
}

const Agent AGENT_FAST_SELLER = {
    .name = "fast_seller",
    .description =
        "Always plants the shortest-growth crop; waters reliably; avoids fertilizer spend.",
    .watering_diligence = 1.0,
    .choose_crop = fast_seller_choose_crop,
    .should_buy_upgrade = fast_seller_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = agent_base_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = fast_seller_choose_sales,
    .should_use_fertilizer = agent_base_should_use_fertilizer,
};
