"""Reference tests for metrics/inference.py.

Every interval here is checked against a value computed independently -- a
published table, a hand-evaluated formula, or `statistics` -- rather than
against this module's own output. A self-consistent statistics layer that is
uniformly wrong is exactly the failure these tests exist to catch.
"""

import math
import statistics

import pytest

from metrics.inference import (
    BernoulliAccumulator,
    Estimate,
    InferenceAccumulator,
    MomentAccumulator,
    beta_quantile,
    bootstrap_interval,
    bootstrap_paired_interval,
    clopper_pearson_interval,
    derive_analysis_seed,
    mean_interval,
    normal_quantile,
    proportion_interval,
    publish_float,
    regularized_incomplete_beta,
    student_t_cdf,
    student_t_quantile,
    wilson_interval,
)

# --------------------------------------------------------------------------
# Special functions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "df,expected",
    [(1, 12.7062), (2, 4.3027), (5, 2.5706), (10, 2.2281), (30, 2.0423), (100, 1.9840)],
)
def test_student_t_quantile_matches_published_table(df, expected):
    assert student_t_quantile(0.975, df) == pytest.approx(expected, abs=5e-5)


def test_student_t_quantile_converges_to_normal():
    assert student_t_quantile(0.975, 1e9) == pytest.approx(normal_quantile(0.975), abs=1e-6)


def test_student_t_quantile_is_symmetric():
    assert student_t_quantile(0.025, 7) == pytest.approx(-student_t_quantile(0.975, 7), abs=1e-12)


def test_student_t_cdf_inverts_the_quantile():
    for df in (1, 3, 12, 60):
        for p in (0.01, 0.25, 0.5, 0.9, 0.99):
            assert student_t_cdf(student_t_quantile(p, df), df) == pytest.approx(p, abs=1e-9)


def test_regularized_incomplete_beta_matches_known_values():
    # I_x(a, b) with a = b = 1 is just x; with a = 1 it is 1 - (1 - x)^b.
    assert regularized_incomplete_beta(1, 1, 0.37) == pytest.approx(0.37, abs=1e-12)
    assert regularized_incomplete_beta(1, 3, 0.5) == pytest.approx(1 - 0.5**3, abs=1e-12)
    assert regularized_incomplete_beta(2, 2, 0.5) == pytest.approx(0.5, abs=1e-12)


def test_beta_quantile_inverts_its_cdf():
    for a, b in ((0.5, 0.5), (2, 5), (19, 81)):
        for p in (0.025, 0.5, 0.975):
            x = beta_quantile(p, a, b)
            assert regularized_incomplete_beta(a, b, x) == pytest.approx(p, abs=1e-9)


# --------------------------------------------------------------------------
# Moment accumulator
# --------------------------------------------------------------------------


def test_moment_accumulator_matches_statistics_module():
    values = [3.2, -8.0, 15.5, 0.0, 7.125, 99.9, -0.5]
    accumulator = MomentAccumulator()
    for value in values:
        accumulator.add(value)
    assert accumulator.count == len(values)
    assert accumulator.mean() == pytest.approx(statistics.fmean(values), abs=1e-12)
    assert accumulator.variance() == pytest.approx(statistics.variance(values), rel=1e-12)
    assert accumulator.stdev() == pytest.approx(statistics.stdev(values), rel=1e-12)
    assert accumulator.standard_error() == pytest.approx(
        statistics.stdev(values) / math.sqrt(len(values)), rel=1e-12
    )
    assert accumulator.minimum == min(values)
    assert accumulator.maximum == max(values)


def test_moment_accumulator_stays_accurate_with_a_large_offset():
    # Naive sum-of-squares loses catastrophically here; Welford does not.
    values = [1e9 + delta for delta in (1, 2, 3, 4, 5)]
    accumulator = MomentAccumulator()
    for value in values:
        accumulator.add(value)
    assert accumulator.variance() == pytest.approx(statistics.variance(values), rel=1e-9)


def test_moment_accumulator_treats_none_as_not_observed():
    accumulator = MomentAccumulator()
    for value in (1.0, None, 3.0):
        accumulator.add(value)
    assert accumulator.count == 2
    assert accumulator.mean() == pytest.approx(2.0)


def test_moment_accumulator_is_undefined_below_two_observations():
    empty = MomentAccumulator()
    assert empty.mean() is None and empty.variance() is None and empty.standard_error() is None
    single = MomentAccumulator()
    single.add(5.0)
    assert single.mean() == 5.0
    assert single.variance() is None, "one observation has no measured spread, not zero spread"


def test_moment_accumulator_merge_matches_a_single_pass():
    left_values = [1.0, 2.0, 3.5]
    right_values = [10.0, -4.0]
    left, right, whole = MomentAccumulator(), MomentAccumulator(), MomentAccumulator()
    for value in left_values:
        left.add(value)
    for value in right_values:
        right.add(value)
    for value in left_values + right_values:
        whole.add(value)
    merged = left.merge(right)
    assert merged.count == whole.count
    assert merged.mean() == pytest.approx(whole.mean(), rel=1e-12)
    assert merged.variance() == pytest.approx(whole.variance(), rel=1e-12)


def test_snapshot_is_non_destructive():
    accumulator = MomentAccumulator()
    for value in (1.0, 2.0, 3.0):
        accumulator.add(value)
    first = accumulator.snapshot()
    second = accumulator.snapshot()
    assert first == second
    accumulator.add(4.0)
    assert accumulator.snapshot()["count"] == 4


# --------------------------------------------------------------------------
# Mean intervals
# --------------------------------------------------------------------------


def test_student_t_interval_matches_hand_calculation():
    values = [10.0, 12.0, 14.0, 16.0, 18.0]
    accumulator = MomentAccumulator()
    for value in values:
        accumulator.add(value)
    estimate = mean_interval(accumulator, "mean", 0.95)
    critical = student_t_quantile(0.975, 4)
    margin = critical * statistics.stdev(values) / math.sqrt(5)
    assert estimate.value == pytest.approx(14.0)
    assert estimate.lower == pytest.approx(14.0 - margin, rel=1e-12)
    assert estimate.upper == pytest.approx(14.0 + margin, rel=1e-12)
    assert estimate.half_width == pytest.approx(margin, rel=1e-12)


def test_normal_interval_is_narrower_than_student_t_at_small_n():
    accumulator = MomentAccumulator()
    for value in (1.0, 2.0, 3.0, 4.0):
        accumulator.add(value)
    assert (
        mean_interval(accumulator, method="normal").half_width
        < mean_interval(accumulator, method="student_t").half_width
    )


def test_mean_interval_is_undefined_for_empty_and_single_cohorts():
    empty = mean_interval(MomentAccumulator())
    assert empty.value is None and empty.lower is None and empty.defined is False
    single = MomentAccumulator()
    single.add(3.0)
    one = mean_interval(single)
    assert one.value == 3.0
    assert one.lower is None and one.upper is None
    assert "fewer than two" in one.notes


def test_unknown_methods_and_bad_confidence_are_rejected():
    accumulator = MomentAccumulator()
    accumulator.add(1.0)
    accumulator.add(2.0)
    with pytest.raises(ValueError):
        mean_interval(accumulator, method="jackknife")
    with pytest.raises(ValueError):
        mean_interval(accumulator, confidence=1.0)


# --------------------------------------------------------------------------
# Proportion intervals
# --------------------------------------------------------------------------


def test_wilson_interval_matches_hand_calculation():
    lower, upper = wilson_interval(5, 20, 0.95)
    z = normal_quantile(0.975)
    p = 0.25
    denominator = 1 + z * z / 20
    center = (p + z * z / 40) / denominator
    margin = z * math.sqrt(p * (1 - p) / 20 + z * z / 1600) / denominator
    assert lower == pytest.approx(center - margin, rel=1e-12)
    assert upper == pytest.approx(center + margin, rel=1e-12)


def test_wilson_interval_handles_all_success_and_all_failure():
    zero_lower, zero_upper = wilson_interval(0, 30)
    assert zero_lower == 0.0, "0/n must bound below at exactly zero, not a float residue"
    assert 0.0 < zero_upper < 1.0, "0/n must not produce a degenerate zero-width interval"
    all_lower, all_upper = wilson_interval(30, 30)
    assert all_upper == 1.0
    assert 0.0 < all_lower < 1.0


def test_clopper_pearson_matches_published_example():
    # 3 successes in 10 trials -> (0.0667, 0.6525) at 95%.
    lower, upper = clopper_pearson_interval(3, 10)
    assert lower == pytest.approx(0.0667, abs=1e-4)
    assert upper == pytest.approx(0.6525, abs=1e-4)


def test_clopper_pearson_is_conservative_relative_to_wilson():
    wilson_lower, wilson_upper = wilson_interval(7, 40)
    exact_lower, exact_upper = clopper_pearson_interval(7, 40)
    assert exact_lower <= wilson_lower and exact_upper >= wilson_upper


def test_proportion_interval_is_undefined_with_no_trials():
    estimate = proportion_interval(BernoulliAccumulator())
    assert estimate.value is None and estimate.lower is None
    assert estimate.notes == "no trials"


def test_bernoulli_accumulator_counts_and_merges():
    left = BernoulliAccumulator()
    for flag in (True, False, True):
        left.add(flag)
    right = BernoulliAccumulator()
    right.add(False)
    merged = left.merge(right)
    assert (merged.successes, merged.trials, merged.failures) == (2, 4, 2)
    assert merged.proportion() == 0.5


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------


def test_bootstrap_is_deterministic_for_a_fixed_analysis_seed():
    values = [float(v) for v in range(50)]
    first = bootstrap_interval(values, analysis_seed=1234, replications=200)
    second = bootstrap_interval(values, analysis_seed=1234, replications=200)
    assert (first.lower, first.upper) == (second.lower, second.upper)
    different = bootstrap_interval(values, analysis_seed=99, replications=200)
    assert (different.lower, different.upper) != (first.lower, first.upper)


def test_bootstrap_brackets_the_mean_of_a_tight_sample():
    values = [10.0] * 20 + [11.0] * 20
    estimate = bootstrap_interval(values, analysis_seed=7, replications=500)
    assert estimate.lower <= estimate.value <= estimate.upper
    assert estimate.lower >= 10.0 and estimate.upper <= 11.0


def test_bootstrap_handles_degenerate_samples():
    assert bootstrap_interval([], analysis_seed=1).value is None
    single = bootstrap_interval([4.0], analysis_seed=1)
    assert single.value == 4.0 and single.lower is None


def test_paired_bootstrap_uses_the_difference_stream():
    differences = [1.0, 1.2, 0.9, 1.1, 1.05] * 6
    estimate = bootstrap_paired_interval(differences, analysis_seed=3, replications=400)
    assert estimate.lower > 0.0, "a consistently positive paired difference excludes zero"


def test_derive_analysis_seed_is_stable_and_distinct_per_estimand():
    assert derive_analysis_seed(42, "expected_final_money") == derive_analysis_seed(
        42, "expected_final_money"
    )
    assert derive_analysis_seed(42, "expected_final_money") != derive_analysis_seed(
        42, "bankruptcy_probability"
    )
    assert derive_analysis_seed(None, "x") == derive_analysis_seed(None, "x")


# --------------------------------------------------------------------------
# Publication view
# --------------------------------------------------------------------------


def test_publish_float_quantizes_only_floats():
    assert publish_float(1 / 3) == float("%.12g" % (1 / 3))
    assert publish_float(None) is None
    assert publish_float(True) is True
    assert publish_float(7) == 7


def test_estimate_relative_half_width_is_undefined_near_zero():
    estimate = Estimate(estimand="x", value=1e-15, lower=-1.0, upper=1.0)
    assert estimate.half_width == 1.0
    assert estimate.relative_half_width is None


# --------------------------------------------------------------------------
# Estimand-driven accumulation
# --------------------------------------------------------------------------


class _Run:
    def __init__(self, **fields):
        self.final_money = fields.get("final_money", 0.0)
        self.bankrupt = fields.get("bankrupt", False)
        self.avg_profit_per_day = fields.get("avg_profit_per_day", 0.0)
        self.bankruptcy_day = fields.get("bankruptcy_day")
        self.minimum_cash_balance = fields.get("minimum_cash_balance", 0.0)
        self.first_upgrade_day = fields.get("first_upgrade_day")
        self.crop_loss_rate = fields.get("crop_loss_rate")


def test_inference_accumulator_respects_cohorts():
    accumulator = InferenceAccumulator()
    accumulator.add(_Run(final_money=100.0, bankrupt=False))
    accumulator.add(_Run(final_money=0.0, bankrupt=True, bankruptcy_day=12))
    accumulator.add(_Run(final_money=50.0, bankrupt=False))

    estimates = accumulator.estimates()
    assert estimates["expected_final_money"].n == 3
    assert estimates["expected_final_money"].value == pytest.approx(50.0)
    assert estimates["expected_final_money_survivors"].n == 2
    assert estimates["expected_final_money_survivors"].value == pytest.approx(75.0)
    assert estimates["conditional_bankruptcy_day"].n == 1
    assert estimates["bankruptcy_probability"].value == pytest.approx(1 / 3)


def test_inference_accumulator_skips_quantile_estimands():
    accumulator = InferenceAccumulator(["expected_final_money", "final_money_quantile"])
    assert "final_money_quantile" not in accumulator.accumulators


def test_inference_accumulator_merge_matches_a_single_pass():
    runs = [_Run(final_money=float(i), bankrupt=i % 3 == 0) for i in range(10)]
    whole = InferenceAccumulator()
    for run in runs:
        whole.add(run)
    left, right = InferenceAccumulator(), InferenceAccumulator()
    for run in runs[:4]:
        left.add(run)
    for run in runs[4:]:
        right.add(run)
    merged = left.merge(right)
    assert merged.estimate("expected_final_money").value == pytest.approx(
        whole.estimate("expected_final_money").value, rel=1e-12
    )
    assert merged.estimate("bankruptcy_probability").n == whole.estimate("bankruptcy_probability").n
