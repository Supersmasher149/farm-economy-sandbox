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

    assert (
        economy_rules.effective_growth_days(
            dict(fast_crop, growth_days=10),
            player,
            {"first": first, "second": second},
        )
        == 6
    )


def test_expected_profit_per_day_is_positive_for_profitable_crop(player, standard_crop):
    profit = economy_rules.expected_profit_per_day(standard_crop, player, {})
    assert profit > 0


# -- fertilizer marginal profit: survival x yield-bonus interaction (#26) ----


def test_fertilizer_marginal_profit_matches_enumerated_expected_outcomes():
    """Brute-force E[revenue] fertilized vs. not, by direct enumeration of the
    two outcomes (loss / no-loss) each branch can land in -- rather than
    trusting any algebraic simplification -- and check the function against
    that ground truth.
    """
    crop = {"min_yield": 4, "max_yield": 8, "base_price": 9, "loss_chance": 0.3}
    fert = {"yield_bonus_pct": 0.2, "loss_chance_reduction": 0.05, "cost": 6}

    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    original_loss = crop["loss_chance"]
    reduced_loss = original_loss - fert["loss_chance_reduction"]
    yield_bonus = avg_yield * fert["yield_bonus_pct"]

    # E[revenue] = P(survive) * yield * price, enumerated directly rather
    # than via the marginal-profit formula under test.
    expected_unfertilized = (1 - original_loss) * avg_yield * crop["base_price"]
    expected_fertilized = (1 - reduced_loss) * (avg_yield + yield_bonus) * crop["base_price"]
    expected_marginal_profit = expected_fertilized - expected_unfertilized - fert["cost"]

    assert economy_rules.fertilizer_expected_marginal_profit(crop, fert) == pytest.approx(
        expected_marginal_profit
    )


def test_fertilizer_marginal_profit_interaction_can_flip_the_decision_sign():
    """A case where omitting the survival x yield-bonus interaction (the old
    bug: weighting the yield bonus by the *unfertilized* survival odds)
    reports fertilizer as unprofitable, but the correct joint calculation
    says it's worth buying.
    """
    crop = {"min_yield": 10, "max_yield": 10, "base_price": 10, "loss_chance": 0.5}
    fert = {"yield_bonus_pct": 0.5, "loss_chance_reduction": 0.1, "cost": 37}

    # The old, buggy formula for comparison: yield bonus weighted by
    # (1 - original_loss_chance) instead of (1 - reduced_loss_chance).
    avg_yield = 10
    yield_bonus = avg_yield * fert["yield_bonus_pct"]
    buggy_yield_bonus_revenue = yield_bonus * crop["base_price"] * (1 - crop["loss_chance"])
    buggy_loss_reduction_revenue = fert["loss_chance_reduction"] * avg_yield * crop["base_price"]
    buggy_marginal_profit = buggy_yield_bonus_revenue + buggy_loss_reduction_revenue - fert["cost"]

    assert buggy_marginal_profit == pytest.approx(-2.0)
    assert buggy_marginal_profit <= 0  # the bug says "don't fertilize"

    actual = economy_rules.fertilizer_expected_marginal_profit(crop, fert)
    assert actual == pytest.approx(3.0)
    assert actual > 0  # the corrected math says "fertilize"


# -- upgrade-purchase budget gate -------------------------------------------


def test_upgrade_payback_days_none_without_crop_catalog(player, capacity_upgrade):
    assert economy_rules.upgrade_payback_days(capacity_upgrade, player, {}, {}) is None


def test_upgrade_payback_days_prices_capacity_upgrade(player, standard_crop, capacity_upgrade):
    crops_by_id = {"standard": standard_crop}
    payback = economy_rules.upgrade_payback_days(capacity_upgrade, player, crops_by_id, {})
    nominal = economy_rules.expected_profit_per_day(standard_crop, player, {})
    expected = capacity_upgrade["cost"] / (nominal * capacity_upgrade["effect"]["amount"])
    assert payback == pytest.approx(expected)


# -- growth-time-reduction upgrades priced off rounded durations (#23) ------


def test_upgrade_payback_days_growth_reduction_no_op_has_no_finite_payback(player, fast_crop):
    crops_by_id = {"fast": fast_crop}
    # fast_crop.growth_days == 3; 3 * (1 - 0.1) == 2.7, which rounds to 3 --
    # the exact same integer duration. The old continuous amount/(1-amount)
    # approximation still priced this as a finite (and wrong) payback of
    # cost / (slots * profit_per_day * 0.1/0.9); the fix must instead see
    # zero real throughput gain and refuse to price it at all.
    no_op_upgrade = {
        "id": "no_op",
        "cost": 50,
        "effect": {"type": "growth_time_reduction", "amount": 0.1},
    }
    upgrades_by_id = {"no_op": no_op_upgrade}

    assert economy_rules.effective_growth_days(fast_crop, player, upgrades_by_id) == 3
    payback = economy_rules.upgrade_payback_days(no_op_upgrade, player, crops_by_id, upgrades_by_id)
    assert payback is None


def test_upgrade_payback_days_growth_reduction_prices_the_real_one_day_change(
    player, fast_crop, efficiency_upgrade
):
    # fast_crop.growth_days == 3; efficiency_upgrade's 0.20 reduction rounds
    # 3 * 0.8 == 2.4 down to 2 -- a real, one-day-shorter cycle.
    crops_by_id = {"fast": fast_crop}
    upgrades_by_id = {"efficiency_1": efficiency_upgrade}

    payback = economy_rules.upgrade_payback_days(
        efficiency_upgrade, player, crops_by_id, upgrades_by_id
    )

    avg_yield = (fast_crop["min_yield"] + fast_crop["max_yield"]) / 2
    profit_per_cycle = avg_yield * fast_crop["base_price"] * (1 - fast_crop["loss_chance"])
    profit_per_cycle -= fast_crop["seed_cost"]
    added_value_per_day = player.slots_total * profit_per_cycle * (1 / 2 - 1 / 3)
    expected = efficiency_upgrade["cost"] / added_value_per_day
    assert payback == pytest.approx(expected)


def test_upgrade_payback_days_growth_reduction_stacks_on_already_owned_upgrades(player, fast_crop):
    """Pricing a second growth-reduction upgrade must account for the one(s)
    already owned: the before/after day counts are both computed with the
    already-owned set, differing only by whether the candidate is added --
    not from the crop's raw, un-upgraded growth_days.
    """
    ten_day_crop = dict(fast_crop, growth_days=10)
    crops_by_id = {"fast": ten_day_crop}
    first = {"id": "first", "cost": 90, "effect": {"type": "growth_time_reduction", "amount": 0.15}}
    second = {
        "id": "second",
        "cost": 90,
        "effect": {"type": "growth_time_reduction", "amount": 0.20},
    }
    upgrades_by_id = {"first": first, "second": second}
    player.upgrades_owned.add("first")

    # Matches test_effective_growth_days_uses_upgrade_configuration_order's
    # stacked fold: 10 -(15%)-> 8 -(20%)-> 6.
    current_days = economy_rules.effective_growth_days(ten_day_crop, player, upgrades_by_id)
    assert current_days == 8

    payback = economy_rules.upgrade_payback_days(second, player, crops_by_id, upgrades_by_id)

    avg_yield = (ten_day_crop["min_yield"] + ten_day_crop["max_yield"]) / 2
    profit_per_cycle = avg_yield * ten_day_crop["base_price"] * (1 - ten_day_crop["loss_chance"])
    profit_per_cycle -= ten_day_crop["seed_cost"]
    added_value_per_day = player.slots_total * profit_per_cycle * (1 / 6 - 1 / 8)
    expected = second["cost"] / added_value_per_day
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
        "id": "demanding",
        "seed_cost": 45,
        "growth_days": 12,
        "min_yield": 5,
        "max_yield": 10,
        "base_price": 16,
        "loss_chance": 0.0,
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
        "id": "demanding",
        "seed_cost": 45,
        "growth_days": 12,
        "min_yield": 5,
        "max_yield": 10,
        "base_price": 16,
        "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
        "family": "flowering",
    }
    nominal = economy_rules.expected_profit_per_day(crop, player, {})
    adjusted = economy_rules.quality_adjusted_profit_per_day(crop, player, {})
    assert adjusted < nominal


def test_soil_quality_risk_favors_low_demand_crop_when_depleted(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    low_demand = {
        "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.01, "potassium": 0.01},
        "family": "leafy",
    }
    high_demand = {
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
        "family": "flowering",
    }
    assert economy_rules.soil_quality_risk(low_demand, player) < economy_rules.soil_quality_risk(
        high_demand, player
    )


def test_best_crop_by_expected_profit_triages_to_lowest_demand_when_soil_critical(player):
    for plot in player.plots:
        plot.nitrogen = plot.phosphorus = plot.potassium = 0.0
    low_demand = {
        "id": "low",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 1,
        "max_yield": 2,
        "base_price": 5,
        "loss_chance": 0.0,
        "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.01, "potassium": 0.01},
    }
    high_demand = {
        "id": "high",
        "seed_cost": 45,
        "growth_days": 12,
        "min_yield": 5,
        "max_yield": 10,
        "base_price": 16,
        "loss_chance": 0.0,
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
        "id": "omitted",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 1,
        "max_yield": 2,
        "base_price": 5,
        "loss_chance": 0.0,
        # No "nutrient_demand" key at all -- effective total demand is 0.04.
    }
    explicit_low_demand = {
        "id": "explicit_low",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 1,
        "max_yield": 2,
        "base_price": 5,
        "loss_chance": 0.0,
        # Explicit total demand 0.015 -- genuinely gentler than the 0.04
        # default, even though it's the one with a JSON-present field.
        "nutrient_demand": {"nitrogen": 0.005, "phosphorus": 0.005, "potassium": 0.005},
    }
    chosen = economy_rules.best_crop_by_expected_profit(
        [omitted_demand, explicit_low_demand], player, {}
    )
    assert chosen["id"] == "explicit_low"
