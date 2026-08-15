/* Faithful port of agents/random_agent.py. Every draw goes through
 * rng_hash's decision_random (PlayerState.decision_random), never the event
 * RNG -- see rng_hash.h.
 */
#include "agent.h"

#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "economy.h"
#include "rng_hash.h"

/* agents/random_agent.py:12 watering_diligence = 0.5 (should_water itself
 * is not overridden -- inherits agent_base_should_water, same as Python
 * inheriting agents/base.py's schedule check unchanged). */

typedef struct {
    const char *external_id;
    const CropDef *crop;
} NamedCrop;

static int cmp_named_crop(const void *a, const void *b) {
    const NamedCrop *na = a;
    const NamedCrop *nb = b;
    return strcmp(na->external_id, nb->external_id);
}

/* agents/random_agent.py:14-32. Python sorts candidates by their *string*
 * id (`c["id"]`), not internal numeric order -- this looks up each
 * candidate's external_id (via its unified item entry) to sort on the same
 * key. */
static ItemId random_agent_choose_crop(const Agent *self, const FarmState *state,
                                        const ResolvedConfig *config) {
    (void)self;
    if (config->crop_count == 0) {
        return INVALID_ID;
    }
    NamedCrop *candidates = malloc(config->crop_count * sizeof(NamedCrop));
    size_t count = 0;
    for (size_t i = 0; i < config->crop_count; i++) {
        const CropDef *crop = &config->crops[i];
        if (economy_is_crop_unlocked(crop, state) && state->money >= crop->seed_cost) {
            const ItemDef *item = config_find_item(config, crop->item_id);
            candidates[count++] =
                (NamedCrop){.external_id = item != NULL ? item->external_id : "", .crop = crop};
        }
    }
    if (count == 0) {
        free(candidates);
        return INVALID_ID;
    }
    qsort(candidates, count, sizeof(NamedCrop), cmp_named_crop);

    const char **ids = malloc(count * sizeof(char *));
    for (size_t i = 0; i < count; i++) {
        ids[i] = candidates[i].external_id;
    }

    ReprValue ctx[4] = {
        repr_str("choose_crop"),
        repr_int((long)state->planted.count),
        repr_int((long)state->total_planted),
        repr_str_tuple(ids, count),
    };
    double r = rng_decision_random(state->has_run_seed, state->run_seed, state->day, ctx, 4);
    long index = (long)(r * (double)count);
    if (index >= (long)count) {
        index = (long)count - 1; /* Python: min(index, len(candidates) - 1) */
    }
    ItemId result = candidates[index].crop->item_id;
    free(ids);
    free(candidates);
    return result;
}

/* agents/random_agent.py:34-35 */
static bool random_agent_should_buy_upgrade(const Agent *self, const FarmState *state,
                                             UpgradeId upgrade_id) {
    (void)self;
    const UpgradeDef *upgrade = config_find_upgrade(state->config, upgrade_id);
    ReprValue ctx[2] = {repr_str("upgrade"), repr_str(upgrade->external_id)};
    return rng_decision_random(state->has_run_seed, state->run_seed, state->day, ctx, 2) < 0.5;
}

/* agents/random_agent.py:37-38 */
static bool random_agent_should_use_fertilizer(const Agent *self, const FarmState *state,
                                                ItemId crop_item_id) {
    (void)self;
    const ItemDef *item = config_find_item(state->config, crop_item_id);
    ReprValue ctx[2] = {repr_str("fertilize_at_plant"), repr_str(item->external_id)};
    return rng_decision_random(state->has_run_seed, state->run_seed, state->day, ctx, 2) < 0.5;
}

/* agents/random_agent.py:40-48 */
static bool random_agent_should_fertilize(const Agent *self, const FarmState *state,
                                           int planted_index) {
    (void)self;
    const PlantedCrop *planted = &state->planted.data[planted_index];
    const ItemDef *item = config_find_item(state->config, planted->crop_item_id);
    ReprValue ctx[3] = {repr_str("fertilize_mid_grow"), repr_int((long)planted->day_planted),
                         repr_str(item->external_id)};
    return rng_decision_random(state->has_run_seed, state->run_seed, state->day, ctx, 3) < 0.5;
}

const Agent AGENT_RANDOM_AGENT = {
    .name = "random_agent",
    .description = "Baseline: makes every decision (crop, upgrade, fertilizer) by an unweighted "
                    "random draw.",
    .watering_diligence = 0.5,
    .choose_crop = random_agent_choose_crop,
    .should_buy_upgrade = random_agent_should_buy_upgrade,
    .should_water = agent_base_should_water,
    .should_fertilize = random_agent_should_fertilize,
    .choose_contracts = agent_base_choose_contracts,
    .choose_contract_deliveries = agent_base_choose_contract_deliveries,
    .choose_processing = agent_base_choose_processing,
    .choose_sales = agent_base_choose_sales,
    .should_use_fertilizer = random_agent_should_use_fertilizer,
};
