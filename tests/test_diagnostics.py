from agents.fast_seller import FastSeller
from agents.profit_optimizer import ProfitOptimizer
from metrics.aggregate_results import aggregate
from metrics.economics_audit import build_economics_audit
from metrics.run_results import build_run_result
from runner.single_run import run_single
from tests.test_engine import FERTILIZER_CONFIG, WATERING_SETTINGS, make_crops, make_upgrades


def test_bankruptcy_records_human_day_and_reason():
    config = {"start_money": 1, "start_slots": 1, "days": 30}
    crops, upgrades = make_crops(), make_upgrades()
    player, _, _ = run_single(
        config, ProfitOptimizer(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=1
    )

    assert player.bankrupt
    assert player.bankruptcy_day == 1
    assert player.bankruptcy_reason == "no_viable_reinvestment"
    assert player.day == 1


def test_expense_categories_reconcile_with_total_expenses():
    config = {"start_money": 100, "start_slots": 2, "days": 12}
    crops, upgrades = make_crops(), make_upgrades()
    player, _, _ = run_single(
        config, ProfitOptimizer(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=3
    )

    assert round(sum(player.expenses_by_category.values()), 6) == round(player.total_expenses, 6)


def test_crop_observations_report_availability_without_overcounting():
    config = {"start_money": 60, "start_slots": 3, "days": 1}
    crops, upgrades = make_crops(), make_upgrades()
    player, _, _ = run_single(
        config, FastSeller(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=4
    )

    observations = player.crop_decision_observations
    assert observations["fast"]["opportunities"] == 3
    assert observations["fast"]["selected"] == 3
    assert observations["fast"]["unlocked"] == 3
    assert observations["premium"]["opportunities"] == 3
    assert observations["premium"]["unlocked"] == 0
    assert all(
        observation["unlocked"] <= observation["opportunities"]
        and observation["affordable"] <= observation["opportunities"]
        for observation in observations.values()
    )


def test_occupied_watering_rate_is_recorded():
    config = {"start_money": 60, "start_slots": 3, "days": 10}
    crops, upgrades = make_crops(), make_upgrades()
    player, _, _ = run_single(
        config, FastSeller(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=5
    )

    assert player.occupied_slot_days > 0
    assert player.total_waterings <= player.occupied_slot_days


def test_aggregate_separates_survivors_and_bankrupt_runs():
    crops, upgrades = make_crops(), make_upgrades()
    bankrupt_player, bankrupt_seed, _ = run_single(
        {"start_money": 1, "start_slots": 1, "days": 30},
        ProfitOptimizer(),
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        seed=6,
    )
    survivor_player, survivor_seed, _ = run_single(
        {"start_money": 100, "start_slots": 1, "days": 1},
        FastSeller(),
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        seed=7,
    )
    results = [
        build_run_result(
            bankrupt_player, "test", bankrupt_seed, bankrupt_player.day, crops, upgrades
        ),
        build_run_result(
            survivor_player, "test", survivor_seed, survivor_player.day, crops, upgrades
        ),
    ]

    stats = aggregate(results)["test"]
    assert stats["surviving_runs"] == 1
    assert stats["bankrupt_runs"] == 1
    assert stats["avg_final_money_survivors"] == survivor_player.money
    assert stats["avg_final_money_bankrupt"] == bankrupt_player.money
    assert stats["avg_bankruptcy_day"] == 1


def test_economics_audit_matches_nominal_profit_formula():
    crops = make_crops()
    audit = build_economics_audit(
        crops,
        FERTILIZER_CONFIG,
        {"channels": [{"id": "spot", "price_multiplier": 1.0}]},
    )

    fast = next(item for item in audit["crops"] if item["id"] == "fast")
    assert fast["expected_revenue_per_cycle"] == 7.27
    assert fast["nominal_profit_per_cycle"] == 2.27
    assert fast["nominal_profit_per_growth_day"] == 0.76
