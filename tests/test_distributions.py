"""Tests for metrics/distributions.py -- the exact-observation analysis tier.

The distinction these tests defend is the two-tier storage policy: quantiles
and tails here must come from the real per-run observations, never from the
1,024-value reservoir the streaming aggregator keeps.
"""

import csv
import statistics

import pytest

from metrics.distributions import (
    DEFAULT_QUANTILE_PROBABILITIES,
    EMPIRICAL_QUANTILE_CONVENTION,
    analyze_csv,
    bootstrap_quantile_set,
    describe,
    ecdf,
    histogram,
    load_observations,
    median_absolute_deviation,
    observed_values,
    quantile,
    quantile_estimate,
    skewness,
    survival_curve,
    tail_probability,
    tukey_fences,
)

# --------------------------------------------------------------------------
# Quantile conventions
# --------------------------------------------------------------------------


def test_inverse_cdf_quantile_returns_an_observed_value():
    values = [1.0, 2.0, 3.0, 4.0]
    # Q_p = inf {m : F(m) >= p}: p50 is the 2nd of 4 order statistics.
    assert quantile(values, 0.5) == 2.0
    assert quantile(values, 0.25) == 1.0
    assert quantile(values, 0.75) == 3.0
    assert quantile(values, 1.0) == 4.0
    assert quantile(values, 0.0) == 1.0


def test_linear_convention_matches_the_statistics_module():
    values = [1.0, 2.0, 3.0, 4.0, 7.0]
    for p in (0.25, 0.5, 0.75):
        expected = statistics.quantiles(values, n=100, method="inclusive")[int(p * 100) - 1]
        assert quantile(values, p, "linear") == pytest.approx(expected, abs=1e-12)


def test_quantile_conventions_can_disagree_and_both_are_named():
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.5, "inverse_cdf") == 2.0
    assert quantile(values, 0.5, "linear") == 2.5
    assert EMPIRICAL_QUANTILE_CONVENTION == "inverse_cdf"


def test_unknown_convention_is_rejected():
    with pytest.raises(ValueError):
        quantile([1.0, 2.0], 0.5, "nearest")


def test_quantile_of_degenerate_samples():
    assert quantile([], 0.5) is None
    assert quantile([9.0], 0.99) == 9.0


# --------------------------------------------------------------------------
# Shape diagnostics
# --------------------------------------------------------------------------


def test_skewness_matches_the_adjusted_fisher_pearson_definition():
    values = [1.0, 2.0, 3.0, 4.0, 12.0]
    n = len(values)
    mean = statistics.fmean(values)
    s = statistics.stdev(values)
    expected = n / ((n - 1) * (n - 2)) * sum(((x - mean) / s) ** 3 for x in values)
    assert skewness(values) == pytest.approx(expected, rel=1e-12)


def test_skewness_is_undefined_without_shape():
    assert skewness([1.0, 2.0]) is None, "needs at least three observations"
    assert skewness([5.0, 5.0, 5.0]) is None, "a constant sample has no shape"


def test_symmetric_sample_has_near_zero_skew():
    assert skewness([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(0.0, abs=1e-12)


def test_median_absolute_deviation_ignores_a_wild_outlier():
    values = [10.0, 11.0, 12.0, 13.0, 1000.0]
    assert median_absolute_deviation(values) == pytest.approx(1.0)
    assert statistics.stdev(values) > 400


def test_tukey_fences_and_outlier_counts():
    values = [float(v) for v in range(1, 11)] + [500.0]
    low, high = tukey_fences(values)
    described = describe(values)
    assert described["tukey_outliers"]["upper"] == 1
    assert described["tukey_outliers"]["lower"] == 0
    assert described["tukey_outliers"]["upper_fence"] == pytest.approx(high)
    assert low is not None
    assert described["count"] == len(values), "outliers are described, never removed"


def test_describe_on_empty_and_constant_cohorts():
    empty = describe([])
    assert empty["count"] == 0
    assert empty["mean"] is None and empty["stdev"] is None
    constant = describe([4.0, 4.0, 4.0])
    assert constant["stdev"] == 0.0
    assert constant["skewness"] is None
    assert constant["mean_minus_median"] == 0.0


# --------------------------------------------------------------------------
# Histogram / ECDF / tails
# --------------------------------------------------------------------------


def test_histogram_uses_freedman_diaconis_and_covers_every_observation():
    values = [float(v % 37) for v in range(200)]
    result = histogram(values)
    assert result["binning"] == "freedman_diaconis"
    assert sum(result["counts"]) == len(values)
    assert len(result["bin_edges"]) == len(result["counts"]) + 1


def test_histogram_falls_back_when_the_iqr_is_zero():
    values = [5.0] * 40 + [100.0]
    result = histogram(values)
    assert result["binning"] == "sturges_fallback_zero_iqr"
    assert sum(result["counts"]) == len(values)


def test_histogram_of_constant_data_is_one_degenerate_bin():
    result = histogram([7.0] * 5)
    assert result["binning"] == "degenerate_constant"
    assert result["counts"] == [5]
    assert result["degenerate"] is True


def test_ecdf_is_exact_and_thins_predictably():
    values = [float(v) for v in range(1000)]
    curve = ecdf(values, max_points=100)
    assert curve["thinned"] is True
    assert len(curve["values"]) <= 100
    assert curve["probabilities"][-1] == pytest.approx(1.0)
    small = ecdf([3.0, 1.0, 2.0])
    assert small["values"] == [1.0, 2.0, 3.0]
    assert small["thinned"] is False


def test_tail_probability_is_a_proportion_with_a_wilson_interval():
    values = [-5.0, -1.0, 0.0, 3.0, 10.0]
    estimate = tail_probability(values, 0.0, "below")
    assert estimate.value == pytest.approx(2 / 5)
    assert estimate.method == "wilson"
    assert estimate.extra["condition"] == "X < 0.0"
    inclusive = tail_probability(values, 0.0, "below", inclusive=True)
    assert inclusive.value == pytest.approx(3 / 5)


def test_tail_probability_direction_is_validated():
    with pytest.raises(ValueError):
        tail_probability([1.0], 0.0, "sideways")


# --------------------------------------------------------------------------
# Bootstrap quantiles
# --------------------------------------------------------------------------


def test_bootstrap_quantile_set_is_deterministic_and_brackets_the_point_estimate():
    values = [float(v) for v in range(200)]
    first = bootstrap_quantile_set(values, replications=200, analysis_seed=11)
    second = bootstrap_quantile_set(values, replications=200, analysis_seed=11)
    for key, estimate in first.items():
        assert (estimate.lower, estimate.upper) == (second[key].lower, second[key].upper)
        assert estimate.lower <= estimate.value <= estimate.upper
        assert estimate.extra["quantile_convention"] == EMPIRICAL_QUANTILE_CONVENTION


def test_bootstrap_quantile_set_covers_the_requested_probabilities():
    result = bootstrap_quantile_set([float(v) for v in range(50)], replications=50, analysis_seed=1)
    assert set(result) == {"p5", "p25", "p50", "p75", "p95"}
    assert all(estimate.n == 50 for estimate in result.values())


def test_bootstrap_quantile_set_on_degenerate_cohorts():
    empty = bootstrap_quantile_set([], replications=10)
    assert all(estimate.value is None for estimate in empty.values())
    single = bootstrap_quantile_set([2.0], replications=10)
    assert all(estimate.value == 2.0 and estimate.lower is None for estimate in single.values())


def test_quantile_estimate_derives_a_stable_seed_from_the_base_seed():
    values = [float(v) for v in range(80)]
    first = quantile_estimate(values, 0.5, base_seed=42, replications=100)
    second = quantile_estimate(values, 0.5, base_seed=42, replications=100)
    other = quantile_estimate(values, 0.5, base_seed=43, replications=100)
    assert first.extra["analysis_seed"] == second.extra["analysis_seed"]
    assert first.extra["analysis_seed"] != other.extra["analysis_seed"]


# --------------------------------------------------------------------------
# Survival
# --------------------------------------------------------------------------


def test_survival_curve_censors_solvent_runs():
    curve = survival_curve([2, 4], [10, 10, 10])
    assert curve["events"] == 2
    assert curve["censored"] == 3
    assert curve["times"] == [2.0, 4.0]
    # 5 at risk, one event at t=2 -> 0.8; then 4 at risk, one event -> 0.6.
    assert curve["survival"] == pytest.approx([0.8, 0.6])


def test_survival_curve_with_no_events_is_flat():
    curve = survival_curve([], [30, 30])
    assert curve["events"] == 0
    assert curve["survival"] == []


def test_survival_differs_from_the_conditional_mean_bankruptcy_day():
    # Two failures at day 5, 98 survivors. The conditional mean is 5; the
    # survival curve says 98% are still solvent -- the two answer different
    # questions, which is exactly why both exist.
    curve = survival_curve([5, 5], [100] * 98)
    assert curve["survival"][-1] == pytest.approx(0.98)


# --------------------------------------------------------------------------
# CSV loading, including farm-c's column set
# --------------------------------------------------------------------------


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return str(path)


def test_load_observations_reads_the_python_csv_shape(tmp_path):
    path = _write_csv(
        tmp_path / "python.csv",
        [
            "strategy",
            "seed",
            "replicate_id",
            "days_simulated",
            "final_money",
            "net_profit",
            "bankrupt",
            "bankruptcy_day",
            "minimum_cash_balance",
            "avg_profit_per_day",
            "first_upgrade_day",
            "crop_loss_rate",
        ],
        [
            ["alpha", 1, 0, 30, "120.5", "60.5", "False", "", "-3.0", "2.0167", "4", "12.5"],
            ["alpha", 2, 1, 12, "0.0", "-60.0", "True", "12", "-9.0", "-5.0", "", ""],
        ],
    )
    observations = load_observations(path)["alpha"]
    assert [o.bankrupt for o in observations] == [False, True]
    assert observations[0].replicate_id == 0
    assert observations[1].bankruptcy_day == 12
    assert observations[0].crop_loss_rate == pytest.approx(12.5)
    assert observations[1].first_upgrade_day is None


def test_load_observations_reads_the_farm_c_csv_shape(tmp_path):
    # farm-c writes 0/1 for bankrupt, has no minimum_cash_balance or
    # avg_profit_per_day column, and records no upgrade days at all.
    path = _write_csv(
        tmp_path / "farmc.csv",
        [
            "strategy",
            "seed",
            "days_simulated",
            "final_money",
            "total_revenue",
            "total_expenses",
            "net_profit",
            "bankrupt",
            "bankruptcy_day",
            "lowest_money",
        ],
        [
            ["alpha", 7, 30, "150", "300", "150", "90", "0", "", "-2"],
            ["alpha", 8, 9, "0", "10", "70", "-60", "1", "9", "-11"],
        ],
    )
    observations = load_observations(path)["alpha"]
    assert [o.bankrupt for o in observations] == [False, True]
    assert observations[0].minimum_cash_balance == -2.0, "falls back to lowest_money"
    assert observations[0].avg_profit_per_day == pytest.approx(90 / 30), "recomputed per run"
    assert observations[0].first_upgrade_day is None
    # An estimand over a column the producer never wrote must be undefined,
    # not a cohort of confident zeros.
    assert observed_values(observations, "expected_crop_loss_rate") == []


def test_analyze_csv_names_its_cohorts_and_conventions(tmp_path):
    rows = []
    for index in range(40):
        bankrupt = index % 4 == 0
        rows.append(
            [
                "alpha",
                index,
                index,
                30 if not bankrupt else 10,
                0.0 if bankrupt else float(100 + index),
                -50.0 if bankrupt else 50.0,
                str(bankrupt),
                10 if bankrupt else "",
                -5.0,
                1.0,
                "",
                "",
            ]
        )
    path = _write_csv(
        tmp_path / "run_results.csv",
        [
            "strategy",
            "seed",
            "replicate_id",
            "days_simulated",
            "final_money",
            "net_profit",
            "bankrupt",
            "bankruptcy_day",
            "minimum_cash_balance",
            "avg_profit_per_day",
            "first_upgrade_day",
            "crop_loss_rate",
        ],
        rows,
    )
    document = analyze_csv(path, horizon_days=30, replications=100, base_seed=5)
    entry = document["strategies"]["alpha"]
    assert document["quantile_convention"] == EMPIRICAL_QUANTILE_CONVENTION
    assert document["exact"] is True
    assert set(entry["cohorts"]) == {"all_runs", "survivors", "bankrupt", "bankruptcy_day"}
    assert entry["cohorts"]["all_runs"]["count"] == 40
    assert entry["cohorts"]["bankrupt"]["count"] == 10
    assert entry["cohorts"]["survivors"]["count"] == 30
    assert set(entry["quantile_estimates"]) == {
        f"p{int(100 * p)}" if p != 0.05 else "p5" for p in DEFAULT_QUANTILE_PROBABILITIES
    }
    assert entry["time_to_bankruptcy"]["censored"] == 30
    assert entry["tail_probabilities"]["below_0"]["value"] == pytest.approx(0.0)


def test_exact_quantiles_are_unaffected_by_the_reservoir_capacity(tmp_path):
    """A cohort far larger than MEDIAN_RESERVOIR_CAPACITY still gets an exact
    median here -- the whole reason this module reads the CSV."""
    from metrics.aggregate_results import MEDIAN_RESERVOIR_CAPACITY

    size = MEDIAN_RESERVOIR_CAPACITY * 2 + 1
    values = [float(v) for v in range(size)]
    assert quantile(values, 0.5) == float((size + 1) // 2 - 1)
    assert describe(values)["quantiles"]["p50"] == quantile(values, 0.5)
