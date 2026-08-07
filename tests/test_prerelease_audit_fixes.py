"""Regression tests for the pre-release simulation audit fixes (F1-F10).

Each test names the invariant it pins rather than the symptom, so a future
change that reintroduces the defect fails for a readable reason. See
docs/design/2026-08-06-prerelease-simulation-audit.md.
"""

import pytest

from main import load_config
from simulation import (
    contracts,
    crop_growth,
    derived,
    engine,
    inventory,
    markets,
    weather,
)
from simulation.configuration import validate
from simulation.state import (
    ContractState,
    InventoryLot,
    PlantedCrop,
    PlayerState,
    PlotState,
    ProcessingJob,
)

CROP = {
    "id": "c",
    "name": "C",
    "seed_cost": 5,
    "growth_days": 3,
    "min_yield": 1,
    "max_yield": 2,
    "base_price": 10,
    "price_variation": 0.1,
    "loss_chance": 0.0,
    "water_interval_days": 3,
}


# --- F1: evaporation applies to every plot, occupied or not ----------------


def test_fallow_plot_evaporates_like_a_planted_one():
    """Evaporation is a property of the day's weather, not of occupancy.

    Previously it was only applied inside update_crop_stress, which runs
    only for planted plots, so a fallow plot took on rainfall and never gave
    any back -- it saturated at 1.0 and handed the next crop planted there
    free stress-free days.
    """
    day_weather = {"season": "spring", "temperature": 20, "rainfall": 0.10, "evaporation": 0.08}

    fallow = PlayerState(money=100, slots_total=1)
    fallow.plots[0].moisture = 0.30
    for day in range(10):
        fallow.day = day
        weather.apply_weather(fallow, {}, day_weather, crop_growth, None, {})

    crops_by_id = {"c": dict(CROP, growth_days=50, nutrient_demand={})}
    planted_player = PlayerState(money=100, slots_total=1)
    planted_player.plots[0].moisture = 0.30
    crop = PlantedCrop("c", 0, 50, plot_index=0)
    planted_player.planted.append(crop)
    planted_player.plots[0].crop = crop
    for day in range(10):
        planted_player.day = day
        weather.apply_weather(planted_player, crops_by_id, day_weather, crop_growth, None, {})

    assert fallow.plots[0].moisture == pytest.approx(planted_player.plots[0].moisture)
    assert fallow.plots[0].moisture < 1.0


def test_fallow_plot_moisture_is_floored_at_zero():
    dry = {"rainfall": 0.0, "evaporation": 0.5}
    player = PlayerState(money=100, slots_total=1)
    player.plots[0].moisture = 0.2
    for _ in range(5):
        weather.apply_weather(player, {}, dry, crop_growth, None, {})
    assert player.plots[0].moisture == 0.0


# --- F2: held seed is used, and a failed planting cannot drain cash --------


class _AlwaysPlant:
    name = "always"
    watering_diligence = 1.0

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        return CROP

    def should_buy_upgrade(self, player, upgrade):
        return False

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return False


def test_failed_planting_does_not_drain_cash_into_unplantable_seed():
    """The loop must exit on a failed plant, not retry until money runs out.

    slots_total exceeds the number of real plots, so plant_seed can never
    find a free plot. Previously the loop re-entered with the slot still
    open and bought another seed each pass, converting the whole balance
    into seed that nothing in the simulation ever consumes.
    """
    player = PlayerState(money=100.0, slots_total=3, plots=[PlotState()])
    engine._plant_open_slots(player, _AlwaysPlant(), [CROP], {"c": CROP}, {})

    assert len(player.planted) == 1
    assert player.money == 95.0
    assert sum(player.seed_inventory.values()) == 0


def test_planting_consumes_a_held_seed_instead_of_buying_another():
    player = PlayerState(money=0.0, slots_total=1)
    player.seed_inventory["c"] = 1

    engine._plant_open_slots(player, _AlwaysPlant(), [CROP], {"c": CROP}, {})

    assert len(player.planted) == 1
    assert player.money == 0.0
    assert player.seed_inventory["c"] == 0


def test_held_seed_counts_as_fundable_for_crop_observations():
    broke = PlayerState(money=0.0, slots_total=1)
    assert not engine._can_fund_seed(broke, CROP)
    broke.seed_inventory["c"] = 1
    assert engine._can_fund_seed(broke, CROP)


# --- F3: an expiry penalty never credits the farm -------------------------


def test_expired_contract_penalty_never_credits_a_negative_balance():
    player = PlayerState(money=-50.0, slots_total=1)
    player.day = 20
    player.active_contracts.append(
        ContractState(
            id="x",
            buyer_id="b",
            item_id="i",
            quantity=10,
            min_quality="standard",
            unit_price=5.0,
            offered_day=0,
            deadline_day=10,
            penalty_rate=0.5,
        )
    )

    contracts.resolve_expired(player)

    assert player.money == -50.0
    assert player.contract_penalties == 0.0
    assert player.expenses_by_category.get("contract_penalties", 0.0) == 0.0


def test_expiry_penalty_is_still_bounded_by_cash_on_hand():
    player = PlayerState(money=4.0, slots_total=1)
    player.day = 20
    player.active_contracts.append(
        ContractState(
            id="x",
            buyer_id="b",
            item_id="i",
            quantity=10,
            min_quality="standard",
            unit_price=5.0,
            offered_day=0,
            deadline_day=10,
            penalty_rate=0.5,
        )
    )

    contracts.resolve_expired(player)

    assert player.money == 0.0
    assert player.contract_penalties == 4.0
    assert player.expenses_by_category["contract_penalties"] == 4.0


# --- F4: fertilizer is bounded, and its quality bonus is configurable ------


def test_fertilizer_yield_bonus_cannot_escape_the_multiplier_cap():
    planted = PlantedCrop(crop_id="c", day_planted=0, growth_days_required=1, fertilized=True)
    watering = {
        "neglect_loss_chance_penalty_per_day": 0.05,
        "neglect_yield_penalty_per_day": 0.08,
        "max_neglect_loss_chance_bonus": 0.6,
        "max_neglect_yield_penalty": 0.8,
    }
    fertilizer = {"cost": 8, "yield_bonus_pct": 5.0, "loss_chance_reduction": 0.0}

    class FixedRng:
        def roll_loss(self, _chance):
            return False

        def roll_yield(self, _minimum, _maximum):
            return 100

    _lost, amount = crop_growth.compute_harvest_outcome(
        planted, CROP, watering, fertilizer, FixedRng()
    )

    assert amount == 100 * crop_growth.YIELD_MULTIPLIER_BOUNDS[1]


def test_fertilizer_quality_bonus_is_read_from_configuration():
    plain = PlantedCrop(crop_id="c", day_planted=0, growth_days_required=1)
    fertilized = PlantedCrop(crop_id="c", day_planted=0, growth_days_required=1, fertilized=True)

    _y, plain_quality = crop_growth.harvest_multipliers(plain, CROP)
    _y, default_quality = crop_growth.harvest_multipliers(fertilized, CROP)
    _y, configured_quality = crop_growth.harvest_multipliers(
        fertilized, CROP, None, {"quality_bonus": 0.2}
    )

    assert default_quality == pytest.approx(
        plain_quality + crop_growth.DEFAULT_FERTILIZER_QUALITY_BONUS
    )
    assert configured_quality == pytest.approx(plain_quality + 0.2)


# --- F5: resolved contracts do not accumulate -----------------------------


def test_resolved_contracts_are_pruned_from_active_contracts():
    player = PlayerState(money=1000.0, slots_total=1)
    player.day = 20
    for index in range(3):
        player.active_contracts.append(
            ContractState(
                id=f"c{index}",
                buyer_id="b",
                item_id="i",
                quantity=1,
                min_quality="standard",
                unit_price=1.0,
                offered_day=0,
                deadline_day=10,
                penalty_rate=0.1,
            )
        )

    contracts.resolve_expired(player)

    assert player.active_contracts == []
    assert player.contracts_failed == 3


def test_pruning_keeps_contracts_that_are_still_outstanding():
    player = PlayerState(money=1000.0, slots_total=1)
    player.day = 5
    live = ContractState(
        id="live",
        buyer_id="b",
        item_id="i",
        quantity=1,
        min_quality="standard",
        unit_price=1.0,
        offered_day=0,
        deadline_day=30,
        penalty_rate=0.1,
    )
    player.active_contracts.append(live)

    contracts.resolve_expired(player)

    assert player.active_contracts == [live]
    assert player.contracts_failed == 0


# --- F6/F10: derived caches key on their config, and fold in stable order --


def test_effective_storage_does_not_return_another_configs_cached_value():
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})

    first = lookups.effective_storage({"capacity": 100}, set(), {})
    second = lookups.effective_storage({"capacity": 999, "daily_cost": 7}, set(), {})

    assert first["capacity"] == 100
    assert second["capacity"] == 999
    assert second["daily_cost"] == 7


def test_processing_capacity_does_not_return_another_configs_cached_value():
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})

    assert lookups.processing_capacity({"base_capacity": 2}, set(), {}) == 2
    assert lookups.processing_capacity({"base_capacity": 50}, set(), {}) == 50


def test_processing_capacity_fold_is_independent_of_set_ordering():
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})
    upgrades_by_id = {
        "a": {"effect": {"type": "processing_capacity", "amount": 1}},
        "b": {"effect": {"type": "processing_capacity", "amount": 2}},
    }
    config = {"base_capacity": 1}

    forward = lookups.processing_capacity(config, {"a", "b"}, upgrades_by_id)
    backward = derived.world_lookups(_minimal_world(), {}).processing_capacity(
        config, {"b", "a"}, upgrades_by_id
    )

    assert forward == backward == 4


def _minimal_world() -> dict:
    return {
        "watering": {},
        "fertilizer": {},
        "storage": {"capacity": 100, "shelf_life_multiplier": 1.0, "daily_cost": 1.0},
        "weather": {},
        "markets": {"channels": [], "default_variation": 0.12},
        "contracts": {},
        "buyers": [],
        "processing": {"base_capacity": 2, "products": [], "recipes": []},
        "soil": {},
    }


# --- F7: loss causes name their unit of measure ---------------------------


def test_loss_cause_keys_name_their_measure():
    player = PlayerState(money=10, slots_total=1)
    player.inventory_lots.append(
        InventoryLot(item_id="c", quantity=4, produced_day=0, shelf_life_days=1)
    )
    player.day = 5

    inventory.age_and_spoil(player, {"capacity": 100, "shelf_life_multiplier": 1.0}, False)

    assert player.losses_by_cause == {"spoilage_units": 4}
    assert "spoilage" not in player.losses_by_cause


# --- F8: plot dynamics are configuration, not constants -------------------


def test_soil_dynamics_defaults_match_previous_hard_coded_constants():
    defaults = derived.SoilDynamics()
    assert defaults.harvest_soil_health_cost == 0.02
    assert defaults.same_family_yield_penalty == 0.85
    assert defaults.same_family_quality_penalty == 0.9
    assert defaults.soil_health_yield_floor == 0.85
    assert defaults.soil_health_yield_span == 0.25
    assert defaults.max_pest_pressure == 0.8


def test_configured_soil_dynamics_override_the_defaults():
    dynamics = derived.SoilDynamics({"dynamics": {"same_family_yield_penalty": 0.5}})
    assert dynamics.same_family_yield_penalty == 0.5
    assert dynamics.same_family_quality_penalty == 0.9


def test_same_family_penalty_is_driven_by_configuration():
    plot = PlotState(previous_crop_family="leafy", soil_health=1.0)
    planted = PlantedCrop(crop_id="c", day_planted=0, growth_days_required=1)
    crop = dict(CROP, family="leafy")

    default_yield, _q = crop_growth.harvest_multipliers(planted, crop, plot)
    relaxed_yield, _q = crop_growth.harvest_multipliers(
        planted,
        crop,
        plot,
        None,
        derived.SoilDynamics({"dynamics": {"same_family_yield_penalty": 1.0}}),
    )

    assert relaxed_yield > default_yield


def test_shipped_soil_dynamics_configuration_validates():
    crops, upgrades, _config, world = load_config()
    validate(crops, upgrades, world)
    assert world["soil"]["dynamics"]["harvest_soil_health_cost"] == 0.02


def test_unknown_soil_dynamics_field_is_rejected():
    crops, upgrades, _config, world = load_config()
    world = {**world, "soil": {**world["soil"], "dynamics": {"not_a_real_knob": 1}}}
    with pytest.raises(ValueError, match="soil.dynamics contains unknown fields"):
        validate(crops, upgrades, world)


def test_soil_dynamics_bounds_cover_every_default():
    from simulation.configuration import SOIL_DYNAMICS_BOUNDS

    assert set(SOIL_DYNAMICS_BOUNDS) == set(derived.DEFAULT_SOIL_DYNAMICS)


# --- F9: production cost and processing revenue are real numbers ----------


def test_harvest_lot_unit_cost_includes_care_spend_not_just_seed():
    player = PlayerState(money=100.0, slots_total=1)
    fertilizer = {"cost": 8, "yield_bonus_pct": 0.0, "loss_chance_reduction": 0.0}
    watering = {"cost_per_plot": 2.0, "moisture_added": 0.4}

    from simulation import actions

    actions.buy_seeds(player, CROP, 1)
    actions.buy_fertilizer(player, fertilizer, 1)
    actions.plant_seed(player, CROP, 1, fertilized=True, fertilizer_config=fertilizer)
    actions.water_crop(player, player.planted[0], watering)

    assert player.planted[0].accrued_cost == pytest.approx(5 + 8 + 2.0)


def test_selling_a_product_lot_records_processing_revenue():
    player = PlayerState(money=0.0, slots_total=1)
    player.market_prices = {"flour": 10.0}
    player.inventory_lots.append(
        InventoryLot(item_id="flour", quantity=3, quality="standard", item_type="product")
    )
    channel = {"id": "spot", "price_multiplier": 1.0, "daily_capacity": 100}

    revenue, sold = markets.sell(player, "flour", 3, channel)

    assert sold == 3
    assert player.processing_revenue == pytest.approx(revenue)


def test_selling_a_crop_lot_does_not_record_processing_revenue():
    player = PlayerState(money=0.0, slots_total=1)
    player.market_prices = {"c": 10.0}
    player.inventory_lots.append(InventoryLot(item_id="c", quantity=3, quality="standard"))
    channel = {"id": "spot", "price_multiplier": 1.0, "daily_capacity": 100}

    markets.sell(player, "c", 3, channel)

    assert player.processing_revenue == 0.0


def test_processing_job_output_cost_reflects_real_input_cost():
    from simulation import processing

    player = PlayerState(money=100.0, slots_total=1)
    player.inventory_lots.append(
        InventoryLot(item_id="c", quantity=2, quality="standard", unit_cost=7.0)
    )
    recipe = {
        "id": "r",
        "input_item_id": "c",
        "output_item_id": "flour",
        "input_quantity": 2,
        "output_quantity": 1,
        "cost": 3.0,
        "processing_days": 1,
        "min_quality": "processing",
    }

    assert processing.start_job(player, recipe, 1, capacity=5)
    job: ProcessingJob = player.processing_jobs[0]
    assert job.unit_cost == pytest.approx((14.0 + 3.0) / 1)
