"""Table-driven schema tests for simulation/configuration.py.

The economy is rebalanced by editing `config/*.json` by hand, so the failure
mode that matters is a config file that *loads* and quietly does not mean what
its author wrote: a misspelled key the runtime never reads, a value outside the
range the maths assumes, an id that points at nothing. Every case below starts
from the shipped configuration and breaks exactly one thing, so a failure names
the check that stopped protecting you rather than a fixture that drifted.
"""

from copy import deepcopy

import pytest

from main import load_config
from simulation.configuration import (
    ALLOWED_EFFECT_FIELDS,
    EFFECT_TYPES,
    SOIL_DYNAMICS_BOUNDS,
    validate,
    validate_simulation_config,
)


@pytest.fixture(scope="module")
def shipped():
    crops, upgrades, config, world = load_config()
    return crops, upgrades, config, world


@pytest.fixture
def cfg(shipped):
    crops, upgrades, config, world = shipped
    return deepcopy(crops), deepcopy(upgrades), deepcopy(config), deepcopy(world)


def _validate(cfg):
    crops, upgrades, _config, world = cfg
    validate(crops, upgrades, world)


# -- unknown keys -------------------------------------------------------------
# One per record type. The runtime reads a fixed set of keys from each of
# these; anything else is a typo wearing a plausible name.

UNKNOWN_KEY_CASES = {
    "world-section": lambda c: c[3].update(irrigation={}),
    "crop": lambda c: c[0][0].update(loss_chnace=0.1),
    "crop-unlock-requirement": lambda c: c[0][2]["unlock_requirement"].update(threshold=1),
    "upgrade": lambda c: c[1][0].update(prerequisite="none"),
    "upgrade-effect": lambda c: c[1][0]["effect"].update(multiplier=2),
    "processing": lambda c: c[3]["processing"].update(max_parallel=2),
    "processing-product": lambda c: c[3]["processing"]["products"][0].update(shelf_life_days=3),
    "processing-recipe": lambda c: c[3]["processing"]["recipes"][0].update(yield_bonus=1),
    "watering": lambda c: c[3]["watering"].update(cost_per_day=1),
    "fertilizer": lambda c: c[3]["fertilizer"].update(growth_bonus=1),
    "soil": lambda c: c[3]["soil"].update(erosion={}),
    "weather": lambda c: c[3]["weather"].update(storm_chance=0.1),
    "weather-season": lambda c: c[3]["weather"]["seasons"]["spring"].update(humidity=0.5),
    "storage": lambda c: c[3]["storage"].update(max_lots=10),
    "markets": lambda c: c[3]["markets"].update(price_floor=1),
    "market-channel": lambda c: c[3]["markets"]["channels"][0].update(max_quality="premium"),
    "contracts": lambda c: c[3]["contracts"].update(max_active=3),
    "buyer": lambda c: c[3]["buyers"][0].update(reputation_bonus=1),
}


@pytest.mark.parametrize("case", sorted(UNKNOWN_KEY_CASES))
def test_unknown_keys_are_rejected(cfg, case):
    UNKNOWN_KEY_CASES[case](cfg)
    with pytest.raises(ValueError, match="unknown fields"):
        _validate(cfg)


def test_a_fifth_season_is_rejected(cfg):
    """`weather.seasons` is indexed by the four names in SEASONS; a fifth is
    never consulted, so accepting it would be accepting dead config."""
    cfg[3]["weather"]["seasons"]["monsoon"] = deepcopy(cfg[3]["weather"]["seasons"]["summer"])
    with pytest.raises(ValueError, match="unknown fields"):
        _validate(cfg)


def test_the_unknown_key_message_names_the_real_spelling(cfg):
    """A near-miss is the common case, so the message has to be actionable."""
    cfg[0][0]["loss_chnace"] = 0.1
    with pytest.raises(ValueError) as error:
        _validate(cfg)

    assert "loss_chnace" in str(error.value)
    assert "loss_chance" in str(error.value)


def test_effect_field_sets_cover_every_effect_type():
    """_validate_upgrades indexes ALLOWED_EFFECT_FIELDS by effect type right
    after the enum check, so a new type added to one and not the other is a
    KeyError at config load."""
    assert set(ALLOWED_EFFECT_FIELDS) == EFFECT_TYPES


def test_effect_fields_are_not_pooled_across_types(cfg):
    """A storage effect carrying `amount` would be silently ignored -- the
    storage branch reads capacity_bonus and shelf_life_multiplier only."""
    storage_effects = [u for u in cfg[1] if u["effect"]["type"] == "storage"]
    assert storage_effects, "fixture no longer contains a storage upgrade"
    storage_effects[0]["effect"]["amount"] = 2

    with pytest.raises(ValueError, match="unknown fields"):
        _validate(cfg)


# -- missing required keys ----------------------------------------------------

MISSING_KEY_CASES = {
    "crop-base_price": (lambda c: c[0][0].pop("base_price"), "base_price"),
    "crop-growth_days": (lambda c: c[0][0].pop("growth_days"), "growth_days"),
    "upgrade-cost": (lambda c: c[1][0].pop("cost"), "cost"),
    "upgrade-effect": (lambda c: c[1][0].pop("effect"), "effect"),
    "product-price": (
        lambda c: c[3]["processing"]["products"][0].pop("processed_base_price"),
        "processed_base_price",
    ),
    "recipe-input_quantity": (
        lambda c: c[3]["processing"]["recipes"][0].pop("input_quantity"),
        "input_quantity",
    ),
    "buyer-items": (lambda c: c[3]["buyers"][0].pop("items"), "items"),
    "buyer-deadline": (lambda c: c[3]["buyers"][0].pop("deadline_days"), "deadline_days"),
    "channel-capacity": (
        lambda c: c[3]["markets"]["channels"][0].pop("daily_capacity"),
        "daily_capacity",
    ),
    "season-rain_chance": (
        lambda c: c[3]["weather"]["seasons"]["spring"].pop("rain_chance"),
        "rain_chance",
    ),
    "world-buyers": (lambda c: c[3].pop("buyers"), "buyers"),
}


@pytest.mark.parametrize("case", sorted(MISSING_KEY_CASES))
def test_missing_required_keys_are_rejected(cfg, case):
    mutate, expected = MISSING_KEY_CASES[case]
    mutate(cfg)
    with pytest.raises(ValueError, match=expected):
        _validate(cfg)


# -- duplicate ids ------------------------------------------------------------


DUPLICATE_CASES = {
    "crop": (lambda c: c[0].append(deepcopy(c[0][0])), "crop"),
    "upgrade": (lambda c: c[1].append(deepcopy(c[1][0])), "upgrade"),
    "buyer": (lambda c: c[3]["buyers"].append(deepcopy(c[3]["buyers"][0])), "buyer"),
    "channel": (
        lambda c: c[3]["markets"]["channels"].append(deepcopy(c[3]["markets"]["channels"][0])),
        "market channel",
    ),
    "recipe": (
        lambda c: c[3]["processing"]["recipes"].append(deepcopy(c[3]["processing"]["recipes"][0])),
        "processing recipe",
    ),
    "product": (
        lambda c: c[3]["processing"]["products"].append(
            deepcopy(c[3]["processing"]["products"][0])
        ),
        "processing product",
    ),
}


@pytest.mark.parametrize("case", sorted(DUPLICATE_CASES))
def test_duplicate_ids_are_rejected(cfg, case):
    mutate, label = DUPLICATE_CASES[case]
    mutate(cfg)
    with pytest.raises(ValueError, match=f"Duplicate {label} id"):
        _validate(cfg)


def test_blank_id_is_rejected(cfg):
    cfg[0][0]["id"] = "   "
    with pytest.raises(ValueError, match="non-empty string id"):
        _validate(cfg)


# -- booleans and non-finite numbers -----------------------------------------
# `True == 1` and bool subclasses int, so an unguarded isinstance check lets
# `"capacity": true` through as capacity 1. NaN is worse: every comparison
# against it is False, so a range check passes and the arithmetic silently
# poisons every downstream total.

BAD_NUMBER_CASES = {
    "crop-seed_cost-bool": lambda c: c[0][0].update(seed_cost=True),
    "crop-growth_days-bool": lambda c: c[0][0].update(growth_days=True),
    "crop-base_price-nan": lambda c: c[0][0].update(base_price=float("nan")),
    "crop-base_price-inf": lambda c: c[0][0].update(base_price=float("inf")),
    "crop-loss_chance-string": lambda c: c[0][0].update(loss_chance="0.1"),
    "storage-capacity-bool": lambda c: c[3]["storage"].update(capacity=True),
    "storage-daily_cost-nan": lambda c: c[3]["storage"].update(daily_cost=float("nan")),
    "channel-daily_capacity-bool": lambda c: c[3]["markets"]["channels"][0].update(
        daily_capacity=True
    ),
    "channel-multiplier-inf": lambda c: c[3]["markets"]["channels"][0].update(
        price_multiplier=float("inf")
    ),
    "soil-dynamics-nan": lambda c: c[3]["soil"]["dynamics"].update(min_soil_health=float("nan")),
    "season-evaporation-nan": lambda c: c[3]["weather"]["seasons"]["spring"].update(
        evaporation=float("nan")
    ),
    "temperature_range-bool": lambda c: c[0][0].update(temperature_range=[True, 30]),
}


@pytest.mark.parametrize("case", sorted(BAD_NUMBER_CASES))
def test_booleans_and_non_finite_numbers_are_rejected(cfg, case):
    BAD_NUMBER_CASES[case](cfg)
    with pytest.raises(ValueError):
        _validate(cfg)


# -- boundaries ---------------------------------------------------------------
# Each pair pins both sides of one documented limit, so a change to the bound
# fails loudly instead of widening silently.

REJECTED_BOUNDARY_CASES = {
    "loss_chance-above-one": lambda c: c[0][0].update(loss_chance=1.01),
    "loss_chance-negative": lambda c: c[0][0].update(loss_chance=-0.01),
    "price_variation-above-one": lambda c: c[0][0].update(price_variation=1.01),
    "growth_days-zero": lambda c: c[0][0].update(growth_days=0),
    "water_interval-zero": lambda c: c[0][0].update(water_interval_days=0),
    "seed_cost-negative": lambda c: c[0][0].update(seed_cost=-1),
    "yield-range-reversed": lambda c: c[0][0].update(min_yield=9, max_yield=2),
    "ph_range-below-zero": lambda c: c[0][0].update(ph_range=[-1, 7]),
    "ph_range-above-fourteen": lambda c: c[0][0].update(ph_range=[7, 15]),
    "ph_range-reversed": lambda c: c[0][0].update(ph_range=[8, 6]),
    "temperature_range-reversed": lambda c: c[0][0].update(temperature_range=[30, 10]),
    "channel-capacity-zero": lambda c: c[3]["markets"]["channels"][0].update(daily_capacity=0),
    "storage-shelf_life-zero": lambda c: c[3]["storage"].update(shelf_life_multiplier=0),
    "contracts-interval-zero": lambda c: c[3]["contracts"].update(offer_interval_days=0),
    "safety_factor-above-one": lambda c: c[3]["contracts"].update(production_safety_factor=1.5),
    "buyer-quantity-zero": lambda c: c[3]["buyers"][0].update(quantity_range=[0, 5]),
    "season_length-zero": lambda c: c[3]["weather"].update(season_length_days=0),
    "soil-initial-ph-too-high": lambda c: c[3]["soil"]["initial"].update(ph=15),
    "soil-initial-moisture-above-one": lambda c: c[3]["soil"]["initial"].update(moisture=1.5),
    "soil-dynamics-out-of-range": lambda c: c[3]["soil"]["dynamics"].update(max_pest_pressure=2),
}

ACCEPTED_BOUNDARY_CASES = {
    "loss_chance-zero": lambda c: c[0][0].update(loss_chance=0),
    "loss_chance-one": lambda c: c[0][0].update(loss_chance=1),
    "price_variation-one": lambda c: c[0][0].update(price_variation=1),
    "seed_cost-zero": lambda c: c[0][0].update(seed_cost=0),
    "growth_days-one": lambda c: c[0][0].update(growth_days=1),
    "min_yield-zero": lambda c: c[0][0].update(min_yield=0),
    "yield-range-equal": lambda c: c[0][0].update(min_yield=3, max_yield=3),
    "ph_range-full": lambda c: c[0][0].update(ph_range=[0, 14]),
    "storage-capacity-zero": lambda c: c[3]["storage"].update(capacity=0),
    "channel-capacity-one": lambda c: c[3]["markets"]["channels"][0].update(daily_capacity=1),
    "soil-initial-ph-zero": lambda c: c[3]["soil"]["initial"].update(ph=0),
    "soil-initial-ph-fourteen": lambda c: c[3]["soil"]["initial"].update(ph=14),
}


@pytest.mark.parametrize("case", sorted(REJECTED_BOUNDARY_CASES))
def test_values_outside_their_documented_range_are_rejected(cfg, case):
    REJECTED_BOUNDARY_CASES[case](cfg)
    with pytest.raises(ValueError):
        _validate(cfg)


@pytest.mark.parametrize("case", sorted(ACCEPTED_BOUNDARY_CASES))
def test_values_on_their_documented_boundary_are_accepted(cfg, case):
    ACCEPTED_BOUNDARY_CASES[case](cfg)
    _validate(cfg)


@pytest.mark.parametrize("key", sorted(SOIL_DYNAMICS_BOUNDS))
def test_every_soil_dynamics_key_is_range_checked(cfg, key):
    """SOIL_DYNAMICS_BOUNDS is the only description of these limits; an entry
    added there without a matching check would be silently unbounded."""
    minimum, maximum = SOIL_DYNAMICS_BOUNDS[key]
    cfg[3]["soil"]["dynamics"][key] = minimum - 1

    with pytest.raises(ValueError, match=key):
        _validate(cfg)


# -- cross-references ---------------------------------------------------------

CROSS_REFERENCE_CASES = {
    "recipe-input": (
        lambda c: c[3]["processing"]["recipes"][0].update(input_item_id="nonesuch"),
        "unknown item",
    ),
    "recipe-output": (
        lambda c: c[3]["processing"]["recipes"][0].update(output_item_id="nonesuch"),
        "unknown item",
    ),
    "buyer-items": (
        lambda c: c[3]["buyers"][0].update(items=["nonesuch"]),
        "unknown items",
    ),
    "crop-unlock-upgrade": (
        lambda c: c[0][2].update(unlock_requirement={"type": "upgrade", "id": "nonesuch"}),
        "unknown upgrade",
    ),
    "crop-unlock-type": (
        lambda c: c[0][2].update(unlock_requirement={"type": "vibes", "value": 1}),
        "unlock_requirement.type",
    ),
    "channel-quality": (
        lambda c: c[3]["markets"]["channels"][0].update(min_quality="excellent"),
        "min_quality",
    ),
    "recipe-quality": (
        lambda c: c[3]["processing"]["recipes"][0].update(min_quality="excellent"),
        "min_quality",
    ),
    "effect-type": (
        lambda c: c[1][0]["effect"].update(type="teleportation"),
        "effect.type",
    ),
}


@pytest.mark.parametrize("case", sorted(CROSS_REFERENCE_CASES))
def test_dangling_references_are_rejected(cfg, case):
    mutate, expected = CROSS_REFERENCE_CASES[case]
    mutate(cfg)
    with pytest.raises(ValueError, match=expected):
        _validate(cfg)


def test_crop_and_product_ids_may_not_collide(cfg):
    """Both are looked up in one item index, so a shared id makes every
    inventory lookup ambiguous."""
    cfg[3]["processing"]["products"][0]["id"] = cfg[0][0]["id"]
    with pytest.raises(ValueError, match="must be unique"):
        _validate(cfg)


def test_markets_must_define_a_spot_channel(cfg):
    channels = cfg[3]["markets"]["channels"]
    cfg[3]["markets"]["channels"] = [c for c in channels if c["id"] != "spot"]
    with pytest.raises(ValueError, match="spot"):
        _validate(cfg)


# -- simulation settings ------------------------------------------------------

BAD_SETTINGS = {
    "days-zero": ({"days": 0}, "days"),
    "days-bool": ({"days": True}, "days"),
    "days-float": ({"days": 1.5}, "days"),
    "start_slots-zero": ({"start_slots": 0}, "start_slots"),
    "start_money-negative": ({"start_money": -1}, "start_money"),
    "start_money-nan": ({"start_money": float("nan")}, "start_money"),
    "reserve-negative": ({"operating_reserve": -1}, "operating_reserve"),
    "seed-float": ({"seed": 1.5}, "seed"),
    "seed-bool": ({"seed": True}, "seed"),
}


@pytest.mark.parametrize("case", sorted(BAD_SETTINGS))
def test_bad_simulation_settings_are_rejected(shipped, case):
    override, expected = BAD_SETTINGS[case]
    config = dict(shipped[2], **override)
    with pytest.raises(ValueError, match=expected):
        validate_simulation_config(config)


def test_seed_may_be_null(shipped):
    validate_simulation_config(dict(shipped[2], seed=None))
