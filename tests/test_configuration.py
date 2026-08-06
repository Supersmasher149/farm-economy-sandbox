from copy import deepcopy

import pytest

from main import load_config
from simulation.configuration import validate, validate_simulation_config
from simulation import processing
from simulation.state import InventoryLot, PlayerState


def test_shipped_configuration_validates():
    crops, upgrades, config, _world = load_config()
    validate_simulation_config(config)
    assert crops and upgrades


def test_validation_rejects_reversed_crop_yield_range():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(crops)
    malformed[0]["min_yield"], malformed[0]["max_yield"] = 4, 2

    with pytest.raises(ValueError, match="min_yield"):
        validate(malformed, upgrades, world)


def test_validation_rejects_unknown_recipe_item():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    malformed["processing"]["recipes"][0]["input_item_id"] = "missing"

    with pytest.raises(ValueError, match="unknown item"):
        validate(crops, upgrades, malformed)


def test_validation_rejects_invalid_market_quality_enum():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    malformed["markets"]["channels"][0]["min_quality"] = "excellent"

    with pytest.raises(ValueError, match="min_quality"):
        validate(crops, upgrades, malformed)


def test_validation_rejects_invalid_simulation_settings():
    with pytest.raises(ValueError, match="days"):
        validate_simulation_config({"start_money": 10, "start_slots": 1, "days": 0, "operating_reserve": 0})


def test_processing_rejects_non_integer_batch_decision():
    player = PlayerState(money=100, slots_total=1)
    recipe = {
        "id": "dry",
        "input_item_id": "crop",
        "input_quantity": 1,
        "output_item_id": "dried",
        "output_quantity": 1,
        "processing_days": 1,
        "cost": 1,
        "shelf_life_days": 5,
    }

    assert not processing.start_job(player, recipe, 1.5, capacity=1)


def test_processing_rejects_zero_output_before_consuming_inventory():
    player = PlayerState(money=100, slots_total=1)
    player.inventory_lots.append(InventoryLot("crop", 1, "standard"))
    recipe = {
        "id": "invalid",
        "input_item_id": "crop",
        "input_quantity": 1,
        "output_item_id": "dried",
        "output_quantity": 0,
        "processing_days": 1,
        "cost": 1,
        "shelf_life_days": 5,
    }

    assert not processing.start_job(player, recipe, 1, capacity=1)
    assert player.money == 100
    assert player.inventory_lots[0].quantity == 1
