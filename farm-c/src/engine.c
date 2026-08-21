#include "engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "actions.h"
#include "contracts.h"
#include "economy.h"
#include "inventory.h"
#include "markets.h"
#include "processing.h"
#include "rng_hash.h"
#include "weather.h"

static void set_error(EngineError *error, EngineErrorCode code,
                      const char *message) {
  if (error == NULL)
    return;
  error->code = code;
  snprintf(error->message, sizeof(error->message), "%s", message);
}

/* The four decision-buffer steps (12/13/14/15 below) all share this shape:
 * call the agent, and if the buffer's own growth allocation failed, free it
 * and bail with a matching error message. */
#define ENGINE_CHECK_ALLOC(buf, free_fn, what)                               \
  do {                                                                       \
    if ((buf).allocation_failed) {                                           \
      free_fn(&(buf));                                                       \
      set_error(error, ENGINE_ERROR_ALLOCATION, what " buffer allocation failed"); \
      return false;                                                          \
    }                                                                        \
  } while (0)

#define ENGINE_CHECK_STATE_ALLOC(what)                                        \
  do {                                                                        \
    if (state->allocation_failed || contracts_had_allocation_failure() ||     \
        rng_hash_had_allocation_failure()) {                                  \
      state->allocation_failed = true;                                        \
      set_error(error, ENGINE_ERROR_ALLOCATION, what " allocation failed"); \
      return false;                                                           \
    }                                                                         \
  } while (0)

static StorageConfig effective_storage(const FarmState *state) {
  StorageConfig storage = state->config->storage;
  for (size_t i = 0; i < state->config->upgrade_count; i++) {
    const UpgradeDef *upgrade = &state->config->upgrades[i];
    if (state->upgrades_owned[upgrade->id] &&
        upgrade->effect.type == EFFECT_STORAGE) {
      storage.capacity += upgrade->effect.as.storage.capacity_bonus;
      storage.shelf_life_multiplier *=
          upgrade->effect.as.storage.shelf_life_multiplier;
    }
  }
  return storage;
}

static bool crop_can_be_funded(const FarmState *state, const CropDef *crop) {
  return state->money >= crop->seed_cost ||
         state->seed_inventory[crop->item_id] > 0;
}

static bool plant_open_slots(FarmState *state, const Agent *agent) {
  bool acted = false;
  int open_slots = farm_state_open_slots(state);
  if (open_slots > 0) {
    if ((size_t)open_slots > SIZE_MAX - state->planted.count ||
        !vec_reserve((void **)&state->planted.data, &state->planted.capacity,
                     state->planted.count + (size_t)open_slots,
                     sizeof(*state->planted.data))) {
      farm_state_mark_allocation_failed(state);
      return false;
    }
  }
  /* next_plot only ever advances across iterations, so filling k of n open
   * slots is a single O(n) sweep instead of an O(n*k) rescan-from-0 every
   * time a slot gets planted. */
  size_t next_plot = 0;
  while (farm_state_open_slots(state) > 0) {
    bool free_plot = false;
    for (; next_plot < state->plot_count; next_plot++) {
      if (state->plots[next_plot].planted_index == -1) {
        free_plot = true;
        break;
      }
    }
    if (!free_plot)
      break;
    ItemId crop_id = agent->choose_crop(agent, state, state->config);
    if (contracts_had_allocation_failure() || rng_hash_had_allocation_failure()) {
      farm_state_mark_allocation_failed(state);
      return false;
    }
    if (!id_valid(crop_id))
      break;
    const CropDef *crop = config_find_crop(state->config, crop_id);
    if (crop == NULL || !economy_is_crop_unlocked(crop, state) ||
        !crop_can_be_funded(state, crop))
      break;

    bool fertilized = agent->should_use_fertilizer(agent, state, crop_id);
    if (contracts_had_allocation_failure() || rng_hash_had_allocation_failure()) {
      farm_state_mark_allocation_failed(state);
      return false;
    }
    if (fertilized && state->fertilizer_inventory == 0) {
      if (state->money >= crop->seed_cost + state->config->fertilizer.cost) {
        (void)actions_buy_fertilizer(state, &state->config->fertilizer, 1);
      } else {
        fertilized = false;
      }
    }
    fertilized = fertilized && state->fertilizer_inventory > 0;
    if (state->seed_inventory[crop_id] <= 0 &&
        !actions_buy_seeds(state, crop, 1))
      break;
    int growth_days = economy_effective_growth_days(crop, state, state->config);
    if (!actions_plant_seed(state, crop, growth_days, fertilized,
                            &state->config->fertilizer))
      break;
    acted = true;
  }
  return acted;
}

static bool no_viable_reinvestment(const FarmState *state) {
  /* Cheap O(1) checks first: on the overwhelming majority of days this
   * returns false right here, so the crop-catalog scan below (invariant
   * for the config's lifetime) only runs on the rare days it's needed. */
  if (state->planted.count != 0 || state->processing_jobs.count != 0)
    return false;
  double cheapest = 0.0;
  bool found = false;
  for (size_t i = 0; i < state->config->crop_count; i++) {
    double cost = state->config->crops[i].seed_cost;
    if (!found || cost < cheapest) {
      cheapest = cost;
      found = true;
    }
  }
  if (!found || state->money >= cheapest)
    return false;
  for (size_t i = 0; i < state->inventory_lots.count; i++) {
    if (state->inventory_lots.data[i].quantity > 0)
      return false;
  }
  for (size_t i = 0; i < state->config->crop_count; i++) {
    if (state->seed_inventory[state->config->crops[i].item_id] > 0)
      return false;
  }
  return true;
}

/*
 *
 */
bool engine_run_day_observed(FarmState *state, const Agent *agent, FarmRng *rng,
                             EngineDayCallback callback, void *context,
                             EngineError *error) {
  if (error != NULL)
    *error = (EngineError){0};
  if (state == NULL || state->config == NULL || agent == NULL || rng == NULL ||
      agent->choose_crop == NULL || agent->should_buy_upgrade == NULL ||
      agent->should_water == NULL || agent->should_fertilize == NULL ||
      agent->choose_contracts == NULL ||
      agent->choose_contract_deliveries == NULL ||
      agent->choose_processing == NULL || agent->choose_sales == NULL ||
      agent->should_use_fertilizer == NULL) {
    set_error(error, ENGINE_ERROR_ARGUMENT,
              "state, config, agent, and rng are required");
    return false;
  }
  contracts_clear_allocation_failure();
  rng_hash_clear_allocation_failure();
  if (state->allocation_failed) {
    set_error(error, ENGINE_ERROR_ALLOCATION, "state allocation previously failed");
    return false;
  }
  const ResolvedConfig *config = state->config;
  state->processing_capacity = config->processing_capacity;
  for (size_t i = 0; i < config->upgrade_count; i++) {
    const UpgradeDef *upgrade = &config->upgrades[i];
    if (state->upgrades_owned[upgrade->id] &&
        upgrade->effect.type == EFFECT_PROCESSING_CAPACITY)
      state->processing_capacity += upgrade->effect.as.processing_capacity;
  }
  /* Computed fresh above every day the full engine runs, so it's always
   * trustworthy from here on -- has_processing_capacity only reads as false
   * for a bare FarmState that never went through run_day (e.g. a direct
   * unit-test construction), matching state.h's contract with contracts.c
   * and profit_optimizer.c's choose_processing. Leaving this unset left
   * both permanently reading processing_capacity as 0, so choose_processing
   * returned early every day of every run -- see docs/agent-decision
   * divergence notes. */
  state->has_processing_capacity = true;
  StorageConfig storage = effective_storage(state);
  bool acted = false;

  /* 1. */ state->slot_days += state->slots_total;
  /* 2. */ state->occupied_slot_days += (int)state->planted.count;
  /* 3. */ WeatherDay weather = weather_generate(state->day, config, rng);
  state->current_season = weather.season;
  /* 4. */ weather_apply(state, &weather);
  /* 5. */ double liability =
      inventory_capture_storage_liability(state, &storage);
  /* 6. */ acted = actions_harvest_mature(state, config, rng, &config->watering,
                                           &config->fertilizer) ||
                    acted;
  ENGINE_CHECK_STATE_ALLOC("harvest");
  /* 7. */ int spoiled = inventory_age_and_spoil(state, &storage, false);
  ENGINE_CHECK_STATE_ALLOC("inventory aging");
  acted = spoiled > 0 || acted;
  /* 8. */ int completed = processing_complete_jobs(state);
  ENGINE_CHECK_STATE_ALLOC("processing completion");
  acted = completed > 0 || acted;
  /* 9. */ spoiled =
      inventory_enforce_storage_capacity(state, storage.capacity);
  ENGINE_CHECK_STATE_ALLOC("storage enforcement");
  acted = spoiled > 0 || acted;
  /* 10. */ markets_update_daily_prices(state, config, rng);
  /* 11. */ contracts_generate_offers(state, config, rng);
  ENGINE_CHECK_STATE_ALLOC("contract offer");

  /* 12. */ ContractDecisionBuffer accepts = {0};
  agent->choose_contracts(agent, state, config, &accepts);
  ENGINE_CHECK_ALLOC(accepts, contract_decision_free, "contract decision");
  ENGINE_CHECK_STATE_ALLOC("contract decision");
  for (size_t i = 0; i < accepts.count; i++) {
    acted = contracts_accept(state, config, accepts.data[i]) || acted;
    if (state->allocation_failed) {
      contract_decision_free(&accepts);
      set_error(error, ENGINE_ERROR_ALLOCATION, "contract acceptance allocation failed");
      return false;
    }
  }
  contract_decision_free(&accepts);
  ENGINE_CHECK_STATE_ALLOC("contract acceptance");
  /* 13. */ DeliveryDecisionBuffer deliveries = {0};
  agent->choose_contract_deliveries(agent, state, &deliveries);
  ENGINE_CHECK_ALLOC(deliveries, delivery_decision_free, "delivery decision");
  ENGINE_CHECK_STATE_ALLOC("delivery decision");
  for (size_t i = 0; i < deliveries.count; i++) {
    int delivered = 0;
    (void)contracts_deliver(state, config, deliveries.data[i].contract_id,
                            deliveries.data[i].quantity, &delivered);
    acted = delivered > 0 || acted;
    if (state->allocation_failed) {
      delivery_decision_free(&deliveries);
      set_error(error, ENGINE_ERROR_ALLOCATION, "contract delivery allocation failed");
      return false;
    }
  }
  delivery_decision_free(&deliveries);
  /* 14. */ ProcessingDecisionBuffer processing = {0};
  agent->choose_processing(agent, state, config, &processing);
  ENGINE_CHECK_ALLOC(processing, processing_decision_free, "processing decision");
  ENGINE_CHECK_STATE_ALLOC("processing decision");
  for (size_t i = 0; i < processing.count; i++) {
    const RecipeDef *recipe =
        config_find_recipe(config, processing.data[i].recipe_id);
    if (recipe != NULL)
      acted = processing_start_job(state, recipe, processing.data[i].batches,
                                   state->processing_capacity) ||
              acted;
    if (state->allocation_failed) {
      processing_decision_free(&processing);
      set_error(error, ENGINE_ERROR_ALLOCATION, "processing allocation failed");
      return false;
    }
  }
  processing_decision_free(&processing);
  /* 15. */ SalesDecisionBuffer sales = {0};
  agent->choose_sales(agent, state, config, &sales);
  ENGINE_CHECK_ALLOC(sales, sales_decision_free, "sales decision");
  ENGINE_CHECK_STATE_ALLOC("sales decision");
  for (size_t i = 0; i < sales.count; i++) {
    const ChannelDef *channel =
        config_find_channel(config, sales.data[i].channel_id);
    if (channel == NULL)
      continue;
    int sold = 0;
    bool exact = sales.data[i].quality != SALE_QUALITY_ANY;
    (void)markets_sell(state, sales.data[i].item_id, sales.data[i].quantity,
                       channel, exact, sales.data[i].quality, false,
                       QUALITY_REJECTED, &sold);
    acted = sold > 0 || acted;
    if (state->allocation_failed) {
      sales_decision_free(&sales);
      set_error(error, ENGINE_ERROR_ALLOCATION, "sale allocation failed");
      return false;
    }
  }
  sales_decision_free(&sales);
  /* 16. */
  for (size_t i = 0; i < config->upgrade_count; i++) {
    const UpgradeDef *upgrade = &config->upgrades[i];
    if (!state->upgrades_owned[upgrade->id]) {
      bool should_buy = agent->should_buy_upgrade(agent, state, upgrade->id);
      ENGINE_CHECK_STATE_ALLOC("upgrade decision");
      if (should_buy) {
        acted = actions_buy_upgrade(state, upgrade) || acted;
        ENGINE_CHECK_STATE_ALLOC("upgrade");
      }
    }
  }
  ENGINE_CHECK_STATE_ALLOC("upgrade");
  /* 17. Water and fertilize in ONE pass over the planted crops, per crop --
   * simulation/engine.py:176-183 is a single `for planted in
   * list(player.planted)` loop doing both. Splitting it into a water pass
   * followed by a fertilize pass looks equivalent and is not: both actions
   * spend money, so the split reorders the debits (W,W,F instead of W,F,W)
   * and every later balance rounds differently, which showed up as 1-256 ulp
   * drift in final_money/lowest_money against Python. It also changes
   * behaviour outright whenever cash is tight, since each action's
   * affordability check (`money < cost`) then sees a different balance.
   * RNG order is unaffected either way -- rng_chance below is the only draw
   * in this phase and its per-crop order is the same -- which is exactly why
   * the split survived the fixture suites undetected. */
  for (size_t i = 0; i < state->planted.count; i++) {
    PlantedCrop *planted = &state->planted.data[i];
    const CropDef *crop = config_find_crop(config, planted->crop_item_id);
    if (crop == NULL)
      continue;
    bool should_water = agent->should_water(agent, state, (int)i);
    ENGINE_CHECK_STATE_ALLOC("water decision");
    if (should_water && rng_chance(rng, agent->watering_diligence)) {
      acted = actions_water_crop(state, planted, &config->watering) || acted;
    }
    /* Operand order matches Python's `should_fertilize(...) and not
     * planted.fertilized`; no agent's should_fertilize has side effects, so
     * this is short-circuit-equivalent, but keep it in the reference's
     * order. */
    bool should_fertilize = agent->should_fertilize(agent, state, (int)i);
    ENGINE_CHECK_STATE_ALLOC("fertilizer decision");
    if (should_fertilize && !planted->fertilized) {
      if (state->fertilizer_inventory == 0)
        (void)actions_buy_fertilizer(state, &config->fertilizer, 1);
      acted =
          actions_fertilize_crop(state, planted, &config->fertilizer) || acted;
    }
  }
  ENGINE_CHECK_STATE_ALLOC("crop care");
  /* 18. */ acted = plant_open_slots(state, agent) || acted;
  ENGINE_CHECK_STATE_ALLOC("planting");
  /* 19. */ contracts_resolve_expired(state, config);
  /* 20. */ (void)inventory_collect_storage_liability(state, liability);
  /* 21. */ if (!acted)
    actions_do_nothing(state);
  /* 22. */ if (state->money < state->lowest_money)
    state->lowest_money = state->money;
  farm_state_track_peak_cash(state);
  if (no_viable_reinvestment(state)) {
    state->bankrupt = true;
    state->bankruptcy_day = state->day + 1;
    free(state->bankruptcy_reason);
    state->bankruptcy_reason = malloc(sizeof("no_viable_reinvestment"));
    if (state->bankruptcy_reason == NULL) {
      farm_state_mark_allocation_failed(state);
      set_error(error, ENGINE_ERROR_ALLOCATION, "bankruptcy reason allocation failed");
      return false;
    }
    strcpy(state->bankruptcy_reason, "no_viable_reinvestment");
  }
  /* 23. */ state->day++;
  if (callback != NULL)
    callback(state, &weather, context);
  return true;
}

bool engine_run_day(FarmState *state, const Agent *agent, FarmRng *rng,
                    EngineError *error) {
  return engine_run_day_observed(state, agent, rng, NULL, NULL, error);
}
