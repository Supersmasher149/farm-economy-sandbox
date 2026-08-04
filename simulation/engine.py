"""Deterministic daily simulation orchestration."""
from copy import deepcopy

from simulation import actions, contracts, crop_growth, economy_rules, inventory, markets, processing, weather


def _legacy_run_day(player, agent, crops, crops_by_id, upgrades, upgrades_by_id, watering_settings, fertilizer_config, rng):
    player.slot_days += player.slots_total
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


def _effective_storage(base: dict, player, upgrades_by_id: dict) -> dict:
    result = deepcopy(base)
    for upgrade_id in player.upgrades_owned:
        effect = upgrades_by_id[upgrade_id]["effect"]
        if effect["type"] == "storage":
            result["capacity"] = result.get("capacity", 100) + effect.get("capacity_bonus", 0)
            result["shelf_life_multiplier"] = result.get("shelf_life_multiplier", 1) * effect.get("shelf_life_multiplier", 1)
    return result


def _processing_capacity(config: dict, player, upgrades_by_id: dict) -> int:
    capacity = config.get("base_capacity", 0)
    for upgrade_id in player.upgrades_owned:
        effect = upgrades_by_id[upgrade_id]["effect"]
        if effect["type"] == "processing_capacity":
            capacity += effect["amount"]
    return capacity


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
    items_by_id = dict(crops_by_id)
    items_by_id.update({product["id"]: product for product in world["processing"].get("products", [])})

    player.current_weather = weather.generate_weather(player.day, world["weather"], rng)
    weather.apply_weather(player, crops_by_id, player.current_weather, crop_growth)
    harvested = actions.harvest_mature(
        player, crops_by_id, rng, world["watering"], world["fertilizer"]
    )
    storage = _effective_storage(world["storage"], player, upgrades_by_id)
    spoiled = inventory.age_and_spoil(player, storage)
    completed = processing.complete_jobs(player)
    markets.update_daily_prices(player, items_by_id, world["markets"], rng)
    new_offers = contracts.generate_offers(player, world["contracts"], world["buyers"], items_by_id, rng)

    acted = bool(harvested or spoiled or completed)
    for contract_id in agent.choose_contracts(player, list(new_offers)):
        acted = contracts.accept(player, contract_id) or acted
    for decision in agent.choose_contract_deliveries(player):
        _revenue, delivered = contracts.deliver(player, decision["contract_id"], decision["quantity"])
        acted = delivered > 0 or acted

    recipes_by_id = {recipe["id"]: recipe for recipe in world["processing"].get("recipes", [])}
    capacity = _processing_capacity(world["processing"], player, upgrades_by_id)
    for decision in agent.choose_processing(player, list(recipes_by_id.values()), items_by_id):
        recipe = recipes_by_id.get(decision["recipe_id"])
        if recipe:
            acted = processing.start_job(player, recipe, decision.get("batches", 1), capacity) or acted

    channels = world["markets"]["channels"]
    channels_by_id = {channel["id"]: channel for channel in channels}
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
            acted = actions.water_crop(player, planted, world["watering"]) or acted
        if agent.should_fertilize(player, planted, crop, world["fertilizer"]) and not planted.fertilized:
            if player.fertilizer_inventory == 0:
                actions.buy_fertilizer(player, world["fertilizer"])
            acted = actions.fertilize_crop(player, planted, world["fertilizer"]) or acted

    acted = _plant_open_slots(
        player, agent, crops, crops_by_id, upgrades_by_id, world["fertilizer"]
    ) or acted
    contracts.resolve_expired(player)
    _finish_day(player, crops, acted)


def _plant_open_slots(player, agent, crops, crops_by_id, upgrades_by_id, fertilizer_config=None) -> bool:
    planted_something = False
    while player.open_slots > 0:
        crop = agent.choose_crop(player, crops, crops_by_id, upgrades_by_id)
        if crop is None or not economy_rules.is_crop_unlocked(crop, player) or player.money < crop["seed_cost"]:
            break
        use_fertilizer = bool(
            fertilizer_config
            and agent.should_use_fertilizer(player, crop, fertilizer_config)
        )
        if use_fertilizer and player.fertilizer_inventory == 0:
            actions.buy_fertilizer(player, fertilizer_config, 1)
        use_fertilizer = use_fertilizer and player.fertilizer_inventory > 0
        if not actions.buy_seeds(player, crop, 1):
            break
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
    cheapest_seed = min(crop["seed_cost"] for crop in crops)
    has_inventory = any(lot.quantity > 0 for lot in player.inventory_lots) or any(player.crop_inventory.values())
    if player.money < cheapest_seed and not player.planted and not has_inventory and not player.processing_jobs:
        player.bankrupt = True
    player.day += 1
