#include "config.h"

#include <string.h>

/* Linear scan: config is small (a handful of crops/upgrades/buyers/channels
 * in the shipped JSON) and this runs at decision time, not the per-plot
 * hot path simulation/derived.py's id()-keyed caches exist for. See
 * farm-c/README.md. */

const CropDef *config_find_crop(const ResolvedConfig *config, ItemId item_id) {
    for (size_t i = 0; i < config->crop_count; i++) {
        if (config->crops[i].item_id == item_id) {
            return &config->crops[i];
        }
    }
    return NULL;
}

const ItemDef *config_find_item(const ResolvedConfig *config, ItemId item_id) {
    for (size_t i = 0; i < config->item_count; i++) {
        if (config->items[i].id == item_id) {
            return &config->items[i];
        }
    }
    return NULL;
}

const UpgradeDef *config_find_upgrade(const ResolvedConfig *config, UpgradeId upgrade_id) {
    for (size_t i = 0; i < config->upgrade_count; i++) {
        if (config->upgrades[i].id == upgrade_id) {
            return &config->upgrades[i];
        }
    }
    return NULL;
}

const RecipeDef *config_find_recipe(const ResolvedConfig *config, RecipeId recipe_id) {
    for (size_t i = 0; i < config->recipe_count; i++) {
        if (config->recipes[i].id == recipe_id) {
            return &config->recipes[i];
        }
    }
    return NULL;
}

const ChannelDef *config_find_channel(const ResolvedConfig *config, ChannelId channel_id) {
    for (size_t i = 0; i < config->channel_count; i++) {
        if (config->channels[i].channel_id == channel_id) {
            return &config->channels[i];
        }
    }
    return NULL;
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
