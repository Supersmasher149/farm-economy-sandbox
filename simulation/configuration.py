"""Validation for configuration consumed by the simulation runtime."""

import math
from collections.abc import Mapping

QUALITY_LEVELS = {"rejected", "processing", "standard", "premium"}
SEASONS = ("spring", "summer", "autumn", "winter")
EFFECT_TYPES = {"capacity", "growth_time_reduction", "storage", "processing_capacity"}
UNLOCK_TYPES = {"total_revenue", "upgrade"}
SOIL_LEVELS = {
    "moisture",
    "nitrogen",
    "phosphorus",
    "potassium",
    "ph",
    "soil_health",
    "pest_pressure",
    "disease_pressure",
}


def validate(crops: list, upgrades: list, world: dict) -> None:
    """Validate crop, upgrade, and world configuration or raise ``ValueError``."""
    _require_list(crops, "crops")
    _require_list(upgrades, "upgrades")
    _require_mapping(world, "world")

    crop_ids = _validate_crops(crops)
    upgrade_ids = _validate_upgrades(upgrades)
    processing = _required_mapping(world, "processing")
    products = processing.get("products", [])
    recipes = processing.get("recipes", [])
    _require_list(products, "processing.products")
    _require_list(recipes, "processing.recipes")
    product_ids = _validate_products(products)
    overlap = crop_ids & product_ids
    if overlap:
        raise ValueError(f"Crop and processing product ids must be unique: {sorted(overlap)}")
    item_ids = crop_ids | product_ids

    _validate_watering(_required_mapping(world, "watering"))
    _validate_fertilizer(_required_mapping(world, "fertilizer"))
    _validate_soil(_required_mapping(world, "soil"))
    _validate_weather(_required_mapping(world, "weather"))
    _validate_storage(_required_mapping(world, "storage"))
    _validate_markets(_required_mapping(world, "markets"))
    _validate_contracts(_required_mapping(world, "contracts"))
    _validate_buyers(_required_list(world, "buyers"), item_ids)
    _validate_processing(processing, recipes, item_ids)

    for crop in crops:
        requirement = crop.get("unlock_requirement")
        if requirement is None:
            continue
        _require_mapping(requirement, f"crop '{crop['id']}'.unlock_requirement")
        requirement_type = _required_value(
            requirement, "type", f"crop '{crop['id']}'.unlock_requirement"
        )
        _enum(requirement_type, UNLOCK_TYPES, f"crop '{crop['id']}'.unlock_requirement.type")
        if requirement_type == "total_revenue":
            _number(requirement, "value", f"crop '{crop['id']}.unlock_requirement", minimum=0)
        else:
            requirement_id = requirement.get("id")
            if not isinstance(requirement_id, str) or requirement_id not in upgrade_ids:
                raise ValueError(
                    f"Crop '{crop['id']}' references unknown upgrade '{requirement_id}'"
                )


def validate_simulation_config(config: dict) -> None:
    """Validate settings passed to ``runner.single_run``/``run_batch``."""
    _require_mapping(config, "simulation_settings")
    _integer(config, "days", "simulation_settings", minimum=1)
    _integer(config, "start_slots", "simulation_settings", minimum=1)
    _number(config, "start_money", "simulation_settings", minimum=0)
    if "operating_reserve" in config:
        _number(config, "operating_reserve", "simulation_settings", minimum=0)
    if (
        "seed" in config
        and config["seed"] is not None
        and (not isinstance(config["seed"], int) or isinstance(config["seed"], bool))
    ):
        raise ValueError("simulation_settings.seed must be an integer or null")


def _validate_crops(crops: list) -> set:
    if not crops:
        raise ValueError("At least one crop is required")
    crop_ids = _unique_ids(crops, "crop")
    required = (
        "name",
        "seed_cost",
        "growth_days",
        "min_yield",
        "max_yield",
        "base_price",
        "price_variation",
        "loss_chance",
        "water_interval_days",
    )
    for crop in crops:
        path = f"crop '{crop['id']}'"
        for field in required:
            _required_value(crop, field, path)
        _string(crop["name"], f"{path}.name")
        _number(crop, "seed_cost", path, minimum=0)
        _integer(crop, "growth_days", path, minimum=1)
        _integer(crop, "min_yield", path, minimum=0)
        _integer(crop, "max_yield", path, minimum=0)
        if crop["min_yield"] > crop["max_yield"]:
            raise ValueError(f"{path}.min_yield cannot exceed max_yield")
        _number(crop, "base_price", path, minimum=0)
        _number(crop, "price_variation", path, minimum=0, maximum=1)
        _number(crop, "loss_chance", path, minimum=0, maximum=1)
        _integer(crop, "water_interval_days", path, minimum=1)
        if "shelf_life_days" in crop:
            _integer(crop, "shelf_life_days", path, minimum=1)
        if "temperature_range" in crop:
            _ordered_range(crop["temperature_range"], f"{path}.temperature_range")
        if "ph_range" in crop:
            _ordered_range(crop["ph_range"], f"{path}.ph_range", minimum=0, maximum=14)
        if "min_moisture" in crop:
            _number(crop, "min_moisture", path, minimum=0, maximum=1)
        for field in ("pest_susceptibility", "disease_susceptibility"):
            if field in crop:
                _number(crop, field, path, minimum=0)
        if "nutrient_demand" in crop:
            _nonnegative_mapping(
                crop["nutrient_demand"],
                f"{path}.nutrient_demand",
                {"nitrogen", "phosphorus", "potassium"},
            )
        if "seasonal_demand" in crop:
            _seasonal_values(crop["seasonal_demand"], f"{path}.seasonal_demand", minimum=0)
    return crop_ids


def _validate_upgrades(upgrades: list) -> set:
    upgrade_ids = _unique_ids(upgrades, "upgrade")
    for upgrade in upgrades:
        path = f"upgrade '{upgrade['id']}'"
        _required_value(upgrade, "name", path)
        _required_value(upgrade, "cost", path)
        effect = _required_value(upgrade, "effect", path)
        _string(upgrade["name"], f"{path}.name")
        _number(upgrade, "cost", path, minimum=0)
        _require_mapping(effect, f"{path}.effect")
        effect_type = _required_value(effect, "type", f"{path}.effect")
        _enum(effect_type, EFFECT_TYPES, f"{path}.effect.type")
        if effect_type in {"capacity", "processing_capacity"}:
            _integer(effect, "amount", f"{path}.effect", minimum=1)
        elif effect_type == "growth_time_reduction":
            _number(
                effect, "amount", f"{path}.effect", minimum=0, maximum=None, exclusive_maximum=1
            )
        else:
            _integer(effect, "capacity_bonus", f"{path}.effect", minimum=0)
            _number(
                effect, "shelf_life_multiplier", f"{path}.effect", minimum=0, exclusive_minimum=0
            )
    return upgrade_ids


def _validate_products(products: list) -> set:
    product_ids = _unique_ids(products, "processing product")
    for product in products:
        path = f"processing product '{product['id']}'"
        _required_value(product, "name", path)
        _required_value(product, "processed_base_price", path)
        _string(product["name"], f"{path}.name")
        _number(product, "processed_base_price", path, minimum=0)
        # Optional: derived.py's market profile builder falls back to
        # markets.default_variation when a product omits this, so requiring
        # it here would reject configuration the runtime already handles.
        if "price_variation" in product:
            _number(product, "price_variation", path, minimum=0, maximum=1)
        _seasonal_values(product.get("seasonal_demand", {}), f"{path}.seasonal_demand", minimum=0)
    return product_ids


def _validate_processing(processing: dict, recipes: list, item_ids: set) -> None:
    _integer(processing, "base_capacity", "processing", minimum=0)
    _unique_ids(recipes, "processing recipe")
    for recipe in recipes:
        path = f"processing recipe '{recipe['id']}'"
        for field in ("input_item_id", "output_item_id", "input_quantity", "output_quantity"):
            _required_value(recipe, field, path)
        if (
            not isinstance(recipe["input_item_id"], str)
            or not isinstance(recipe["output_item_id"], str)
            or recipe["input_item_id"] not in item_ids
            or recipe["output_item_id"] not in item_ids
        ):
            raise ValueError(f"{path} references an unknown item")
        _integer(recipe, "input_quantity", path, minimum=1)
        _integer(recipe, "output_quantity", path, minimum=1)
        if "min_quality" in recipe:
            _enum(recipe["min_quality"], QUALITY_LEVELS, f"{path}.min_quality")
        _integer(recipe, "processing_days", path, minimum=1)
        _number(recipe, "cost", path, minimum=0)
        _integer(recipe, "shelf_life_days", path, minimum=1)


def _validate_watering(config: dict) -> None:
    path = "watering"
    for field in ("neglect_loss_chance_penalty_per_day", "neglect_yield_penalty_per_day"):
        _number(config, field, path, minimum=0, maximum=1)
    _number(config, "max_neglect_loss_chance_bonus", path, minimum=0, maximum=1)
    _number(config, "max_neglect_yield_penalty", path, minimum=0, maximum=1)
    _number(config, "cost_per_plot", path, minimum=0)
    _number(config, "moisture_added", path, minimum=0, maximum=1)


def _validate_fertilizer(config: dict) -> None:
    path = "fertilizer"
    _number(config, "cost", path, minimum=0)
    _number(config, "yield_bonus_pct", path, minimum=0)
    _number(config, "loss_chance_reduction", path, minimum=0, maximum=1)
    _nonnegative_mapping(
        config.get("nutrients_added", {}),
        f"{path}.nutrients_added",
        {"nitrogen", "phosphorus", "potassium"},
        maximum=1,
    )


def _validate_soil(config: dict) -> None:
    initial = config.get("initial", {})
    _require_mapping(initial, "soil.initial")
    unknown = set(initial) - SOIL_LEVELS
    if unknown:
        raise ValueError(f"soil.initial contains unknown fields: {sorted(unknown)}")
    for key, value in initial.items():
        if key == "ph":
            _number(value, None, "soil.initial.ph", minimum=0, maximum=14)
        else:
            _number(value, None, f"soil.initial.{key}", minimum=0, maximum=1)
    _nonnegative_mapping(
        config.get("regen_per_day", {}),
        "soil.regen_per_day",
        SOIL_LEVELS - {"ph"},
        maximum=1,
    )


def _validate_weather(config: dict) -> None:
    _integer(config, "season_length_days", "weather", minimum=1)
    seasons = _required_value(config, "seasons", "weather")
    _require_mapping(seasons, "weather.seasons")
    for season in SEASONS:
        values = _required_mapping(seasons, season, "weather.seasons")
        path = f"weather.seasons.{season}"
        _ordered_range(
            _required_value(values, "temperature_range", path), f"{path}.temperature_range"
        )
        _number(values, "rain_chance", path, minimum=0, maximum=1)
        _ordered_range(
            _required_value(values, "rainfall_range", path),
            f"{path}.rainfall_range",
            minimum=0,
            maximum=1,
        )
        _number(values, "evaporation", path, minimum=0, maximum=1)


def _validate_storage(config: dict) -> None:
    _integer(config, "capacity", "storage", minimum=0)
    _number(config, "shelf_life_multiplier", "storage", minimum=0, exclusive_minimum=0)
    _number(config, "daily_cost", "storage", minimum=0)


def _validate_markets(config: dict) -> None:
    _number(config, "default_variation", "markets", minimum=0, maximum=1)
    _number(config, "minimum_supply_multiplier", "markets", minimum=0, maximum=1)
    _number(config, "supply_decay", "markets", minimum=0, maximum=1)
    channels = _required_list(config, "channels")
    channel_ids = _unique_ids(channels, "market channel")
    if "spot" not in channel_ids:
        raise ValueError("markets must define a 'spot' channel")
    for channel in channels:
        path = f"market channel '{channel['id']}'"
        _number(channel, "price_multiplier", path, minimum=0)
        _enum(channel.get("min_quality", "rejected"), QUALITY_LEVELS, f"{path}.min_quality")
        _integer(channel, "daily_capacity", path, minimum=1)
        if "fee_rate" in channel:
            _number(channel, "fee_rate", path, minimum=0, maximum=1)
        if "flat_fee" in channel:
            _number(channel, "flat_fee", path, minimum=0)
        if "min_reputation" in channel:
            _number(channel, "min_reputation", path, minimum=0)
        if "reputation_bonus" in channel:
            _number(channel, "reputation_bonus", path, minimum=0)


def _validate_contracts(config: dict) -> None:
    _integer(config, "offer_interval_days", "contracts", minimum=1)
    _number(config, "default_penalty_rate", "contracts", minimum=0, maximum=1)
    _number(config, "production_safety_factor", "contracts", minimum=0, maximum=1)
    if "offer_expiry_days" in config:
        _integer(config, "offer_expiry_days", "contracts", minimum=1)


def _validate_buyers(buyers: list, item_ids: set) -> None:
    _unique_ids(buyers, "buyer")
    for buyer in buyers:
        path = f"buyer '{buyer['id']}'"
        items = _required_value(buyer, "items", path)
        _require_list(items, f"{path}.items")
        if any(not isinstance(item_id, str) for item_id in items):
            raise ValueError(f"{path}.items must contain string item ids")
        unknown = set(items) - item_ids
        if unknown:
            raise ValueError(f"{path} references unknown items: {sorted(unknown)}")
        quantity_range = _required_value(buyer, "quantity_range", path)
        _ordered_range(quantity_range, f"{path}.quantity_range", integer=True, minimum=1)
        _enum(buyer.get("min_quality", "standard"), QUALITY_LEVELS, f"{path}.min_quality")
        if "contract_price_multiplier" in buyer:
            _number(buyer, "contract_price_multiplier", path, minimum=0)
        _integer(buyer, "deadline_days", path, minimum=1)
        if "penalty_rate" in buyer:
            _number(buyer, "penalty_rate", path, minimum=0, maximum=1)
        if "min_reputation" in buyer:
            _number(buyer, "min_reputation", path, minimum=0)


def _required_mapping(mapping: Mapping, key: str, parent: str = "") -> dict:
    value = _required_value(mapping, key, parent or "configuration")
    _require_mapping(value, f"{parent + '.' if parent else ''}{key}")
    return value


def _required_list(mapping: Mapping, key: str, parent: str = "") -> list:
    value = _required_value(mapping, key, parent or "configuration")
    _require_list(value, f"{parent + '.' if parent else ''}{key}")
    return value


def _required_value(mapping: Mapping, key: str, path: str):
    if key not in mapping:
        raise ValueError(f"{path}.{key} is required")
    return mapping[key]


def _require_mapping(value, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be an object")


def _require_list(value, path: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")


def _unique_ids(values: list, label: str) -> set:
    identifiers = []
    for index, value in enumerate(values):
        _require_mapping(value, f"{label}[{index}]")
        identifier = value.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"Every {label} requires a non-empty string id")
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"Duplicate {label} id")
    return set(identifiers)


def _string(value, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")


def _number(
    mapping_or_value,
    key,
    path: str,
    minimum=None,
    maximum=None,
    exclusive_minimum=None,
    exclusive_maximum=None,
) -> None:
    value = mapping_or_value if key is None else mapping_or_value.get(key)
    field_path = path if key is None else f"{path}.{key}"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_path} must be numeric")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_path} must be at most {maximum}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise ValueError(f"{field_path} must be greater than {exclusive_minimum}")
    if exclusive_maximum is not None and value >= exclusive_maximum:
        raise ValueError(f"{field_path} must be less than {exclusive_maximum}")


def _integer(mapping: Mapping, key: str, path: str, minimum=None) -> None:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{path}.{key} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path}.{key} must be at least {minimum}")


def _enum(value, choices: set, path: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{path} must be one of {sorted(choices)}")


def _ordered_range(value, path: str, minimum=None, maximum=None, integer=False) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{path} must contain exactly two values")
    if integer:
        if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
            raise ValueError(f"{path} values must be integers")
    elif any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
        for item in value
    ):
        raise ValueError(f"{path} values must be numeric")
    if value[0] > value[1]:
        raise ValueError(f"{path} must be ordered low-to-high")
    for item in value:
        if minimum is not None and item < minimum:
            raise ValueError(f"{path} values must be at least {minimum}")
        if maximum is not None and item > maximum:
            raise ValueError(f"{path} values must be at most {maximum}")


def _nonnegative_mapping(value, path: str, allowed: set, maximum=None) -> None:
    _require_mapping(value, path)
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown fields: {sorted(unknown)}")
    for key, amount in value.items():
        _number(amount, None, f"{path}.{key}", minimum=0, maximum=maximum)


def _seasonal_values(value, path: str, minimum=None) -> None:
    _require_mapping(value, path)
    unknown = set(value) - set(SEASONS)
    if unknown:
        raise ValueError(f"{path} contains unknown seasons: {sorted(unknown)}")
    for season, amount in value.items():
        _number(amount, None, f"{path}.{season}", minimum=minimum)
