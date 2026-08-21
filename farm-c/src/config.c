#include "config.h"

#include <string.h>

/* Direct indexing, not a scan: config_loader.c assigns every id as its
 * array position at load time (crops[i].item_id=i, items[i].id=i with
 * products appended after crops, upgrades[i].id=i, recipes[r].id=r,
 * channels[i].channel_id=i) and never reorders or reallocates these arrays
 * afterward, so "the id" and "the array index" are the same number for the
 * lifetime of a ResolvedConfig. A bounds check reproduces the linear scan's
 * NULL-on-miss behavior exactly (an id past the end was never a match
 * either way) while returning the identical pointer for every hit. */

const CropDef *config_find_crop(const ResolvedConfig *config, ItemId item_id) {
    return item_id < config->crop_count ? &config->crops[item_id] : NULL;
}

const ItemDef *config_find_item(const ResolvedConfig *config, ItemId item_id) {
    return item_id < config->item_count ? &config->items[item_id] : NULL;
}

const UpgradeDef *config_find_upgrade(const ResolvedConfig *config, UpgradeId upgrade_id) {
    return upgrade_id < config->upgrade_count ? &config->upgrades[upgrade_id] : NULL;
}

const RecipeDef *config_find_recipe(const ResolvedConfig *config, RecipeId recipe_id) {
    return recipe_id < config->recipe_count ? &config->recipes[recipe_id] : NULL;
}

const ChannelDef *config_find_channel(const ResolvedConfig *config, ChannelId channel_id) {
    return channel_id < config->channel_count ? &config->channels[channel_id] : NULL;
}

ChannelId config_channel_id_by_external_id(const ResolvedConfig *config, const char *external_id) {
    for (size_t i = 0; i < config->channel_count; i++) {
        if (config->channels[i].external_id != NULL &&
            strcmp(config->channels[i].external_id, external_id) == 0) {
            return config->channels[i].channel_id;
        }
    }
    return INVALID_ID;
}
