"""Tests for the incremental batch aggregator in metrics/aggregate_results.py.

aggregate()/BatchAggregator process one RunResult at a time (instead of
collecting full per-strategy lists) so a multi-million-run batch doesn't
have to sit in memory. These tests build RunResults directly (bypassing the
simulator) so cohort sizes, edge cases, and ordering can be controlled
exactly, and check the incremental accumulator against hand-computed
expected values and against itself under different feed orders.
"""

import random
import statistics

from metrics.aggregate_results import BatchAggregator, aggregate
from metrics.run_results import RunResult


def _make_run_result(
    strategy="test",
    seed=0,
    final_money=50.0,
    bankrupt=False,
    bankruptcy_day=None,
    bankruptcy_reason=None,
    minimum_cash_balance=10.0,
    first_upgrade_day=None,
    second_upgrade_day=None,
    crop_counts=None,
    crops_planted=2,
    expenses_by_category=None,
    revenue_by_channel=None,
    quality_harvested=None,
    crop_decision_observations=None,
    **overrides,
):
    fields = dict(
        strategy=strategy,
        seed=seed,
        days_simulated=10,
        final_money=final_money,
        total_revenue=100.0,
        total_expenses=50.0,
        total_costs=50.0,
        net_profit=50.0,
        gross_profit=40.0,
        operating_profit=35.0,
        net_cash_change=50.0,
        expenses_by_category=expenses_by_category
        if expenses_by_category is not None
        else {"seeds": 5.0},
        crops_planted=crops_planted,
        crops_harvested=2,
        crops_sold=2,
        crop_counts=crop_counts if crop_counts is not None else {"quickweed": crops_planted},
        crop_percentages={},
        avg_profit_per_day=5.0,
        avg_profit_per_slot_day=1.0,
        first_upgrade_day=first_upgrade_day,
        second_upgrade_day=second_upgrade_day,
        idle_days=0,
        bankrupt=bankrupt,
        bankruptcy_day=bankruptcy_day,
        bankruptcy_reason=bankruptcy_reason,
        lowest_money=final_money,
        minimum_cash_balance=minimum_cash_balance,
        highest_money=final_money,
        crops_lost=0,
        crop_loss_rate=0.0,
        watering_rate=50.0,
        occupied_watering_rate=60.0,
        occupied_slot_days=5,
        fertilizer_applications=0,
        spoiled_units=0,
        processed_units=0,
        contracts_completed=0,
        contracts_failed=0,
        final_reputation=0.0,
        revenue_by_channel=revenue_by_channel if revenue_by_channel is not None else {"spot": 50.0},
        quality_harvested=quality_harvested if quality_harvested is not None else {"standard": 1},
        crop_decision_observations=(
            crop_decision_observations
            if crop_decision_observations is not None
            else {"quickweed": {"opportunities": 3, "selected": 2}}
        ),
    )
    fields.update(overrides)
    return RunResult(**fields)


def test_aggregate_matches_hand_computed_stats_for_mixed_cohort():
    survivors = [
        _make_run_result(
            final_money=10.0, minimum_cash_balance=5.0, first_upgrade_day=2, second_upgrade_day=6
        ),
        _make_run_result(final_money=20.0, minimum_cash_balance=10.0, expenses_by_category={}),
        _make_run_result(
            final_money=30.0,
            minimum_cash_balance=15.0,
            first_upgrade_day=4,
            expenses_by_category={"seeds": 5.0, "watering": 2.0},
        ),
    ]
    bankrupt = [
        _make_run_result(
            final_money=5.0,
            minimum_cash_balance=2.5,
            bankrupt=True,
            bankruptcy_day=3,
            bankruptcy_reason="no_viable_reinvestment",
        ),
        _make_run_result(
            final_money=15.0,
            minimum_cash_balance=7.5,
            bankrupt=True,
            bankruptcy_day=7,
            bankruptcy_reason="no_viable_reinvestment",
        ),
    ]
    results = survivors + bankrupt

    stats = aggregate(results)["test"]

    assert stats["num_runs"] == 5
    assert stats["avg_final_money"] == 16.0
    assert stats["median_final_money"] == 15.0
    assert stats["min_final_money"] == 5.0
    assert stats["max_final_money"] == 30.0
    assert stats["surviving_runs"] == 3
    assert stats["bankrupt_runs"] == 2
    assert stats["avg_final_money_survivors"] == 20.0
    assert stats["median_final_money_survivors"] == 20.0
    assert stats["avg_final_money_bankrupt"] == 10.0
    assert stats["median_final_money_bankrupt"] == 10.0
    assert stats["bankruptcy_rate"] == 40.0
    assert stats["avg_bankruptcy_day"] == 5.0
    assert stats["median_bankruptcy_day"] == 5.0
    assert stats["min_bankruptcy_day"] == 3
    assert stats["max_bankruptcy_day"] == 7
    assert stats["avg_minimum_cash_balance"] == 8.0
    assert stats["avg_minimum_cash_balance_bankrupt"] == 5.0
    assert stats["bankruptcy_reasons"] == {"no_viable_reinvestment": 2}
    assert stats["avg_first_upgrade_day"] == 3.0
    assert stats["avg_second_upgrade_day"] == 6.0
    assert stats["crop_usage_pct"] == {"quickweed": 100.0}
    # One run's empty expenses_by_category must still count toward the
    # denominator (5 runs), not just the runs that recorded that category.
    assert stats["avg_expenses_by_category"] == {"seeds": 4.0, "watering": 0.4}
    assert stats["revenue_by_channel"] == {"spot": 50.0}
    assert stats["quality_harvested"] == {"standard": 5}
    assert stats["crop_decision_observations"] == {
        "quickweed": {"opportunities": 15, "selected": 10}
    }
    assert stats["avg_watering_rate"] == 50.0
    assert stats["avg_total_costs"] == 50.0
    assert stats["avg_gross_profit"] == 40.0
    assert stats["avg_operating_profit"] == 35.0
    assert stats["avg_net_cash_change"] == 50.0
    # Mean of each run's own avg_profit_per_day; every _make_run_result here
    # uses the fixture default (5.0), so the cohort mean is 5.0 too.
    assert stats["avg_profit_per_day"] == 5.0


def test_avg_profit_per_day_averages_each_runs_own_ratio():
    """Regression guard for aggregating avg_profit_per_day as a mean of
    per-run ratios, not net_cash_change / a shared day count -- bankrupt
    runs end on their own day, so the two are not interchangeable.
    """
    results = [
        _make_run_result(avg_profit_per_day=2.0),
        _make_run_result(avg_profit_per_day=4.0),
        _make_run_result(avg_profit_per_day=-6.0, bankrupt=True, bankruptcy_day=3),
    ]

    stats = aggregate(results)["test"]

    assert stats["avg_profit_per_day"] == 0.0


def test_aggregate_handles_zero_bankrupt_runs():
    results = [_make_run_result(final_money=m) for m in (1.0, 2.0, 3.0)]

    stats = aggregate(results)["test"]

    assert stats["surviving_runs"] == 3
    assert stats["bankrupt_runs"] == 0
    assert stats["bankruptcy_rate"] == 0.0
    assert stats["avg_final_money_bankrupt"] is None
    assert stats["median_final_money_bankrupt"] is None
    assert stats["avg_bankruptcy_day"] is None
    assert stats["median_bankruptcy_day"] is None
    assert stats["min_bankruptcy_day"] is None
    assert stats["max_bankruptcy_day"] is None
    assert stats["avg_minimum_cash_balance_bankrupt"] is None
    assert stats["bankruptcy_reasons"] == {}


def test_aggregate_handles_zero_survivor_runs():
    results = [
        _make_run_result(
            final_money=1.0, bankrupt=True, bankruptcy_day=1, bankruptcy_reason="ruin"
        ),
        _make_run_result(
            final_money=2.0, bankrupt=True, bankruptcy_day=2, bankruptcy_reason="ruin"
        ),
    ]

    stats = aggregate(results)["test"]

    assert stats["surviving_runs"] == 0
    assert stats["bankrupt_runs"] == 2
    assert stats["bankruptcy_rate"] == 100.0
    assert stats["avg_final_money_survivors"] is None
    assert stats["median_final_money_survivors"] is None


def _synthetic_results(n=20):
    rng = random.Random(1234)
    results = []
    for i in range(n):
        strategy = "alpha" if i % 2 == 0 else "beta"
        bankrupt = i % 3 == 0
        results.append(
            _make_run_result(
                strategy=strategy,
                seed=i,
                final_money=round(rng.uniform(-20.0, 500.0), 2),
                bankrupt=bankrupt,
                bankruptcy_day=(i % 15) + 1 if bankrupt else None,
                bankruptcy_reason="no_viable_reinvestment" if bankrupt else None,
                minimum_cash_balance=round(rng.uniform(0.0, 50.0), 2),
                first_upgrade_day=i if i % 4 == 0 else None,
                second_upgrade_day=i if i % 7 == 0 else None,
                crop_counts={"quickweed": i % 5, "purplehaze": i % 3},
                crops_planted=(i % 5) + (i % 3),
                expenses_by_category={}
                if i % 6 == 0
                else {"seeds": round(rng.uniform(1.0, 10.0), 2)},
                revenue_by_channel={"spot": round(rng.uniform(10.0, 100.0), 2)},
                quality_harvested={"standard": i % 4, "premium": i % 2},
            )
        )
    return results


def test_batch_aggregator_matches_aggregate_when_fed_one_at_a_time():
    results = _synthetic_results()
    reference = aggregate(results)

    aggregator = BatchAggregator()
    for r in results:
        aggregator.add(r)

    assert aggregator.finalize() == reference


def test_aggregate_is_order_independent():
    results = _synthetic_results()
    reference = aggregate(results)

    shuffled = list(results)
    random.Random(99).shuffle(shuffled)
    assert aggregate(shuffled) == reference

    assert aggregate(list(reversed(results))) == reference


def test_neumaier_running_mean_matches_statistics_mean():
    results = _synthetic_results()
    stats = aggregate(results)

    for strategy in ("alpha", "beta"):
        expected = round(
            statistics.mean(r.final_money for r in results if r.strategy == strategy), 2
        )
        # avg_final_money is unrounded (it feeds a warning threshold
        # comparison in metrics/warnings.py -- see #28); round only for
        # display, so compare against the rounded value here.
        assert round(stats[strategy]["avg_final_money"], 2) == expected
