import pytest

from simulation import economy_rules


def test_crop_with_no_unlock_requirement_is_always_unlocked(player, fast_crop):
    assert economy_rules.is_crop_unlocked(fast_crop, player)


def test_crop_locked_until_revenue_threshold_met(player, premium_crop):
    player.total_revenue = 50
    assert not economy_rules.is_crop_unlocked(premium_crop, player)
    player.total_revenue = 150
    assert economy_rules.is_crop_unlocked(premium_crop, player)


def test_effective_growth_days_unaffected_without_upgrade(player, fast_crop, efficiency_upgrade):
    upgrades_by_id = {"efficiency_1": efficiency_upgrade}
    assert economy_rules.effective_growth_days(fast_crop, player, upgrades_by_id) == 3


def test_effective_growth_days_reduced_with_upgrade(player, fast_crop, efficiency_upgrade):
    player.upgrades_owned.add("efficiency_1")
    upgrades_by_id = {"efficiency_1": efficiency_upgrade}
    # 3 days * (1 - 0.20) = 2.4 -> rounds to 2
    assert economy_rules.effective_growth_days(fast_crop, player, upgrades_by_id) == 2


def test_effective_growth_days_never_below_one(player, fast_crop):
    tiny_crop = dict(fast_crop, growth_days=1)
    huge_reduction = {"id": "e", "effect": {"type": "growth_time_reduction", "amount": 0.99}}
    player.upgrades_owned.add("e")
    upgrades_by_id = {"e": huge_reduction}
    assert economy_rules.effective_growth_days(tiny_crop, player, upgrades_by_id) >= 1


def test_effective_growth_days_uses_upgrade_configuration_order(player, fast_crop):
    first = {"id": "first", "effect": {"type": "growth_time_reduction", "amount": 0.15}}
    second = {"id": "second", "effect": {"type": "growth_time_reduction", "amount": 0.20}}
    player.upgrades_owned.update(("first", "second"))

    assert economy_rules.effective_growth_days(
        dict(fast_crop, growth_days=10),
        player,
        {"first": first, "second": second},
    ) == 6


def test_expected_profit_per_day_is_positive_for_profitable_crop(player, standard_crop):
    profit = economy_rules.expected_profit_per_day(standard_crop, player, {})
    assert profit > 0


# -- upgrade-purchase budget gate -------------------------------------------

def test_upgrade_payback_days_none_without_crop_catalog(player, capacity_upgrade):
    assert economy_rules.upgrade_payback_days(capacity_upgrade, player, {}, {}) is None


def test_upgrade_payback_days_prices_capacity_upgrade(player, standard_crop, capacity_upgrade):
    crops_by_id = {"standard": standard_crop}
    payback = economy_rules.upgrade_payback_days(capacity_upgrade, player, crops_by_id, {})
    nominal = economy_rules.expected_profit_per_day(standard_crop, player, {})
    expected = capacity_upgrade["cost"] / (nominal * capacity_upgrade["effect"]["amount"])
    assert payback == pytest.approx(expected)


def test_should_buy_upgrade_within_budget_blocks_purchase_cooldown(player, capacity_upgrade):
    player.money = 1000
    player.highest_money = 1000
    player.upgrade_purchase_days = {"efficiency_1": player.day}

    assert not economy_rules.should_buy_upgrade_within_budget(player, capacity_upgrade)

    player.day += 10
    assert economy_rules.should_buy_upgrade_within_budget(player, capacity_upgrade)


def test_should_buy_upgrade_within_budget_blocks_over_cumulative_cap(player, capacity_upgrade):
    player.money = 200
    player.highest_money = 200
    # Already spent 100 on upgrades; +120 more would exceed 60% of the peak
    # cash (200 * 0.6 = 120), even though the reserve check alone passes.
    player.expenses_by_category["upgrades"] = 100

    assert not economy_rules.should_buy_upgrade_within_budget(player, capacity_upgrade)


def test_should_buy_upgrade_within_budget_allows_when_clear(player, capacity_upgrade):
    player.money = 1000
    player.highest_money = 1000
    assert economy_rules.should_buy_upgrade_within_budget(player, capacity_upgrade)


# -- crop-selection reserve ladder -------------------------------------------

def test_crop_seed_reserve_gate_relaxes_with_fraction(player, standard_crop):
    player.money = 68
    player.operating_reserve = 100

    assert not economy_rules.crop_seed_reserve_gate(standard_crop, player, 1.0)
    assert economy_rules.crop_seed_reserve_gate(standard_crop, player, 0.5)


def test_choose_crop_with_relaxed_reserve_returns_none_below_loosest_tier(player, standard_crop):
    player.money = 10
    player.operating_reserve = 100
    assert economy_rules.choose_crop_with_relaxed_reserve([standard_crop], player, {}) is None


# -- soil-quality-aware ranking -----------------------------------------------

def test_soil_health_factor_defaults_healthy_without_plots():
    from simulation.state import PlayerState
    empty = PlayerState(money=10, slots_total=0)
    assert economy_rules.soil_health_factor(empty) == 1.0


def test_quality_adjusted_profit_matches_nominal_at_full_soil_health(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 1.0
    crop = {
        "id": "demanding", "seed_cost": 45, "growth_days": 12,
        "min_yield": 5, "max_yield": 10, "base_price": 16, "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
        "family": "flowering",
    }
    nominal = economy_rules.expected_profit_per_day(crop, player, {})
    adjusted = economy_rules.quality_adjusted_profit_per_day(crop, player, {})
    assert adjusted == pytest.approx(nominal)


def test_quality_adjusted_profit_discounted_when_soil_depleted(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    crop = {
        "id": "demanding", "seed_cost": 45, "growth_days": 12,
        "min_yield": 5, "max_yield": 10, "base_price": 16, "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
        "family": "flowering",
    }
    nominal = economy_rules.expected_profit_per_day(crop, player, {})
    adjusted = economy_rules.quality_adjusted_profit_per_day(crop, player, {})
    assert adjusted < nominal


def test_soil_quality_risk_favors_low_demand_crop_when_depleted(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    low_demand = {"nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.01, "potassium": 0.01}, "family": "leafy"}
    high_demand = {"nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035}, "family": "flowering"}
    assert economy_rules.soil_quality_risk(low_demand, player) < economy_rules.soil_quality_risk(high_demand, player)


def test_best_crop_by_expected_profit_triages_to_lowest_demand_when_soil_critical(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    low_demand = {
        "id": "low", "seed_cost": 5, "growth_days": 3, "min_yield": 1, "max_yield": 2,
        "base_price": 5, "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.01, "potassium": 0.01},
    }
    high_demand = {
        "id": "high", "seed_cost": 45, "growth_days": 12, "min_yield": 5, "max_yield": 10,
        "base_price": 16, "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
    }
    # Nominal EV would favor "high" (higher price/yield); soil is fully
    # depleted (well below CRITICAL_SOIL_HEALTH), so triage should override
    # that and pick the gentlest crop instead.
    chosen = economy_rules.best_crop_by_expected_profit([low_demand, high_demand], player, {})
    assert chosen["id"] == "low"


def test_best_crop_by_expected_profit_normalizes_omitted_demand_when_critical(player):
    """Regression for issue #25: a crop that omits `nutrient_demand` must not
    be treated as demand-0 (and so automatically "gentlest") -- it should be
    normalized to the same runtime default CropProfile uses (total 0.04:
    nitrogen 0.02 + phosphorus 0.01 + potassium 0.01), same as
    simulation.crop_growth's actual nutrient consumption.
    """
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    omitted_demand = {
        "id": "omitted", "seed_cost": 5, "growth_days": 3, "min_yield": 1, "max_yield": 2,
        "base_price": 5, "loss_chance": 0.0,
        # No "nutrient_demand" key at all -- effective total demand is 0.04.
    }
    explicit_low_demand = {
        "id": "explicit_low", "seed_cost": 5, "growth_days": 3, "min_yield": 1, "max_yield": 2,
        "base_price": 5, "loss_chance": 0.0,
        # Explicit total demand 0.015 -- genuinely gentler than the 0.04
        # default, even though it's the one with a JSON-present field.
        "nutrient_demand": {"nitrogen": 0.005, "phosphorus": 0.005, "potassium": 0.005},
    }
    chosen = economy_rules.best_crop_by_expected_profit(
        [omitted_demand, explicit_low_demand], player, {}
    )
    assert chosen["id"] == "explicit_low"
