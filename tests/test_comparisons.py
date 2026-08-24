"""Tests for metrics/comparisons.py.

Two properties matter most and are checked directly: an independent result and
a paired result must be distinguishable in the output (they answer different
questions and have different validity conditions), and the multiplicity family
must always be recorded.
"""

import random
import statistics
from types import SimpleNamespace

import pytest

from metrics.comparisons import (
    PAIRING_INDEPENDENT,
    PAIRING_PAIRED,
    adjust_family,
    benjamini_hochberg_adjusted,
    bonferroni_confidence,
    compare_all_pairs,
    compare_means_independent,
    compare_means_paired,
    compare_pair,
    compare_proportions_independent,
    holm_adjusted,
    pair_by_replicate,
    win_probability,
)
from metrics.inference import student_t_quantile


def _observations(strategy, values, bankrupt=None, replicates=None):
    bankrupt = bankrupt or [False] * len(values)
    replicates = replicates if replicates is not None else list(range(len(values)))
    return [
        SimpleNamespace(
            strategy=strategy,
            replicate_id=replicate,
            final_money=value,
            bankrupt=flag,
            avg_profit_per_day=value / 30.0,
            bankruptcy_day=None,
            minimum_cash_balance=0.0,
            first_upgrade_day=None,
            crop_loss_rate=None,
        )
        for value, flag, replicate in zip(values, bankrupt, replicates, strict=True)
    ]


# --------------------------------------------------------------------------
# Win probability
# --------------------------------------------------------------------------


def test_win_probability_matches_brute_force_including_ties():
    rng = random.Random(0)
    for _ in range(200):
        a = [rng.randrange(4) for _ in range(rng.randrange(1, 9))]
        b = [rng.randrange(4) for _ in range(rng.randrange(1, 9))]
        total = len(a) * len(b)
        expected_strict = sum(1 for x in a for y in b if x > y) / total
        expected_ties = sum(1 for x in a for y in b if x == y) / total
        result = win_probability(a, b)
        assert result["strict_wins"] == pytest.approx(expected_strict)
        assert result["ties"] == pytest.approx(expected_ties)
        assert result["win_probability"] == pytest.approx(expected_strict + 0.5 * expected_ties)


def test_win_probability_reports_ties_separately_from_wins():
    result = win_probability([1, 1, 1], [1, 1, 1])
    assert result["strict_wins"] == 0.0
    assert result["ties"] == 1.0
    assert result["win_probability"] == 0.5


def test_win_probability_is_undefined_for_an_empty_arm():
    assert win_probability([], [1.0])["win_probability"] is None


# --------------------------------------------------------------------------
# Independent comparisons
# --------------------------------------------------------------------------


def test_welch_difference_matches_hand_calculation():
    a = [10.0, 12.0, 14.0, 16.0]
    b = [5.0, 6.0, 9.0, 12.0]
    comparison = compare_means_independent(a, b, "expected_final_money", "A", "B")
    var_a, var_b = statistics.variance(a), statistics.variance(b)
    se = (var_a / 4 + var_b / 4) ** 0.5
    df = (var_a / 4 + var_b / 4) ** 2 / ((var_a / 4) ** 2 / 3 + (var_b / 4) ** 2 / 3)
    margin = student_t_quantile(0.975, df) * se
    assert comparison.difference == pytest.approx(statistics.fmean(a) - statistics.fmean(b))
    assert comparison.standard_error == pytest.approx(se)
    assert comparison.lower == pytest.approx(comparison.difference - margin)
    assert comparison.upper == pytest.approx(comparison.difference + margin)
    assert comparison.pairing == PAIRING_INDEPENDENT


def test_relative_difference_is_undefined_against_a_zero_reference():
    comparison = compare_means_independent(
        [1.0, 2.0, 3.0], [0.0, 0.0, 0.0], "expected_final_money", "A", "B"
    )
    assert comparison.relative_difference is None


def test_independent_comparison_needs_two_observations_per_arm():
    comparison = compare_means_independent([1.0], [2.0, 3.0], "e", "A", "B")
    assert comparison.lower is None
    assert "at least two observations" in comparison.notes


def test_proportion_difference_interval_contains_the_point_estimate():
    comparison = compare_proportions_independent(
        20, 100, 5, 100, "bankruptcy_probability", "A", "B"
    )
    assert comparison.difference == pytest.approx(0.15)
    assert comparison.lower < comparison.difference < comparison.upper
    assert comparison.lower >= -1.0 and comparison.upper <= 1.0
    assert comparison.p_value < 0.05


def test_proportion_difference_handles_zero_event_arms():
    comparison = compare_proportions_independent(0, 50, 0, 50, "bankruptcy_probability", "A", "B")
    assert comparison.difference == 0.0
    assert comparison.lower <= 0.0 <= comparison.upper


# --------------------------------------------------------------------------
# Paired comparisons
# --------------------------------------------------------------------------


def test_pairing_joins_on_replicate_id_and_reports_dropped_runs():
    a = _observations("A", [1.0, 2.0, 3.0], replicates=[0, 1, 2])
    b = _observations("B", [1.5, 2.5], replicates=[1, 2])
    aligned_a, aligned_b, diagnostics = pair_by_replicate(a, b)
    assert [o.replicate_id for o in aligned_a] == [1, 2]
    assert [o.replicate_id for o in aligned_b] == [1, 2]
    assert diagnostics["pairs"] == 2
    assert diagnostics["unmatched_a"] == 1


def test_pairing_ignores_runs_with_no_replicate_id():
    a = _observations("A", [1.0, 2.0], replicates=[None, 1])
    b = _observations("B", [1.0, 2.0], replicates=[None, 1])
    _, _, diagnostics = pair_by_replicate(a, b)
    assert diagnostics["pairs"] == 1
    assert diagnostics["missing_replicate_id_a"] == 1


def test_pair_ordering_does_not_change_the_result():
    rng = random.Random(3)
    values_a = [rng.gauss(100, 10) for _ in range(30)]
    values_b = [value - 5 + rng.gauss(0, 1) for value in values_a]
    a = _observations("A", values_a)
    b = _observations("B", values_b)
    forward = compare_pair(a, b, "expected_final_money", "A", "B", pairing=PAIRING_PAIRED)
    shuffled_a = list(a)
    shuffled_b = list(b)
    random.Random(9).shuffle(shuffled_a)
    random.Random(10).shuffle(shuffled_b)
    reordered = compare_pair(
        shuffled_a, shuffled_b, "expected_final_money", "A", "B", pairing=PAIRING_PAIRED
    )
    assert forward.difference == pytest.approx(reordered.difference)
    assert forward.lower == pytest.approx(reordered.lower)


def test_paired_comparison_measures_variance_reduction_on_correlated_arms():
    rng = random.Random(5)
    shared = [rng.gauss(0, 30) for _ in range(60)]
    values_a = [100 + s + rng.gauss(0, 2) for s in shared]
    values_b = [95 + s + rng.gauss(0, 2) for s in shared]
    comparison = compare_means_paired(
        values_a, values_b, "expected_final_money", "A", "B", bootstrap=False
    )
    assert comparison.pairing == PAIRING_PAIRED
    assert comparison.difference == pytest.approx(5.0, abs=1.0)
    assert comparison.correlation > 0.9
    assert comparison.variance_reduction > 0.9, "strong pairing should remove most variance"
    assert comparison.n_pairs == 60


def test_paired_comparison_reports_no_gain_on_independent_arms():
    rng = random.Random(6)
    values_a = [rng.gauss(100, 10) for _ in range(60)]
    values_b = [rng.gauss(100, 10) for _ in range(60)]
    comparison = compare_means_paired(
        values_a, values_b, "expected_final_money", "A", "B", bootstrap=False
    )
    assert abs(comparison.correlation) < 0.4
    assert comparison.variance_reduction < 0.2, "weak pairing must not claim a gain it did not get"


def test_paired_interval_is_tighter_than_the_independent_one_when_pairing_works():
    rng = random.Random(7)
    shared = [rng.gauss(0, 50) for _ in range(50)]
    values_a = [100 + s for s in shared]
    values_b = [90 + s for s in shared]
    paired = compare_means_paired(
        values_a, values_b, "expected_final_money", "A", "B", bootstrap=False
    )
    independent = compare_means_independent(values_a, values_b, "expected_final_money", "A", "B")
    assert (paired.upper - paired.lower) < (independent.upper - independent.lower)


def test_paired_bootstrap_records_its_seed_and_replications():
    comparison = compare_means_paired(
        [1.0, 2.0, 3.0, 4.0],
        [0.5, 1.5, 2.5, 3.5],
        "expected_final_money",
        "A",
        "B",
        replications=50,
        base_seed=11,
    )
    assert comparison.extra["bootstrap"]["replications"] == 50
    assert comparison.extra["bootstrap"]["analysis_seed"] is not None


def test_paired_and_independent_results_are_distinguishable():
    a = _observations("A", [float(v) for v in range(20)])
    b = _observations("B", [float(v) + 1 for v in range(20)])
    independent = compare_pair(a, b, "expected_final_money", "A", "B")
    paired = compare_pair(a, b, "expected_final_money", "A", "B", pairing=PAIRING_PAIRED)
    assert independent.pairing != paired.pairing
    assert independent.n_pairs is None and paired.n_pairs == 20
    assert independent.to_dict()["pairing"] == PAIRING_INDEPENDENT


# --------------------------------------------------------------------------
# Multiplicity
# --------------------------------------------------------------------------


def test_bonferroni_confidence_widens_with_family_size():
    assert bonferroni_confidence(0.95, 1) == pytest.approx(0.95)
    assert bonferroni_confidence(0.95, 55) == pytest.approx(1 - 0.05 / 55)


def test_holm_is_monotone_and_at_least_as_large_as_the_raw_p_values():
    raw = [0.001, 0.01, 0.02, 0.5]
    adjusted = holm_adjusted(raw)
    assert adjusted == pytest.approx([0.004, 0.03, 0.04, 0.5])
    assert all(a >= r for a, r in zip(adjusted, raw, strict=True))
    assert adjusted == sorted(adjusted), "step-down enforces monotonicity"


def test_benjamini_hochberg_matches_the_standard_definition():
    raw = [0.001, 0.008, 0.039, 0.041, 0.042]
    # m * p / rank, then the step-up minimum from the largest p downwards:
    # 0.005, 0.02, 0.065, 0.05125, 0.042 -> the last three all collapse to
    # 0.042, because a q-value can never exceed one computed at a higher rank.
    expected = [0.005, 0.02, 0.042, 0.042, 0.042]
    assert benjamini_hochberg_adjusted(raw) == pytest.approx(expected, abs=1e-6)


def test_bh_is_never_more_conservative_than_holm():
    raw = [0.001, 0.01, 0.02, 0.03, 0.5]
    assert all(
        bh <= holm
        for bh, holm in zip(benjamini_hochberg_adjusted(raw), holm_adjusted(raw), strict=True)
    )


def test_adjust_family_records_the_family_on_every_comparison():
    comparisons = [
        compare_means_independent([1.0, 2.0, 3.0], [2.0, 3.0, 4.0], "e", "A", "B"),
        compare_means_independent([1.0, 2.0, 3.0], [9.0, 9.5, 10.0], "e", "A", "C"),
    ]
    adjust_family(comparisons, method="bonferroni", family="e:independent")
    for comparison in comparisons:
        assert comparison.family == "e:independent"
        assert comparison.family_size == 2
        assert comparison.correction == "bonferroni"
        assert comparison.extra["per_comparison_confidence"] == pytest.approx(1 - 0.05 / 2)


def test_unknown_correction_is_rejected():
    with pytest.raises(ValueError):
        adjust_family([], method="sidak")


# --------------------------------------------------------------------------
# Families
# --------------------------------------------------------------------------


def test_all_pairs_covers_every_unordered_pair_once():
    cohorts = {
        name: _observations(name, [float(v) + offset for v in range(10)])
        for offset, name in enumerate(("a", "b", "c", "d"))
    }
    document = compare_all_pairs(cohorts, estimand_ids=["expected_final_money"])
    pairs = document["estimands"]["expected_final_money"]
    assert len(pairs) == 6  # 4 choose 2
    seen = {(pair["strategy_a"], pair["strategy_b"]) for pair in pairs}
    assert len(seen) == 6
    assert all(pair["family_size"] == 6 for pair in pairs)


def test_baseline_mode_reduces_the_family_to_one_per_strategy():
    cohorts = {
        name: _observations(name, [float(v) + offset for v in range(10)])
        for offset, name in enumerate(("a", "b", "c", "d"))
    }
    document = compare_all_pairs(cohorts, estimand_ids=["expected_final_money"], baseline="a")
    pairs = document["estimands"]["expected_final_money"]
    assert len(pairs) == 3
    assert all("a" in (pair["strategy_a"], pair["strategy_b"]) for pair in pairs)


def test_all_pairs_is_invariant_to_input_dict_order():
    values = {name: [float(v) + i for v in range(8)] for i, name in enumerate("abc")}
    forward = compare_all_pairs(
        {name: _observations(name, vals) for name, vals in values.items()},
        estimand_ids=["expected_final_money"],
    )
    reversed_doc = compare_all_pairs(
        {name: _observations(name, values[name]) for name in reversed(list(values))},
        estimand_ids=["expected_final_money"],
    )
    assert forward == reversed_doc


def test_comparison_rejects_an_estimand_that_does_not_support_it(monkeypatch):
    from metrics import estimands

    estimand = estimands.get("final_money_quantile")
    object.__setattr__(estimand, "supports_comparison", False)
    try:
        with pytest.raises(ValueError):
            compare_pair([], [], "final_money_quantile", "A", "B")
    finally:
        object.__setattr__(estimand, "supports_comparison", True)
