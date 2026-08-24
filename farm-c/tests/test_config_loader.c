#include <stdio.h>
#include <string.h>

#include "config.h"

int main(void) {
    ResolvedConfig config;
    ConfigError error;
    if (!config_load_directory("../config", &config, &error)) {
        fprintf(stderr, "config load failed: %s\n", error.message);
        return 1;
    }
    if (config.crop_count != 3 || config.item_count != 5 ||
        strcmp(config.items[0].name, "Quickweed") != 0 ||
        config.crops[0].role != CROP_ROLE_FAST ||
        config.crops[2].unlock_requirement.type != UNLOCK_REVENUE ||
        config.crops[2].unlock_requirement.revenue_threshold != 150.0 ||
        config.plot_regen.nitrogen != 0.07 ||
        config.soil_dynamics.same_family_yield_penalty != 0.72 ||
        config.contracts.offer_expiry_days != 3 ||
        config.buyers[0].allowed_item_count != 2 ||
        config.channels[2].flat_fee != 1.0 ||
        config.channels[1].fee_rate != 0.0) {
        fprintf(stderr, "unexpected resolved crop catalog\n");
        config_destroy(&config);
        return 1;
    }
    config_destroy(&config);
    SimulationSettings settings;
    if (!config_load_simulation_settings("../config", &settings, &error) ||
        settings.days != 365 || settings.start_slots != 3 || settings.has_seed) {
        fprintf(stderr, "simulation settings load failed: %s\n", error.message);
        return 1;
    }
    memset(&config, 0xA5, sizeof(config));
    if (config_load_directory("does-not-exist", &config, &error)) {
        fprintf(stderr, "missing directory unexpectedly loaded\n");
        return 1;
    }
    config_destroy(&config);
    puts("config loader: ok");
    return 0;
}
