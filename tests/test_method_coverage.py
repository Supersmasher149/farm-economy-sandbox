"""Method validation: do these intervals actually cover, at the rate claimed?

Section 10's "Method Validation" step. Every other test in this suite checks
that a formula was transcribed correctly; these check that the formula does
its advertised job, by drawing from distributions whose truth is known and
counting how often the interval contains it.

Coverage is itself a Monte Carlo estimate, so each assertion is a band, not a
point -- but the streams are fixed seeds, so a failure here is a real change
in behaviour rather than an unlucky day. The bands are set from the binomial
standard error of the replication count used (at 2000 replications and 95%
nominal, that is 0.49 percentage points, so +/-3 SE is about +/-1.5pp).
"""

import math
import random

import pytest

from metrics import distributions
from metrics.comparisons import compare_means_independent, compare_means_paired
from metrics.inference import (
    BernoulliAccumulator,
    MomentAccumulator,
    clopper_pearson_interval,
    mean_interval,
    wilson_interval,
)

CONFIDENCE = 0.95


def _covers(estimate, truth):
    if estimate.lower is None or estimate.upper is None:
        return False
    return estimate.lower <= truth <= estimate.upper


# --------------------------------------------------------------------------
# Normal outcomes: mean interval coverage
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [5, 25, 200])
def test_student_t_mean_interval_covers_at_its_nominal_rate(n):
    """Student-t is exact for normal data at every n, including n=5.

    This is the assertion that justifies the default: the small-sample case
    is where a normal interval fails, and it is also the case a 5-run
    diagnostic batch actually hits.
    """
    rng = random.Random(90210 + n)
    truth, sigma = 137.0, 42.0
    replications = 2000
    hits = 0
    for _ in range(replications):
        accumulator = MomentAccumulator()
        for _ in range(n):
            accumulator.add(rng.gauss(truth, sigma))
        hits += _covers(mean_interval(accumulator, confidence=CONFIDENCE), truth)
    coverage = hits / replications
    assert 0.935 <= coverage <= 0.965, f"n={n} coverage {coverage}"


def test_normal_interval_undercovers_at_small_n_where_student_t_does_not():
    """The reason `method="normal"` is opt-in and not the default.

    At n=5 the normal interval is visibly narrow: it misses the truth far
    more than 5% of the time, while the t-interval on the *same* samples
    lands on its nominal rate.
    """
    rng = random.Random(11235)
    truth, sigma, n, replications = 0.0, 1.0, 5, 2000
    normal_hits = student_hits = 0
    for _ in range(replications):
        accumulator = MomentAccumulator()
        for _ in range(n):
            accumulator.add(rng.gauss(truth, sigma))
        normal_hits += _covers(
            mean_interval(accumulator, confidence=CONFIDENCE, method="normal"), truth
        )
        student_hits += _covers(mean_interval(accumulator, confidence=CONFIDENCE), truth)
    normal_coverage = normal_hits / replications
    student_coverage = student_hits / replications
    assert normal_coverage < 0.90, normal_coverage
    assert 0.935 <= student_coverage <= 0.965, student_coverage


def test_mean_interval_coverage_survives_skewed_outcomes_at_batch_sizes():
    """Final money is not normal, so check the interval on a skewed draw.

    Lognormal at n=200 -- roughly a real batch -- still covers near nominal.
    The point of pinning this is that the t-interval's normality assumption
    is about the *sampling distribution of the mean*, which the CLT supplies
    here; the same test at n=5 would fail, which is why an adaptive minimum
    run count exists.
    """
    rng = random.Random(31415)
    mu, sigma, n, replications = 0.0, 1.0, 200, 2000
    truth = math.exp(mu + sigma * sigma / 2.0)
    hits = 0
    for _ in range(replications):
        accumulator = MomentAccumulator()
        for _ in range(n):
            accumulator.add(math.exp(rng.gauss(mu, sigma)))
        hits += _covers(mean_interval(accumulator, confidence=CONFIDENCE), truth)
    coverage = hits / replications
    assert 0.92 <= coverage <= 0.965, coverage


# --------------------------------------------------------------------------
# Bernoulli outcomes: bankruptcy-probability intervals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("p", [0.02, 0.15, 0.5])
def test_wilson_covers_near_nominal_including_rare_events(p):
    """Wilson at p=0.02 is the bankruptcy-rate case that motivated it.

    A Wald interval at this p and n is well below nominal and runs outside
    [0, 1]; Wilson stays inside and near its claimed rate. Discreteness means
    coverage oscillates rather than sitting exactly at 0.95, so the band is
    wider below than above.
    """
    rng = random.Random(2718 + int(p * 1000))
    n, replications = 100, 2000
    hits = 0
    for _ in range(replications):
        successes = sum(rng.random() < p for _ in range(n))
        lower, upper = wilson_interval(successes, n, confidence=CONFIDENCE)
        hits += lower <= p <= upper
    coverage = hits / replications
    assert 0.91 <= coverage <= 0.99, f"p={p} coverage {coverage}"


@pytest.mark.parametrize("p", [0.02, 0.15, 0.5])
def test_clopper_pearson_is_never_below_nominal(p):
    """The guarantee that makes it the audit-grade choice.

    Exact means conservative: coverage is at or above the nominal level for
    every p, paid for with a wider interval. If this ever drops below 0.95 the
    implementation is not the exact interval it claims to be.
    """
    rng = random.Random(16180 + int(p * 1000))
    n, replications = 100, 2000
    hits = 0
    for _ in range(replications):
        successes = sum(rng.random() < p for _ in range(n))
        lower, upper = clopper_pearson_interval(successes, n, confidence=CONFIDENCE)
        hits += lower <= p <= upper
    coverage = hits / replications
    assert coverage >= 0.95, f"p={p} coverage {coverage}"


def test_proportion_interval_coverage_matches_its_own_bounds_function():
    """The accumulator path and the bare bounds function must agree.

    Two entry points to one interval is exactly how a reporting layer drifts
    from an inference layer; this pins them together on real draws.
    """
    rng = random.Random(1729)
    for _ in range(200):
        accumulator = BernoulliAccumulator()
        for _ in range(60):
            accumulator.add(rng.random() < 0.3)
        estimate = accumulator.interval(confidence=CONFIDENCE)
        lower, upper = wilson_interval(accumulator.successes, accumulator.trials, CONFIDENCE)
        assert estimate.lower == lower
        assert estimate.upper == upper


# --------------------------------------------------------------------------
# Skewed distributions: bootstrap quantile intervals
# --------------------------------------------------------------------------


@pytest.mark.parametrize("probability", [0.5, 0.9])
def test_bootstrap_quantile_intervals_cover_a_skewed_truth(probability):
    """Percentile bootstrap on lognormal data, where the quantile is analytic.

    The bootstrap is the only interval here with no closed form to check
    against, so this is its only real validation. Coverage of a percentile
    bootstrap for a quantile is known to run somewhat below nominal at
    moderate n -- the band admits that rather than pretending otherwise, and
    a drop below it would mean the resampling itself broke.
    """
    from statistics import NormalDist

    rng = random.Random(5772 + int(probability * 100))
    mu, sigma, n, replications = 0.0, 1.0, 200, 300
    truth = math.exp(mu + sigma * NormalDist().inv_cdf(probability))
    hits = 0
    for replicate in range(replications):
        values = [math.exp(rng.gauss(mu, sigma)) for _ in range(n)]
        estimates = distributions.bootstrap_quantile_set(
            values,
            probabilities=(probability,),
            confidence=CONFIDENCE,
            replications=300,
            analysis_seed=replicate,
        )
        estimate = next(iter(estimates.values()))
        hits += _covers(estimate, truth)
    coverage = hits / replications
    assert 0.88 <= coverage <= 0.99, f"p={probability} coverage {coverage}"


# --------------------------------------------------------------------------
# Correlated paired samples: variance reduction
# --------------------------------------------------------------------------


def _bivariate(rng, n, rho, mean_a=0.0, mean_b=0.0, sigma=1.0):
    """Draw `n` correlated pairs with population correlation `rho`."""
    pairs = []
    root = math.sqrt(1.0 - rho * rho)
    for _ in range(n):
        z1, z2 = rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0)
        a = mean_a + sigma * z1
        b = mean_b + sigma * (rho * z1 + root * z2)
        pairs.append((a, b))
    return [p[0] for p in pairs], [p[1] for p in pairs]


@pytest.mark.parametrize("rho", [0.0, 0.5, 0.9])
def test_measured_variance_reduction_tracks_the_true_correlation(rho):
    """`variance_reduction` must recover the correlation that produced it.

    With equal variances the theoretical reduction is exactly rho, which is
    the cleanest possible check on the definition
    1 - Var(A-B) / (Var A + Var B). It is also the number the paired
    sampling plan is sold on, so a wrong sign or scale here would make the
    plan look useful when it is not.
    """
    rng = random.Random(4096 + int(rho * 100))
    values_a, values_b = _bivariate(rng, 4000, rho)
    comparison = compare_means_paired(
        values_a, values_b, "synthetic", "arm_a", "arm_b", bootstrap=False
    )
    assert comparison.correlation == pytest.approx(rho, abs=0.03)
    assert comparison.variance_reduction == pytest.approx(rho, abs=0.03)


def test_pairing_narrows_the_difference_interval_only_when_correlation_is_real():
    """Positive correlation buys precision; zero correlation does not.

    This is the synthetic control for the measured farm result, where most
    strategy pairs came out near rho=0 and paired intervals were *not*
    narrower. Here the mechanism is isolated: same arms, same n, only the
    correlation differs.
    """
    rng = random.Random(8192)
    n = 2000

    correlated_a, correlated_b = _bivariate(rng, n, 0.9)
    paired = compare_means_paired(
        correlated_a, correlated_b, "synthetic", "a", "b", bootstrap=False
    )
    independent = compare_means_independent(correlated_a, correlated_b, "synthetic", "a", "b")
    assert (paired.upper - paired.lower) < 0.45 * (independent.upper - independent.lower)

    flat_a, flat_b = _bivariate(rng, n, 0.0)
    paired_flat = compare_means_paired(flat_a, flat_b, "synthetic", "a", "b", bootstrap=False)
    independent_flat = compare_means_independent(flat_a, flat_b, "synthetic", "a", "b")
    ratio = (paired_flat.upper - paired_flat.lower) / (
        independent_flat.upper - independent_flat.lower
    )
    assert 0.97 <= ratio <= 1.03, ratio
