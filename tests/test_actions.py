from simulation import actions, crop_growth
from simulation.random_events import RandomEvents
from simulation.state import PlantedCrop


def test_buy_seeds_deducts_money_and_adds_inventory(player, fast_crop):
    ok = actions.buy_seeds(player, fast_crop, quantity=2)
    assert ok
    assert player.money == 90
    assert player.total_expenses == 10
    assert player.seed_inventory["fast"] == 2


def test_buy_seeds_fails_when_too_expensive(player, premium_crop):
    player.money = 10
    ok = actions.buy_seeds(player, premium_crop, quantity=1)
    assert not ok
    assert player.money == 10
    assert "premium" not in player.seed_inventory


def test_plant_seed_fails_without_seed_in_inventory(player, fast_crop):
    ok = actions.plant_seed(player, fast_crop, growth_days=3)
    assert not ok
    assert len(player.planted) == 0


def test_plant_seed_fails_without_open_slot(player, fast_crop):
    player.slots_total = 0
    actions.buy_seeds(player, fast_crop, 1)
    ok = actions.plant_seed(player, fast_crop, growth_days=3)
    assert not ok


def test_plant_seed_consumes_seed_and_occupies_slot(player, fast_crop):
    actions.buy_seeds(player, fast_crop, 1)
    ok = actions.plant_seed(player, fast_crop, growth_days=3)
    assert ok
    assert player.seed_inventory["fast"] == 0
    assert len(player.planted) == 1
    assert player.total_planted == 1
    assert player.crop_plant_counts["fast"] == 1


def test_harvest_mature_adds_yield_to_inventory(
    player, fast_crop, watering_settings, fertilizer_config
):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    actions.buy_seeds(player, fast_crop, 1)
    actions.plant_seed(player, fast_crop, growth_days=3)
    player.day = 3
    harvested = actions.harvest_mature(
        player, crops_by_id, watering_settings, fertilizer_config, rng
    )
    assert harvested
    assert player.crop_inventory["fast"] >= fast_crop["min_yield"]
    assert len(player.planted) == 0
    assert player.total_harvest_events == 1
    assert player.total_crops_lost == 0


def test_harvest_mature_leaves_immature_crops_planted(
    player, fast_crop, watering_settings, fertilizer_config
):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    actions.buy_seeds(player, fast_crop, 1)
    actions.plant_seed(player, fast_crop, growth_days=3)
    player.day = 1
    harvested = actions.harvest_mature(
        player, crops_by_id, watering_settings, fertilizer_config, rng
    )
    assert not harvested
    assert len(player.planted) == 1


def test_harvest_with_neglect_reduces_yield_and_raises_loss_chance(
    player, fast_crop, watering_settings, fertilizer_config
):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    actions.buy_seeds(player, fast_crop, 1)
    actions.plant_seed(player, fast_crop, growth_days=3)
    player.day = 3
    player.planted[0].neglect_days = 5  # heavily overdue on watering
    actions.harvest_mature(player, crops_by_id, watering_settings, fertilizer_config, rng)
    assert player.total_harvest_events == 1
    # either lost outright, or the surviving yield reflects the capped neglect penalty
    if player.total_crops_lost == 0:
        max_possible_yield = fast_crop["max_yield"] * (
            1 - watering_settings["max_neglect_yield_penalty"]
        )
        assert player.crop_inventory.get("fast", 0) <= round(max_possible_yield) + 1


def test_fertilized_crop_never_receives_neglect_penalty_when_never_neglected(
    player, standard_crop, watering_settings, fertilizer_config
):
    crops_by_id = {"standard": standard_crop}
    rng = RandomEvents(seed=2)
    actions.buy_seeds(player, standard_crop, 1)
    actions.buy_fertilizer(player, fertilizer_config, 1)
    actions.plant_seed(player, standard_crop, growth_days=7, fertilized=True)
    player.day = 7
    actions.harvest_mature(player, crops_by_id, watering_settings, fertilizer_config, rng)
    if player.total_crops_lost == 0:
        # fertilizer's yield bonus means a fertilized crop can exceed the crop's raw max_yield
        assert player.crop_inventory.get("standard", 0) <= round(standard_crop["max_yield"] * 1.25)


def test_configured_yield_effects_are_applied_once_and_allow_zero(player, fast_crop):
    class FixedYieldRng:
        def roll_loss(self, _chance):
            return False

        def roll_yield(self, _minimum, _maximum):
            return 100

    planted = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=1, fertilized=True)
    watering = {
        "neglect_loss_chance_penalty_per_day": 0,
        "neglect_yield_penalty_per_day": 0,
        "max_neglect_loss_chance_bonus": 0,
        "max_neglect_yield_penalty": 0,
    }
    fertilizer = {"yield_bonus_pct": 0, "loss_chance_reduction": 0}

    lost, amount = crop_growth.compute_harvest_outcome(
        planted, fast_crop, watering, fertilizer, FixedYieldRng()
    )

    assert not lost
    assert amount == 100

    fertilizer["yield_bonus_pct"] = 0.10
    _lost, amount = crop_growth.compute_harvest_outcome(
        planted, fast_crop, watering, fertilizer, FixedYieldRng()
    )
    assert amount == 110


def test_fertilizer_quality_bonus_is_independent_of_yield_bonus(fast_crop):
    plain = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=1)
    fertilized = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=1, fertilized=True)

    plain_yield, plain_quality = crop_growth.harvest_multipliers(plain, fast_crop)
    fertilized_yield, fertilized_quality = crop_growth.harvest_multipliers(fertilized, fast_crop)

    assert fertilized_yield == plain_yield
    assert fertilized_quality == plain_quality + 0.05


def test_configured_neglect_yield_penalty_is_applied_once(player, fast_crop):
    class FixedYieldRng:
        def roll_loss(self, _chance):
            return False

        def roll_yield(self, _minimum, _maximum):
            return 100

    planted = PlantedCrop(crop_id="fast", day_planted=0, growth_days_required=1)
    planted.neglect_days = 2
    watering = {
        "neglect_loss_chance_penalty_per_day": 0,
        "neglect_yield_penalty_per_day": 0.10,
        "max_neglect_loss_chance_bonus": 0,
        "max_neglect_yield_penalty": 0.80,
    }
    fertilizer = {"yield_bonus_pct": 0, "loss_chance_reduction": 0}

    _lost, amount = crop_growth.compute_harvest_outcome(
        planted, fast_crop, watering, fertilizer, FixedYieldRng()
    )

    assert amount == 80


def test_water_farm_resets_neglect_when_watered(player, fast_crop):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    actions.buy_seeds(player, fast_crop, 1)
    actions.plant_seed(player, fast_crop, growth_days=10)
    player.planted[0].neglect_days = 3

    class AlwaysWaters:
        watering_diligence = 1.0

    watered = actions.water_farm(player, AlwaysWaters(), crops_by_id, rng)
    assert watered
    assert player.planted[0].neglect_days == 0
    assert player.planted[0].last_watered_day == player.day
    assert player.total_waterings == 1


def test_water_farm_accrues_neglect_when_overdue_and_not_watered(player, fast_crop):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    actions.buy_seeds(player, fast_crop, 1)
    actions.plant_seed(player, fast_crop, growth_days=10)
    player.day = fast_crop["water_interval_days"] + 1  # now overdue

    class NeverWaters:
        watering_diligence = 0.0

    watered = actions.water_farm(player, NeverWaters(), crops_by_id, rng)
    assert not watered
    assert player.planted[0].neglect_days == 1
    assert player.total_waterings == 0


def test_buy_fertilizer_deducts_money_and_adds_inventory(player, fertilizer_config):
    ok = actions.buy_fertilizer(player, fertilizer_config, quantity=2)
    assert ok
    assert player.money == 100 - fertilizer_config["cost"] * 2
    assert player.fertilizer_inventory == 2
    assert player.total_fertilizer_bought == 2


def test_plant_seed_with_fertilizer_consumes_fertilizer_inventory(
    player, fast_crop, fertilizer_config
):
    actions.buy_seeds(player, fast_crop, 1)
    actions.buy_fertilizer(player, fertilizer_config, 1)
    ok = actions.plant_seed(player, fast_crop, growth_days=3, fertilized=True)
    assert ok
    assert player.fertilizer_inventory == 0
    assert player.total_fertilizer_applied == 1
    assert player.planted[0].fertilized is True


def test_plant_seed_fails_when_fertilizer_requested_but_unavailable(player, fast_crop):
    actions.buy_seeds(player, fast_crop, 1)
    ok = actions.plant_seed(player, fast_crop, growth_days=3, fertilized=True)
    assert not ok
    assert len(player.planted) == 0


def test_sell_all_clears_inventory_and_adds_revenue(player, fast_crop):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    player.crop_inventory["fast"] = 5
    revenue, sold = actions.sell_all(player, crops_by_id, rng)
    assert sold == 5
    assert revenue > 0
    assert player.crop_inventory.get("fast", 0) == 0
    assert player.total_sold == 5
    assert player.total_revenue == revenue
    assert player.money == 100 + revenue


def test_sell_all_is_noop_on_empty_inventory(player, fast_crop):
    crops_by_id = {"fast": fast_crop}
    rng = RandomEvents(seed=1)
    revenue, sold = actions.sell_all(player, crops_by_id, rng)
    assert revenue == 0
    assert sold == 0
    assert player.money == 100


def test_buy_upgrade_applies_capacity_effect(player, capacity_upgrade):
    player.money = 200
    ok = actions.buy_upgrade(player, capacity_upgrade)
    assert ok
    assert player.money == 200 - capacity_upgrade["cost"]
    assert player.total_expenses == capacity_upgrade["cost"]
    assert player.slots_total == 4
    assert "capacity_1" in player.upgrades_owned
    assert player.upgrade_purchase_days["capacity_1"] == 0


def test_buy_upgrade_fails_when_unaffordable(player, capacity_upgrade):
    player.money = 10
    ok = actions.buy_upgrade(player, capacity_upgrade)
    assert not ok
    assert "capacity_1" not in player.upgrades_owned


def test_buy_upgrade_fails_when_already_owned(player, capacity_upgrade):
    player.money = 1000
    actions.buy_upgrade(player, capacity_upgrade)
    slots_after_first = player.slots_total
    ok = actions.buy_upgrade(player, capacity_upgrade)
    assert not ok
    assert player.slots_total == slots_after_first
