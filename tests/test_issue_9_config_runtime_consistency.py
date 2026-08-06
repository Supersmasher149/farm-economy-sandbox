"""Regression tests for issue #9's reopened follow-up: configuration
validation must accept exactly the values the runtime already treats as
valid, and every value validation accepts must be safe at runtime.

Three schema/runtime consistency defects, each covered here:
- seed_cost == 0 passes validation but crashed contract feasibility's
  floor-division (simulation/contracts.py).
- soil.regen_per_day.moisture passes validation but was never read by
  simulation/weather.py -- a silently-ignored setting.
- processing products were required to specify price_variation, although
  simulation/derived.py already falls back to markets.default_variation
  when it's omitted -- validation was stricter than the runtime.
"""

from copy import deepcopy

import pytest

from main import load_config
from simulation import contracts, crop_growth, derived, weather
from simulation.configuration import validate
from simulation.state import ContractState, PlayerState

# -- seed_cost == 0 must not crash contract feasibility -----------------------


def test_zero_seed_cost_crop_validates():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(crops)
    malformed[0]["seed_cost"] = 0
    validate(malformed, upgrades, world)  # must not raise


def test_zero_seed_cost_crop_does_not_crash_contract_feasibility():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(crops)
    malformed[0]["seed_cost"] = 0
    crop = malformed[0]

    player = PlayerState(money=50, slots_total=2)
    player.crop_catalog = {c["id"]: c for c in malformed}
    player.upgrades_catalog = {}
    player.contract_config = world["contracts"]
    contract = ContractState("c", "buyer", crop["id"], 5, "standard", 10, 0, 10, 0.1)

    # Previously raised ZeroDivisionError via a `// seed_cost` floor-division.
    assert contracts.producible_quantity(player, contract) >= 0
    assert contracts.is_offer_feasible(player, contract) in (True, False)


def test_zero_seed_cost_never_leaves_cash_as_the_limiting_factor():
    """A free crop's future capacity must not be capped by available cash --
    only by growth-cycle throughput (open slots x days available).
    """
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(crops)
    malformed[0]["seed_cost"] = 0
    crop = malformed[0]

    poor_player = PlayerState(money=0, slots_total=1)
    poor_player.crop_catalog = {c["id"]: c for c in malformed}
    poor_player.upgrades_catalog = {}
    contract = ContractState(
        "c", "buyer", crop["id"], 1, "standard", 10, 0, crop["growth_days"] + 2, 0.1
    )

    assert contracts.producible_quantity(poor_player, contract) > 0


# -- soil.regen_per_day.moisture must validate and actually apply ------------


def test_soil_regen_moisture_validates():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    malformed["soil"]["regen_per_day"]["moisture"] = 0.05
    validate(crops, upgrades, malformed)  # must not raise


def test_soil_regen_moisture_is_applied_at_runtime():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    malformed["soil"]["regen_per_day"]["moisture"] = 0.05
    crops_by_id = {c["id"]: c for c in crops}

    player = PlayerState(money=10, slots_total=1)
    player.plots[0].moisture = 0.5

    weather.apply_weather(
        player,
        crops_by_id,
        {"rainfall": 0.0},
        crop_growth,
        plot_regen=malformed["soil"]["regen_per_day"],
    )

    # Previously silently ignored: moisture would have stayed at 0.5.
    assert player.plots[0].moisture == pytest.approx(0.55)


# -- processing product price_variation must be optional, like the runtime ---


def test_product_without_price_variation_validates():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    malformed["processing"]["products"][0].pop("price_variation")
    validate(crops, upgrades, malformed)  # must not raise


def test_product_without_price_variation_uses_market_default_at_runtime():
    crops, upgrades, _config, world = load_config()
    malformed = deepcopy(world)
    product = malformed["processing"]["products"][0]
    product.pop("price_variation")

    crops_by_id = {c["id"]: c for c in crops}
    items_by_id = dict(crops_by_id)
    items_by_id.update({p["id"]: p for p in malformed["processing"]["products"]})
    profiles = derived.market_profiles(items_by_id, malformed["markets"])

    entry = next(p for p in profiles if p[0] == product["id"])
    _item_id, _base_price, variation, _seasonal_demand = entry
    assert variation == malformed["markets"]["default_variation"]
