"""Deterministic daily simulation orchestration."""
from simulation import (
    actions, contracts, crop_growth, derived, economy_rules, inventory, markets, processing, weather,
)


def _legacy_run_day(player, agent, crops, crops_by_id, upgrades, upgrades_by_id, watering_settings, fertilizer_config, rng):
    player.slot_days += player.slots_total
    player.occupied_slot_days += len(player.planted)
    watered = actions.water_farm(player, agent, crops_by_id, rng, watering_settings)
    harvested = actions.harvest_mature(player, crops_by_id, rng, watering_settings, fertilizer_config)
    _revenue, sold = actions.sell_all(player, crops_by_id, rng)
    upgrade_bought = False
    for upgrade in upgrades:
        if upgrade["id"] not in player.upgrades_owned and agent.should_buy_upgrade(player, upgrade):
            upgrade_bought = actions.buy_upgrade(player, upgrade) or upgrade_bought
    planted_something = _plant_open_slots(
        player, agent, crops, crops_by_id, upgrades_by_id, fertilizer_config
    )
    _finish_day(player, crops, bool(watered or harvested or sold or upgrade_bought or planted_something))


def _effective_storage(base: dict, player, upgrades_by_id: dict, lookups) -> dict:
    return lookups.effective_storage(base, player.upgrades_owned, upgrades_by_id)


def _processing_capacity(config: dict, player, upgrades_by_id: dict, lookups) -> int:
    return lookups.processing_capacity(config, player.upgrades_owned, upgrades_by_id)


def run_day(player, agent, crops: list, crops_by_id: dict, upgrades: list, upgrades_by_id: dict, *args, world=None) -> None:
    if len(args) == 1:
        watering_settings, fertilizer_config, rng = actions.DEFAULT_WATERING, actions.DEFAULT_FERTILIZER, args[0]
    elif len(args) == 3:
        watering_settings, fertilizer_config, rng = args
        watering_settings = watering_settings or actions.DEFAULT_WATERING
        fertilizer_config = fertilizer_config or actions.DEFAULT_FERTILIZER
    else:
        raise TypeError("run_day expects rng or watering_settings, fertilizer_config, rng")
    if not world:
        _legacy_run_day(
            player, agent, crops, crops_by_id, upgrades, upgrades_by_id,
            watering_settings, fertilizer_config, rng,
        )
        return

    player.slot_days += player.slots_total
    player.occupied_slot_days += len(player.planted)
    # Item/recipe/channel indexes are pure functions of the world config, so
    # they are built once per (world, crops) pair instead of being rebuilt on
    # every simulated day of every run.
    lookups = derived.world_lookups(world, crops_by_id)
    items_by_id = lookups.items_by_id
    watering_config = lookups.watering
    fertilizer = lookups.fertilizer
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = upgrades_by_id
    player.contract_config = lookups.contracts

    player.current_weather = weather.generate_weather(player.day, lookups.weather, rng)
    weather.apply_weather(
        player, crops_by_id, player.current_weather, crop_growth,
        lookups.crop_profiles, lookups.plot_regen,
    )
    harvested = actions.harvest_mature(player, crops_by_id, rng, watering_config, fertilizer)
    storage = _effective_storage(lookups.storage_config, player, upgrades_by_id, lookups)
    spoiled = inventory.age_and_spoil(player, storage)
    completed = processing.complete_jobs(player)
    markets.update_daily_prices(player, items_by_id, lookups.markets, rng, lookups.market_profiles)
    player.market_channels = lookups.channels
    new_offers = contracts.generate_offers(player, lookups.contracts, lookups.buyers, items_by_id, rng)

    acted = bool(harvested or spoiled or completed)
    for contract_id in agent.choose_contracts(player, list(new_offers)):
        acted = contracts.accept(player, contract_id) or acted
    for decision in agent.choose_contract_deliveries(player):
        _revenue, delivered = contracts.deliver(player, decision["contract_id"], decision["quantity"])
        acted = delivered > 0 or acted

    recipes_by_id = lookups.recipes_by_id
    capacity = _processing_capacity(lookups.processing, player, upgrades_by_id, lookups)
    for decision in agent.choose_processing(player, lookups.recipes, items_by_id):
        recipe = recipes_by_id.get(decision["recipe_id"])
        if recipe:
            acted = processing.start_job(player, recipe, decision.get("batches", 1), capacity) or acted

    channels = lookups.channels
    channels_by_id = lookups.channels_by_id
    for decision in agent.choose_sales(player, channels, items_by_id):
        channel = channels_by_id.get(decision["channel_id"])
        if channel:
            _revenue, sold = markets.sell(player, decision["item_id"], decision["quantity"], channel)
            acted = sold > 0 or acted

    for upgrade in upgrades:
        if upgrade["id"] not in player.upgrades_owned and agent.should_buy_upgrade(player, upgrade):
            acted = actions.buy_upgrade(player, upgrade) or acted

    for planted in list(player.planted):
        crop = crops_by_id[planted.crop_id]
        if agent.should_water(player, planted, crop) and rng.chance(agent.watering_diligence):
            acted = actions.water_crop(player, planted, watering_config) or acted
        if agent.should_fertilize(player, planted, crop, fertilizer) and not planted.fertilized:
            if player.fertilizer_inventory == 0:
                actions.buy_fertilizer(player, fertilizer)
            acted = actions.fertilize_crop(player, planted, fertilizer) or acted

    acted = _plant_open_slots(
        player, agent, crops, crops_by_id, upgrades_by_id, fertilizer
    ) or acted
    contracts.resolve_expired(player)
    _finish_day(player, crops, acted)


def _plant_open_slots(player, agent, crops, crops_by_id, upgrades_by_id, fertilizer_config=None) -> bool:
    planted_something = False
    while player.open_slots > 0:
        for candidate in crops:
            unlocked = economy_rules.is_crop_unlocked(candidate, player)
            affordable = player.money >= candidate["seed_cost"]
            player.observe_crop_decision(candidate, unlocked, affordable)
        crop = agent.choose_crop(player, crops, crops_by_id, upgrades_by_id)
        if crop is None:
            break
        unlocked = economy_rules.is_crop_unlocked(crop, player)
        affordable = player.money >= crop["seed_cost"]
        if not unlocked or not affordable:
            player.observe_crop_decision(
                crop,
                unlocked,
                affordable,
                blocked_reason="locked" if not unlocked else "unaffordable",
                count_opportunity=False,
            )
            break
        use_fertilizer = bool(
            fertilizer_config
            and agent.should_use_fertilizer(player, crop, fertilizer_config)
        )
        if use_fertilizer and player.fertilizer_inventory == 0:
            combined_cost = crop["seed_cost"] + fertilizer_config["cost"]
            if player.money >= combined_cost:
                actions.buy_fertilizer(player, fertilizer_config, 1)
            else:
                # Fertilizer is optional for planting; do not buy it if the
                # same cash cannot also cover the seed.
                use_fertilizer = False
        use_fertilizer = use_fertilizer and player.fertilizer_inventory > 0
        if not actions.buy_seeds(player, crop, 1):
            break
        player.observe_crop_decision(crop, True, True, selected=True, count_opportunity=False)
        growth_days = economy_rules.effective_growth_days(crop, player, upgrades_by_id)
        planted_something = actions.plant_seed(
            player, crop, growth_days, fertilized=use_fertilizer, fertilizer_config=fertilizer_config
        ) or planted_something
    return planted_something


def _finish_day(player, crops: list, acted: bool) -> None:
    if not acted:
        actions.do_nothing(player)
    player.lowest_money = min(player.lowest_money, player.money)
    player.highest_money = max(player.highest_money, player.money)
    cheapest_seed = derived.cheapest_seed_cost(crops)
    if (
        player.money < cheapest_seed
        and not player.planted
        and not player.processing_jobs
        and not any(lot.quantity > 0 for lot in player.inventory_lots)
        and not any(player.crop_inventory.values())
    ):
        player.bankrupt = True
        player.bankruptcy_day = player.day + 1
        player.bankruptcy_reason = "no_viable_reinvestment"
    player.day += 1
