"""Validated state-changing farm actions."""

from simulation import crop_growth, inventory
from simulation.state import InventoryLot, PlantedCrop

DEFAULT_WATERING = {
    "neglect_loss_chance_penalty_per_day": 0.05,
    "neglect_yield_penalty_per_day": 0.08,
    "max_neglect_loss_chance_bonus": 0.60,
    "max_neglect_yield_penalty": 0.80,
}
DEFAULT_FERTILIZER = {"cost": 8, "yield_bonus_pct": 0.25, "loss_chance_reduction": 0.03}


def buy_seeds(player, crop: dict, quantity: int = 1) -> bool:
    cost = crop["seed_cost"] * quantity
    if quantity <= 0 or player.money < cost:
        return False
    player.money -= cost
    player.record_expense("seeds", cost)
    player.seed_inventory[crop["id"]] = player.seed_inventory.get(crop["id"], 0) + quantity
    return True


def plant_seed(
    player, crop: dict, growth_days: int, fertilized: bool = False, fertilizer_config=None
) -> bool:
    if player.open_slots <= 0 or player.seed_inventory.get(crop["id"], 0) <= 0:
        return False
    if fertilized and player.fertilizer_inventory <= 0:
        return False
    plot_index = next((index for index, plot in enumerate(player.plots) if plot.crop is None), None)
    if plot_index is None:
        return False
    planted = PlantedCrop(
        crop["id"], player.day, growth_days, plot_index=plot_index, fertilized=fertilized
    )
    player.seed_inventory[crop["id"]] -= 1
    if fertilized:
        player.fertilizer_inventory -= 1
        player.total_fertilizer_applied += 1
    player.planted.append(planted)
    player.plots[plot_index].crop = planted
    if fertilized:
        fertilizer_config = fertilizer_config or DEFAULT_FERTILIZER
        nutrients = fertilizer_config.get(
            "nutrients_added", {"nitrogen": 0.25, "phosphorus": 0.15, "potassium": 0.15}
        )
        for name, amount in nutrients.items():
            setattr(
                player.plots[plot_index],
                name,
                min(1.0, getattr(player.plots[plot_index], name) + amount),
            )
    player.total_planted += 1
    player.crop_plant_counts[crop["id"]] = player.crop_plant_counts.get(crop["id"], 0) + 1
    return True


def water_crop(player, planted: PlantedCrop, watering_settings: dict) -> bool:
    cost = watering_settings.get("cost_per_plot", 0.0)
    if player.money < cost:
        return False
    plot = player.plots[planted.plot_index] if planted.plot_index is not None else None
    if plot is None:
        return False
    player.money -= cost
    player.record_expense("watering", cost)
    planted.last_watered_day = player.day
    planted.neglect_days = 0
    plot.moisture = min(1.0, plot.moisture + watering_settings.get("moisture_added", 0.45))
    player.total_waterings += 1
    return True


def water_farm(player, agent, crops_by_id: dict, rng, watering_settings=None) -> bool:
    """Compatibility action: water every overdue crop on one diligence roll."""
    watering_settings = watering_settings or DEFAULT_WATERING
    overdue = [
        planted
        for planted in player.planted
        if planted.neglect_days > 0
        or player.day - planted.last_watered_day
        >= crops_by_id[planted.crop_id].get("water_interval_days", 3)
    ]
    if not overdue:
        return False
    if not rng.roll_watering(agent.watering_diligence):
        for planted in overdue:
            interval = crops_by_id[planted.crop_id].get("water_interval_days", 3)
            planted.neglect_days = max(0, player.day - planted.last_watered_day - interval)
        return False
    watered = False
    for planted in overdue:
        watered = water_crop(player, planted, watering_settings) or watered
    return watered


def buy_fertilizer(player, fertilizer_config: dict, quantity: int = 1) -> bool:
    cost = fertilizer_config["cost"] * quantity
    if quantity <= 0 or player.money < cost:
        return False
    player.money -= cost
    player.record_expense("fertilizer", cost)
    player.fertilizer_inventory += quantity
    player.total_fertilizer_bought += quantity
    return True


def fertilize_crop(player, planted: PlantedCrop, fertilizer_config: dict) -> bool:
    if planted.fertilized or player.fertilizer_inventory <= 0:
        return False
    planted.fertilized = True
    player.fertilizer_inventory -= 1
    player.total_fertilizer_applied += 1
    if planted.plot_index is not None:
        plot = player.plots[planted.plot_index]
        nutrients = fertilizer_config.get(
            "nutrients_added", {"nitrogen": 0.25, "phosphorus": 0.15, "potassium": 0.15}
        )
        for name, amount in nutrients.items():
            setattr(plot, name, min(1.0, getattr(plot, name) + amount))
    return True


def harvest_mature(player, crops_by_id: dict, *args) -> bool:
    if len(args) == 1:
        rng, watering_settings, fertilizer_config = args[0], DEFAULT_WATERING, DEFAULT_FERTILIZER
    elif len(args) == 3 and hasattr(args[0], "roll_loss"):
        rng, watering_settings, fertilizer_config = args
    elif len(args) == 3:
        watering_settings, fertilizer_config, rng = args
    else:
        raise TypeError("harvest_mature expects rng or watering_settings, fertilizer_config, rng")
    watering_settings = watering_settings or DEFAULT_WATERING
    fertilizer_config = fertilizer_config or DEFAULT_FERTILIZER
    still_growing = []
    harvested_any = False
    for planted in player.planted:
        if not planted.is_mature(player.day):
            still_growing.append(planted)
            continue
        harvested_any = True
        player.total_harvest_events += 1
        crop = crops_by_id[planted.crop_id]
        plot = player.plots[planted.plot_index] if planted.plot_index is not None else None
        lost, amount = crop_growth.compute_harvest_outcome(
            planted, crop, watering_settings, fertilizer_config, rng, plot
        )
        if lost or amount <= 0:
            player.total_crops_lost += 1
            player.losses_by_cause["crop_loss"] = player.losses_by_cause.get("crop_loss", 0) + 1
        else:
            _yield_multiplier, quality_score = crop_growth.harvest_multipliers(planted, crop, plot)
            grade = crop_growth.quality_grade(quality_score)
            if grade != "rejected":
                player.inventory_lots.append(
                    InventoryLot(
                        item_id=planted.crop_id,
                        quantity=amount,
                        quality=grade,
                        produced_day=player.day,
                        shelf_life_days=crop.get("shelf_life_days", 7),
                        unit_cost=crop["seed_cost"] / amount,
                    )
                )
                player.total_harvested += amount
                player.quality_harvested[grade] = player.quality_harvested.get(grade, 0) + amount
            else:
                player.total_crops_lost += 1
                player.losses_by_cause["rejected_quality"] = (
                    player.losses_by_cause.get("rejected_quality", 0) + amount
                )
        if plot is not None:
            plot.previous_crop_family = crop.get("family", crop["id"])
            plot.crop = None
            plot.soil_health = max(0.1, plot.soil_health - 0.02)
    player.planted = still_growing
    player.rebuild_crop_inventory()
    return harvested_any


def sell_all(player, crops_by_id: dict, rng) -> tuple[float, int]:
    """Compatibility sale through a basic spot market at one rolled item price."""
    player.import_legacy_inventory(crops_by_id)
    revenue = 0.0
    sold = 0
    for crop_id in list(player.crop_inventory):
        quantity = inventory.available_quantity(player, crop_id)
        if quantity <= 0:
            continue
        crop = crops_by_id[crop_id]
        price = rng.roll_price(crop["base_price"], crop["price_variation"])
        consumed, _cost = inventory.consume(player, crop_id, quantity)
        revenue += price * consumed
        sold += consumed
    if sold:
        player.money += revenue
        player.track_peak_cash()
        player.total_revenue += revenue
        player.total_sold += sold
        player.revenue_by_channel["spot"] = player.revenue_by_channel.get("spot", 0) + revenue
    return revenue, sold


def buy_upgrade(player, upgrade: dict) -> bool:
    if upgrade["id"] in player.upgrades_owned or player.money < upgrade["cost"]:
        return False
    player.money -= upgrade["cost"]
    player.record_expense("upgrades", upgrade["cost"])
    player.upgrades_owned.add(upgrade["id"])
    player.upgrade_purchase_days[upgrade["id"]] = player.day
    effect = upgrade["effect"]
    if effect["type"] == "capacity":
        player.add_slots(effect["amount"])
    return True


def do_nothing(player) -> None:
    player.idle_days += 1
