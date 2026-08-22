"""Formal inference primitives: moments, standard errors, confidence intervals.

This module is the *only* place a confidence interval is computed. Everything
downstream (report, view, dashboard, comparisons, adaptive stopping) consumes
`Estimate` objects from here rather than re-deriving a bound, exactly as
`metrics/aggregate_results.py` is the single source of truth for a descriptive
number.

Three deliberate boundaries:

* **Nothing here touches the simulation.** No `random.Random` instance in this
  module is ever the simulator's; bootstrap streams are derived from an
  explicit analysis seed (`derive_analysis_seed`) so inference consumes zero
  simulation RNG draws and cannot perturb a recorded seed's trajectory.
* **Undefined is not zero.** An interval over fewer than two observations is
  `None` bounds, not a zero-width interval; a rate no run observed is `None`,
  not 0%. This mirrors `_MeanAccumulator`'s `add(None)` policy.
* **Stdlib only.** `statistics.NormalDist` gives the normal quantile; the
  Student-t and Beta quantiles are implemented here (regularized incomplete
  beta + bisection) rather than pulling in SciPy, which the rest of the
  simulator does not depend on. They are checked against published table
  values in `tests/test_inference.py`.

Accumulators are mergeable (`merge`) and snapshottable (`snapshot`) so a
parallel batch and an adaptive checkpoint can both use them without
recomputing from raw observations.
"""

import hashlib
import math
import random
import statistics
from dataclasses import asdict, dataclass, field

# Bumped when a method here changes in a way that moves a published number.
INFERENCE_VERSION = "farm-inference-v1"

DEFAULT_CONFIDENCE = 0.95
DEFAULT_BOOTSTRAP_REPLICATIONS = 2000

# Significant digits every float carries when it is *published* (summary.json,
# convergence.json). Live accumulators and stopping rules keep full precision;
# only the artifact is quantized, and for a specific reason: Welford's M2
# recurrence is order-dependent in its last bits, so a standard error computed
# from results arriving in a different order differs by ~1e-16 relative. That
# is meaningless statistically but it makes the artifact non-reproducible
# across worker counts and feed orders -- a property
# `tests/test_aggregate_results.py::test_aggregate_is_order_independent`
# rightly pins. Twelve digits is far beyond anything downstream reads (the
# report prints two) and four orders of magnitude clear of the noise.
PUBLISHED_PRECISION_DIGITS = 12


# --------------------------------------------------------------------------
# Special functions (stdlib-only; SciPy is not a dependency of this repo)
# --------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b) -- the CDF of a Beta(a, b) distribution at x."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return (
        1.0
        - math.exp(
            math.lgamma(a + b)
            - math.lgamma(a)
            - math.lgamma(b)
            + b * math.log1p(-x)
            + a * math.log(x)
        )
        * _betacf(b, a, 1.0 - x)
        / b
    )


def beta_quantile(p: float, a: float, b: float) -> float:
    """Inverse Beta CDF by bisection.

    Bisection rather than Newton: it cannot diverge for the extreme shape
    parameters a Clopper-Pearson bound on a 0/n or n/n cohort produces, and
    100 halvings of [0, 1] is already below double precision.
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if regularized_incomplete_beta(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return 0.5 * (lo + hi)


def student_t_cdf(t: float, df: float) -> float:
    """CDF of Student's t, from the same incomplete-beta identity as the
    quantile above -- so a p-value and a confidence bound computed here can
    never disagree about the distribution they came from."""
    if df <= 0:
        raise ValueError("df must be positive")
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    x = df / (df + t * t)
    tail = 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def normal_quantile(p: float) -> float:
    """Standard normal inverse CDF."""
    return statistics.NormalDist().inv_cdf(p)


def student_t_quantile(p: float, df: float) -> float:
    """Inverse CDF of Student's t with `df` degrees of freedom.

    Derived from the Beta quantile via the standard identity
    I_{df/(df+t^2)}(df/2, 1/2) = 2 * P(T > |t|), which holds for any df > 0
    and needs no series special-cased by df.
    """
    if df <= 0:
        raise ValueError("df must be positive")
    if not 0.0 < p < 1.0:
        raise ValueError("p must be strictly inside (0, 1)")
    if p == 0.5:
        return 0.0
    if math.isinf(df):
        return normal_quantile(p)
    tail = 2.0 * min(p, 1.0 - p)
    x = beta_quantile(tail, df / 2.0, 0.5)
    if x <= 0.0:
        return math.inf if p > 0.5 else -math.inf
    t = math.sqrt(df * (1.0 - x) / x)
    return t if p > 0.5 else -t


# --------------------------------------------------------------------------
# Deterministic analysis seeds
# --------------------------------------------------------------------------


def derive_analysis_seed(base_seed, *parts) -> int:
    """Derive a stable 64-bit analysis seed from a base seed and label parts.

    Bootstrap resampling must be reproducible without being the simulation's
    RNG, and two different estimands must not share a resampling stream (a
    shared stream would correlate their intervals for no reason). Hashing the
    base seed together with the estimand id gives both properties, and gives
    them independently of dict iteration order or how many estimands ran
    before this one.

    `hashlib.blake2b`, never `hash()`: PYTHONHASHSEED randomizes `hash()` for
    strings, so a `hash()`-derived stream would not reproduce across
    processes -- which is exactly what a published analysis seed promises.
    """
    payload = "|".join(["" if base_seed is None else str(base_seed), *(str(p) for p in parts)])
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


# --------------------------------------------------------------------------
# Estimate value object
# --------------------------------------------------------------------------


@dataclass
class Estimate:
    """One inferential result, always carrying the provenance to read it.

    `n` is the *effective* sample count -- the number of observations that
    actually entered this estimate, which is not the batch's run count for a
    conditional cohort (survivors only) or a ratio some runs never observed.
    Section 1 of the statistical-analysis plan requires it on every estimate
    for exactly that reason.
    """

    estimand: str
    value: float | None = None
    n: int = 0
    method: str = "undefined"
    confidence: float = DEFAULT_CONFIDENCE
    lower: float | None = None
    upper: float | None = None
    stdev: float | None = None
    standard_error: float | None = None
    population: str | None = None
    unit: str | None = None
    notes: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def half_width(self) -> float | None:
        if self.lower is None or self.upper is None:
            return None
        if math.isinf(self.lower) or math.isinf(self.upper):
            return math.inf
        return (self.upper - self.lower) / 2.0

    @property
    def relative_half_width(self) -> float | None:
        """Half-width as a fraction of |estimate|, or None near zero.

        Deliberately undefined when the estimate is within `_NEAR_ZERO` of
        zero: relative precision has no meaning there and would otherwise
        report a huge or infinite number that an adaptive stopping rule
        would chase forever (plan Section 4).
        """
        half = self.half_width
        if half is None or self.value is None or abs(self.value) < _NEAR_ZERO:
            return None
        return half / abs(self.value)

    @property
    def defined(self) -> bool:
        return self.lower is not None and self.upper is not None

    def to_dict(self) -> dict:
        """Publication view: floats quantized to PUBLISHED_PRECISION_DIGITS.

        The `Estimate` itself keeps full precision -- an adaptive stopping
        rule compares `half_width` on the object, never on this dict.
        """
        doc = publish_mapping(asdict(self))
        doc["half_width"] = publish_float(self.half_width)
        doc["relative_half_width"] = publish_float(self.relative_half_width)
        if not doc["extra"]:
            doc.pop("extra")
        if doc.get("notes") is None:
            doc.pop("notes")
        return doc


_NEAR_ZERO = 1e-9


def publish_float(value, digits: int = PUBLISHED_PRECISION_DIGITS):
    """Quantize a float for publication. Passes None, bools and ints through."""
    if value is None or isinstance(value, bool) or not isinstance(value, float):
        return value
    if math.isnan(value) or math.isinf(value):
        return value
    return float(f"%.{digits}g" % value)


def publish_mapping(mapping: dict, digits: int = PUBLISHED_PRECISION_DIGITS) -> dict:
    """Recursively quantize every float in a document destined for an artifact."""
    out = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            out[key] = publish_mapping(value, digits)
        elif isinstance(value, list):
            out[key] = [
                publish_mapping(v, digits) if isinstance(v, dict) else publish_float(v, digits)
                for v in value
            ]
        else:
            out[key] = publish_float(value, digits)
    return out


# --------------------------------------------------------------------------
# Streaming accumulators
# --------------------------------------------------------------------------


class MomentAccumulator:
    """Streaming count/mean/M2 with a Neumaier-compensated running total.

    Two summations rather than one on purpose. Welford's M2 recurrence gives
    a numerically stable *variance* in one pass, but its incremental mean
    drifts slightly from `statistics.mean` for long streams; the compensated
    total reproduces `metrics/aggregate_results._MeanAccumulator` exactly, so
    `mean()` here and `avg_final_money` there cannot disagree. M2 is carried
    alongside purely for the variance.

    `add(None)` is a no-op (not a zero), matching the aggregator's policy for
    ratios that some runs never observe.
    """

    __slots__ = ("count", "_total", "_comp", "_welford_mean", "m2", "minimum", "maximum")

    def __init__(self):
        self.count = 0
        self._total = 0.0
        self._comp = 0.0
        self._welford_mean = 0.0
        self.m2 = 0.0
        self.minimum = None
        self.maximum = None

    def add(self, value) -> None:
        if value is None:
            return
        value = float(value)
        self.count += 1
        total = self._total + value
        if abs(self._total) >= abs(value):
            self._comp += (self._total - total) + value
        else:
            self._comp += (value - total) + self._total
        self._total = total

        delta = value - self._welford_mean
        self._welford_mean += delta / self.count
        self.m2 += delta * (value - self._welford_mean)

        if self.minimum is None or value < self.minimum:
            self.minimum = value
        if self.maximum is None or value > self.maximum:
            self.maximum = value

    def merge(self, other: "MomentAccumulator") -> "MomentAccumulator":
        """Chan-Golub-LeVeque parallel combination, returning a new accumulator."""
        merged = MomentAccumulator()
        if self.count == 0:
            other._copy_into(merged)
            return merged
        if other.count == 0:
            self._copy_into(merged)
            return merged
        n = self.count + other.count
        delta = other._welford_mean - self._welford_mean
        merged.count = n
        merged._welford_mean = self._welford_mean + delta * other.count / n
        merged.m2 = self.m2 + other.m2 + delta * delta * self.count * other.count / n
        merged._total = self._total + other._total
        merged._comp = self._comp + other._comp
        merged.minimum = min(x for x in (self.minimum, other.minimum) if x is not None)
        merged.maximum = max(x for x in (self.maximum, other.maximum) if x is not None)
        return merged

    def _copy_into(self, target: "MomentAccumulator") -> None:
        target.count = self.count
        target._total = self._total
        target._comp = self._comp
        target._welford_mean = self._welford_mean
        target.m2 = self.m2
        target.minimum = self.minimum
        target.maximum = self.maximum

    def mean(self):
        return (self._total + self._comp) / self.count if self.count else None

    def variance(self):
        """Sample variance (Bessel-corrected). None for fewer than 2 values."""
        if self.count < 2:
            return None
        return max(0.0, self.m2 / (self.count - 1))

    def stdev(self):
        variance = self.variance()
        return None if variance is None else math.sqrt(variance)

    def standard_error(self):
        stdev = self.stdev()
        return None if stdev is None else stdev / math.sqrt(self.count)

    def snapshot(self) -> dict:
        """Non-destructive sufficient statistics for a checkpoint record.

        Sufficient statistics and nothing else -- never a pickled object, per
        plan Section 9 -- so a persisted checkpoint stays readable by a later
        version of this module.
        """
        return {
            "count": self.count,
            "mean": self.mean(),
            "m2": self.m2,
            "variance": self.variance(),
            "stdev": self.stdev(),
            "standard_error": self.standard_error(),
            "min": self.minimum,
            "max": self.maximum,
        }

    def interval(
        self,
        estimand: str = "mean",
        confidence: float = DEFAULT_CONFIDENCE,
        method: str = "student_t",
        **metadata,
    ) -> Estimate:
        return mean_interval(self, estimand, confidence, method, **metadata)


class BernoulliAccumulator:
    """Streaming successes/trials for a proportion estimand."""

    __slots__ = ("successes", "trials")

    def __init__(self, successes: int = 0, trials: int = 0):
        self.successes = successes
        self.trials = trials

    def add(self, success) -> None:
        if success is None:
            return
        self.trials += 1
        if success:
            self.successes += 1

    def merge(self, other: "BernoulliAccumulator") -> "BernoulliAccumulator":
        return BernoulliAccumulator(self.successes + other.successes, self.trials + other.trials)

    @property
    def failures(self) -> int:
        return self.trials - self.successes

    def proportion(self):
        return self.successes / self.trials if self.trials else None

    def snapshot(self) -> dict:
        return {
            "successes": self.successes,
            "failures": self.failures,
            "trials": self.trials,
            "proportion": self.proportion(),
        }

    def interval(
        self,
        estimand: str = "proportion",
        confidence: float = DEFAULT_CONFIDENCE,
        method: str = "wilson",
        **metadata,
    ) -> Estimate:
        return proportion_interval(self, estimand, confidence, method, **metadata)


# --------------------------------------------------------------------------
# Interval methods
# --------------------------------------------------------------------------


def _check_confidence(confidence: float) -> None:
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly inside (0, 1)")


def mean_interval(
    accumulator: MomentAccumulator,
    estimand: str = "mean",
    confidence: float = DEFAULT_CONFIDENCE,
    method: str = "student_t",
    **metadata,
) -> Estimate:
    """Confidence interval for a population mean.

    Student-t by default and normal only on explicit request: with 500 runs
    the two agree to three decimals, but the default has to be the one that
    is still honest at n=5, which a normal interval is not.

    Fewer than two observations leaves the bounds `None` rather than
    collapsing to a zero-width interval around the single value -- a
    one-observation cohort has no measured spread, which is different from
    having measured a spread of zero.
    """
    _check_confidence(confidence)
    estimate = Estimate(
        estimand=estimand,
        value=accumulator.mean(),
        n=accumulator.count,
        confidence=confidence,
        stdev=accumulator.stdev(),
        standard_error=accumulator.standard_error(),
        method=method,
        **metadata,
    )
    if accumulator.count < 2:
        estimate.method = f"{method}_undefined"
        estimate.notes = "fewer than two observations"
        return estimate
    standard_error = accumulator.standard_error()
    if method == "student_t":
        critical = student_t_quantile(0.5 + confidence / 2.0, accumulator.count - 1)
    elif method == "normal":
        critical = normal_quantile(0.5 + confidence / 2.0)
    else:
        raise ValueError(f"unknown mean interval method: {method!r}")
    estimate.lower = estimate.value - critical * standard_error
    estimate.upper = estimate.value + critical * standard_error
    return estimate


def wilson_interval(successes: int, trials: int, confidence: float = DEFAULT_CONFIDENCE):
    """Wilson score interval bounds as a `(lower, upper)` pair in [0, 1].

    Wilson rather than Wald because a bankruptcy rate is routinely near 0 or
    1 here, where a Wald interval runs off the end of [0, 1] and has coverage
    far below its nominal level. Wilson stays inside [0, 1] by construction
    and is still defined at 0/n and n/n.
    """
    _check_confidence(confidence)
    if trials <= 0:
        return None, None
    z = normal_quantile(0.5 + confidence / 2.0)
    phat = successes / trials
    denominator = 1.0 + z * z / trials
    center = (phat + z * z / (2 * trials)) / denominator
    margin = (
        z * math.sqrt(phat * (1.0 - phat) / trials + z * z / (4.0 * trials * trials)) / denominator
    )
    # At 0/n the centre and the margin are algebraically equal, so the lower
    # bound is exactly 0; floating point leaves ~1e-17 behind instead. Pin the
    # exact endpoints rather than publishing that residue -- a bankruptcy rate
    # bounded below by 1.4e-17 reads as a real (if tiny) risk floor, which is
    # not what the arithmetic says.
    lower = 0.0 if successes == 0 else max(0.0, center - margin)
    upper = 1.0 if successes == trials else min(1.0, center + margin)
    return lower, upper


def clopper_pearson_interval(successes: int, trials: int, confidence: float = DEFAULT_CONFIDENCE):
    """Exact (Clopper-Pearson) binomial interval bounds.

    Conservative -- actual coverage is at least the nominal level, never
    below -- which is why it is offered for audit-sensitive reporting even
    though Wilson is the better everyday default.
    """
    _check_confidence(confidence)
    if trials <= 0:
        return None, None
    alpha = 1.0 - confidence
    lower = 0.0 if successes == 0 else beta_quantile(alpha / 2.0, successes, trials - successes + 1)
    upper = (
        1.0
        if successes == trials
        else beta_quantile(1.0 - alpha / 2.0, successes + 1, trials - successes)
    )
    return lower, upper


def proportion_interval(
    accumulator: BernoulliAccumulator,
    estimand: str = "proportion",
    confidence: float = DEFAULT_CONFIDENCE,
    method: str = "wilson",
    **metadata,
) -> Estimate:
    _check_confidence(confidence)
    trials, successes = accumulator.trials, accumulator.successes
    phat = accumulator.proportion()
    # Bernoulli population sd; for trials < 2 there is no sample sd to report.
    stdev = math.sqrt(phat * (1.0 - phat)) if phat is not None else None
    standard_error = (
        math.sqrt(phat * (1.0 - phat) / trials) if phat is not None and trials else None
    )
    estimate = Estimate(
        estimand=estimand,
        value=phat,
        n=trials,
        confidence=confidence,
        stdev=stdev,
        standard_error=standard_error,
        method=method,
        **metadata,
    )
    if trials <= 0:
        estimate.method = f"{method}_undefined"
        estimate.notes = "no trials"
        return estimate
    if method == "wilson":
        estimate.lower, estimate.upper = wilson_interval(successes, trials, confidence)
    elif method == "clopper_pearson":
        estimate.lower, estimate.upper = clopper_pearson_interval(successes, trials, confidence)
    elif method == "normal":
        z = normal_quantile(0.5 + confidence / 2.0)
        estimate.lower = max(0.0, phat - z * standard_error)
        estimate.upper = min(1.0, phat + z * standard_error)
    else:
        raise ValueError(f"unknown proportion interval method: {method!r}")
    estimate.extra.setdefault("successes", successes)
    estimate.extra.setdefault("trials", trials)
    return estimate


# --------------------------------------------------------------------------
# Deterministic bootstrap
# --------------------------------------------------------------------------


def _percentile_of_sorted(sorted_values: list, p: float) -> float:
    """Inverse-CDF (type 1) percentile of an already-sorted list.

    Matches `metrics.distributions.EMPIRICAL_QUANTILE_CONVENTION` and the
    estimand registry's own definition of a quantile,
    Q_p = inf {m : F(m) >= p}. Interpolating here instead would make the
    bootstrap's convention differ from the point estimate it brackets.
    """
    n = len(sorted_values)
    if n == 0:
        return None
    if p <= 0:
        return sorted_values[0]
    if p >= 1:
        return sorted_values[-1]
    index = math.ceil(p * n) - 1
    return sorted_values[max(0, min(n - 1, index))]


def bootstrap_interval(
    values,
    statistic=None,
    estimand: str = "bootstrap",
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    analysis_seed: int = 0,
    **metadata,
) -> Estimate:
    """Deterministic percentile bootstrap for an arbitrary statistic.

    The resampling stream is `random.Random(analysis_seed)` and nothing else,
    so a published analysis seed reproduces every bound exactly -- and it is
    a *separate* Random from the simulator's, so running an analysis can
    never advance a simulation stream (plan Section 4's "diagnostics do not
    consume simulation RNG draws").
    """
    _check_confidence(confidence)
    values = list(values)
    statistic = statistic or (lambda sample: statistics.fmean(sample))
    n = len(values)
    estimate = Estimate(
        estimand=estimand,
        n=n,
        confidence=confidence,
        method=f"percentile_bootstrap_{replications}",
        **metadata,
    )
    estimate.extra.setdefault("bootstrap_replications", replications)
    estimate.extra.setdefault("analysis_seed", analysis_seed)
    if n == 0:
        estimate.method = "percentile_bootstrap_undefined"
        estimate.notes = "no observations"
        return estimate
    estimate.value = statistic(values)
    if n < 2:
        estimate.method = "percentile_bootstrap_undefined"
        estimate.notes = "fewer than two observations"
        return estimate

    rng = random.Random(analysis_seed)
    replicates = []
    for _ in range(replications):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        replicates.append(statistic(sample))
    replicates.sort()
    alpha = 1.0 - confidence
    estimate.lower = _percentile_of_sorted(replicates, alpha / 2.0)
    estimate.upper = _percentile_of_sorted(replicates, 1.0 - alpha / 2.0)
    if len(replicates) > 1:
        estimate.standard_error = statistics.stdev(replicates)
    return estimate


def bootstrap_paired_interval(
    differences,
    estimand: str = "paired_difference",
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    analysis_seed: int = 0,
    **metadata,
) -> Estimate:
    """Percentile bootstrap over per-replicate differences.

    Resampling *pairs* (not the two arms independently) is what keeps the
    within-replicate correlation in the interval; resampling each arm on its
    own would throw away the variance reduction pairing exists to buy.
    """
    return bootstrap_interval(
        differences,
        statistic=lambda sample: statistics.fmean(sample),
        estimand=estimand,
        confidence=confidence,
        replications=replications,
        analysis_seed=analysis_seed,
        **metadata,
    )


# --------------------------------------------------------------------------
# Estimand-driven streaming inference
# --------------------------------------------------------------------------


class InferenceAccumulator:
    """One accumulator per estimand for a single cohort (usually a strategy).

    Streaming, so a multi-million-run batch never has to hold its own runs in
    memory to get an interval -- the same constraint that shaped
    `metrics/aggregate_results.py`. Quantile estimands are deliberately *not*
    accumulated here: an exact quantile needs the observations themselves, so
    it is computed from `run_results.csv` by `metrics/distributions.py` after
    the batch, and never from a bounded reservoir.
    """

    def __init__(self, estimand_ids=None, confidence: float = DEFAULT_CONFIDENCE):
        from metrics import estimands as estimand_registry

        if estimand_ids is None:
            estimand_ids = estimand_registry.DEFAULT_ESTIMANDS
        self.confidence = confidence
        self.estimands = []
        self.accumulators = {}
        for estimand_id in estimand_ids:
            estimand = estimand_registry.get(estimand_id)
            if estimand.kind == "quantile":
                continue  # exact quantiles come from the CSV, not a stream
            self.estimands.append(estimand)
            self.accumulators[estimand_id] = (
                BernoulliAccumulator() if estimand.kind == "proportion" else MomentAccumulator()
            )

    def add(self, run) -> None:
        for estimand in self.estimands:
            self.accumulators[estimand.id].add(estimand.observe(run))

    def merge(self, other: "InferenceAccumulator") -> "InferenceAccumulator":
        merged = InferenceAccumulator([e.id for e in self.estimands], confidence=self.confidence)
        for estimand_id, accumulator in self.accumulators.items():
            merged.accumulators[estimand_id] = accumulator.merge(other.accumulators[estimand_id])
        return merged

    def estimate(self, estimand_id: str, confidence: float | None = None) -> Estimate:
        from metrics import estimands as estimand_registry

        estimand = estimand_registry.get(estimand_id)
        accumulator = self.accumulators[estimand_id]
        return accumulator.interval(
            estimand=estimand_id,
            confidence=self.confidence if confidence is None else confidence,
            method=estimand.ci_method,
            population=estimand.population,
            unit=estimand.unit,
        )

    def estimates(self, confidence: float | None = None) -> dict:
        return {e.id: self.estimate(e.id, confidence) for e in self.estimands}

    def to_document(self, confidence: float | None = None) -> dict:
        """The `inference` block published per strategy in summary.json."""
        return {eid: estimate.to_dict() for eid, estimate in self.estimates(confidence).items()}

    def snapshot(self) -> dict:
        """Non-destructive sufficient statistics for every estimand."""
        return {eid: acc.snapshot() for eid, acc in self.accumulators.items()}
