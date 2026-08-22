"""Exact distribution analysis over a completed batch's raw observations.

Everything here reads the *exact* per-run values from `run_results.csv` rather
than the bounded median reservoir in `metrics/aggregate_results.py`. That split
is the whole point of this module, and it is a two-tier storage policy, not an
inconsistency:

* **Streaming tier** -- `_DeterministicReservoir`, capacity 1,024, O(1) memory
  per cohort, already published as `median_approximate: true` once a cohort
  outgrows it. Fine for a dashboard median.
* **Exact tier** -- this module. A published batch has already written every
  run to CSV, so a quantile, an ECDF or a tail probability can be computed
  from the real observations. Formal inference uses this tier only.

**Quantile convention.** `Q_p = inf {m : F(m) >= p}` -- the inverse-CDF (type
1) convention, matching the estimand registry's own definition, so a bootstrap
interval brackets the same statistic the point estimate reports. The
interpolating "linear" (type 7) convention that numpy and `statistics.quantiles`
default to is available explicitly, because a p50 that no run actually achieved
is the wrong default for a metric whose unit is "one simulated run", but is the
right one when comparing against an external tool that uses it.

**Outliers are described, never removed.** A farm that ends a run 8 sigma above
the mean is a balance finding, not a data-quality problem; Tukey counts are
reported so the tail is visible, and every value stays in every statistic.
"""

import csv
import math
import random
import statistics

from metrics import estimands as estimand_registry
from metrics.inference import (
    DEFAULT_BOOTSTRAP_REPLICATIONS,
    DEFAULT_CONFIDENCE,
    BernoulliAccumulator,
    Estimate,
    _percentile_of_sorted,
    bootstrap_interval,
    derive_analysis_seed,
    proportion_interval,
    publish_mapping,
)

EMPIRICAL_QUANTILE_CONVENTION = "inverse_cdf"
QUANTILE_CONVENTIONS = ("inverse_cdf", "linear")
DEFAULT_QUANTILE_PROBABILITIES = (0.05, 0.25, 0.50, 0.75, 0.95)
# Cap on points written for an ECDF or survival curve. A 20,000-run batch has
# ~20,000 distinct final-money values; the curve is visually and analytically
# identical at 512 evenly spaced order statistics, and the artifact stays a
# readable size. Always flagged when it happens.
DEFAULT_CURVE_POINTS = 512

SKEWNESS_CONVENTION = "adjusted_fisher_pearson_g1"


# --------------------------------------------------------------------------
# Quantiles
# --------------------------------------------------------------------------


def quantile(values, p: float, convention: str = EMPIRICAL_QUANTILE_CONVENTION):
    """Empirical quantile under an explicitly named convention."""
    if convention not in QUANTILE_CONVENTIONS:
        raise ValueError(
            f"unknown quantile convention {convention!r}; expected one of {QUANTILE_CONVENTIONS}"
        )
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be in [0, 1]")
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    if n == 1:
        return ordered[0]
    if convention == "inverse_cdf":
        if p <= 0.0:
            return ordered[0]
        index = math.ceil(p * n) - 1
        return ordered[max(0, min(n - 1, index))]
    # "linear" (Hyndman-Fan type 7), for parity with numpy/statistics.
    position = (n - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def quantiles(values, probabilities=DEFAULT_QUANTILE_PROBABILITIES, convention=None) -> dict:
    convention = convention or EMPIRICAL_QUANTILE_CONVENTION
    ordered = sorted(values)
    return {_probability_key(p): quantile(ordered, p, convention) for p in probabilities}


def _probability_key(p: float) -> str:
    label = f"{100 * p:g}".replace(".", "_")
    return f"p{label}"


def quantile_estimate(
    values,
    p: float,
    estimand: str = "final_money_quantile",
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
    analysis_seed=None,
    convention: str = EMPIRICAL_QUANTILE_CONVENTION,
    **metadata,
) -> Estimate:
    """Deterministic percentile-bootstrap interval for an empirical quantile.

    A quantile has no closed-form standard error that survives a skewed,
    multi-modal outcome distribution -- which final money here is, since
    bankrupt and surviving runs are two different populations sharing an axis
    -- so the interval is bootstrapped. The stream is derived from the batch's
    base seed *and the estimand id*, so two estimands never share a resampling
    stream and every bound reproduces exactly from published metadata.
    """
    if analysis_seed is None:
        analysis_seed = derive_analysis_seed(base_seed, estimand, f"p={p}")
    estimate = bootstrap_interval(
        values,
        statistic=lambda sample: quantile(sample, p, convention),
        estimand=estimand,
        confidence=confidence,
        replications=replications,
        analysis_seed=analysis_seed,
        **metadata,
    )
    estimate.extra["quantile_p"] = p
    estimate.extra["quantile_convention"] = convention
    return estimate


# --------------------------------------------------------------------------
# Shape diagnostics
# --------------------------------------------------------------------------


def bootstrap_quantile_set(
    values,
    probabilities=DEFAULT_QUANTILE_PROBABILITIES,
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    analysis_seed: int = 0,
    convention: str = EMPIRICAL_QUANTILE_CONVENTION,
    estimand: str = "final_money_quantile",
    **metadata,
) -> dict:
    """Bootstrap every requested quantile from *one* resampling pass.

    Resampling once per replicate and reading all five quantiles off the same
    sorted resample is not just an optimization (five separate passes would be
    five times the work for eleven strategies): it also means p25 and p75 come
    from the same bootstrap world, so a published inter-quartile range cannot
    be assembled from two inconsistent resamples.

    `random.choices` + `sorted` rather than a per-draw Python loop, because
    both are C-level; the stream is still `random.Random(analysis_seed)`, so
    the result is bit-identical to drawing one index at a time in the same
    order and remains exactly reproducible from published metadata.
    """
    values = [float(v) for v in values if v is not None]
    n = len(values)
    results = {}
    if n == 0:
        for p in probabilities:
            estimate = Estimate(
                estimand=estimand,
                n=0,
                confidence=confidence,
                method="percentile_bootstrap_undefined",
                notes="no observations",
                **metadata,
            )
            estimate.extra.update({"quantile_p": p, "quantile_convention": convention})
            results[_probability_key(p)] = estimate
        return results

    ordered = sorted(values)
    point_estimates = {p: quantile(ordered, p, convention) for p in probabilities}
    if n < 2:
        for p in probabilities:
            estimate = Estimate(
                estimand=estimand,
                value=point_estimates[p],
                n=n,
                confidence=confidence,
                method="percentile_bootstrap_undefined",
                notes="fewer than two observations",
                **metadata,
            )
            estimate.extra.update({"quantile_p": p, "quantile_convention": convention})
            results[_probability_key(p)] = estimate
        return results

    rng = random.Random(analysis_seed)
    replicate_quantiles = {p: [] for p in probabilities}
    for _ in range(replications):
        resample = sorted(rng.choices(ordered, k=n))
        for p in probabilities:
            replicate_quantiles[p].append(quantile(resample, p, convention))

    alpha = 1.0 - confidence
    for p in probabilities:
        draws = sorted(replicate_quantiles[p])
        estimate = Estimate(
            estimand=estimand,
            value=point_estimates[p],
            n=n,
            confidence=confidence,
            method=f"percentile_bootstrap_{replications}",
            lower=_percentile_of_sorted(draws, alpha / 2.0),
            upper=_percentile_of_sorted(draws, 1.0 - alpha / 2.0),
            standard_error=statistics.stdev(draws) if len(draws) > 1 else None,
            **metadata,
        )
        estimate.extra.update(
            {
                "quantile_p": p,
                "quantile_convention": convention,
                "bootstrap_replications": replications,
                "analysis_seed": analysis_seed,
            }
        )
        results[_probability_key(p)] = estimate
    return results


def skewness(values):
    """Sample skewness, adjusted Fisher-Pearson standardized moment (G1).

    G1 = n/((n-1)(n-2)) * sum(((x - mean) / s)**3), with s the Bessel-corrected
    sample standard deviation -- the convention Excel's SKEW, R's e1071
    `type=2` and pandas all use. Named in the output because the unadjusted
    (g1) form differs by a factor of ~1.1 at n=20, which is enough to change a
    reader's mind about a tail.

    Undefined (None) below three observations, and for a constant sample:
    there is no shape to report when there is no spread.
    """
    n = len(values)
    if n < 3:
        return None
    mean = statistics.fmean(values)
    s = statistics.stdev(values)
    if s == 0:
        return None
    total = sum(((x - mean) / s) ** 3 for x in values)
    return n / ((n - 1) * (n - 2)) * total


def median_absolute_deviation(values):
    """MAD about the median -- an outlier-resistant spread, unscaled."""
    if not values:
        return None
    center = quantile(values, 0.5)
    return quantile([abs(x - center) for x in values], 0.5)


def tukey_fences(values, multiplier: float = 1.5):
    """(lower, upper) Tukey fences, or (None, None) with fewer than 2 values."""
    if len(values) < 2:
        return None, None
    q1 = quantile(values, 0.25)
    q3 = quantile(values, 0.75)
    iqr = q3 - q1
    return q1 - multiplier * iqr, q3 + multiplier * iqr


def describe(values, probabilities=DEFAULT_QUANTILE_PROBABILITIES) -> dict:
    """Full exact description of one cohort's observations.

    Degenerate cohorts are first-class: an empty cohort reports count 0 with
    every statistic None (never 0.0), and a constant cohort reports zero
    spread with an undefined skewness rather than a division by zero.
    """
    values = [float(v) for v in values if v is not None]
    n = len(values)
    doc = {
        "count": n,
        "mean": None,
        "stdev": None,
        "min": None,
        "max": None,
        "quantiles": {},
        "quantile_convention": EMPIRICAL_QUANTILE_CONVENTION,
        "iqr": None,
        "median_absolute_deviation": None,
        "skewness": None,
        "skewness_convention": SKEWNESS_CONVENTION,
        "mean_minus_median": None,
        "tukey_outliers": {"lower": 0, "upper": 0, "fence_multiplier": 1.5},
        "extreme_outliers": {"lower": 0, "upper": 0, "fence_multiplier": 3.0},
    }
    if n == 0:
        return doc

    ordered = sorted(values)
    doc["mean"] = statistics.fmean(ordered)
    doc["stdev"] = statistics.stdev(ordered) if n > 1 else None
    doc["min"] = ordered[0]
    doc["max"] = ordered[-1]
    doc["quantiles"] = quantiles(ordered, probabilities)
    q1 = quantile(ordered, 0.25)
    q3 = quantile(ordered, 0.75)
    median = quantile(ordered, 0.5)
    doc["iqr"] = q3 - q1
    doc["median_absolute_deviation"] = median_absolute_deviation(ordered)
    doc["skewness"] = skewness(ordered)
    doc["mean_minus_median"] = doc["mean"] - median

    for key, multiplier in (("tukey_outliers", 1.5), ("extreme_outliers", 3.0)):
        low, high = tukey_fences(ordered, multiplier)
        if low is None:
            continue
        doc[key] = {
            "lower": sum(1 for x in ordered if x < low),
            "upper": sum(1 for x in ordered if x > high),
            "fence_multiplier": multiplier,
            "lower_fence": low,
            "upper_fence": high,
        }
    return doc


# --------------------------------------------------------------------------
# Histogram / ECDF / tails
# --------------------------------------------------------------------------


def histogram(values, bins=None) -> dict:
    """Histogram with Freedman-Diaconis binning and documented fallbacks.

    Freedman-Diaconis (bin width 2 * IQR / n^(1/3)) is robust to the long
    right tail a compounding farm economy produces, where Sturges would
    under-bin badly. Two fallbacks, both reported in `binning`:

    * zero IQR but non-zero range (a cohort piled on a few values) -> Sturges;
    * zero range (every run identical, including an all-bankrupt cohort that
      ended at the same number) -> one degenerate bin, rather than a division
      by zero or a fabricated spread.
    """
    values = [float(v) for v in values if v is not None]
    n = len(values)
    doc = {"count": n, "binning": "none", "bin_edges": [], "counts": []}
    if n == 0:
        return doc
    ordered = sorted(values)
    low, high = ordered[0], ordered[-1]
    if low == high:
        doc.update(
            binning="degenerate_constant",
            bin_edges=[low, high],
            counts=[n],
            degenerate=True,
        )
        return doc

    if bins is None:
        iqr = quantile(ordered, 0.75) - quantile(ordered, 0.25)
        width = 2.0 * iqr / (n ** (1.0 / 3.0)) if iqr > 0 else 0.0
        if width > 0:
            bins = max(1, min(200, int(math.ceil((high - low) / width))))
            doc["binning"] = "freedman_diaconis"
        else:
            bins = max(1, min(200, int(math.ceil(math.log2(n) + 1))))
            doc["binning"] = "sturges_fallback_zero_iqr"
    else:
        doc["binning"] = "explicit"

    edges = [low + (high - low) * i / bins for i in range(bins + 1)]
    edges[-1] = high
    counts = [0] * bins
    for x in ordered:
        index = int((x - low) / (high - low) * bins)
        counts[min(index, bins - 1)] += 1
    doc["bin_edges"] = edges
    doc["counts"] = counts
    return doc


def ecdf(values, max_points: int = DEFAULT_CURVE_POINTS) -> dict:
    """Empirical CDF, thinned to at most `max_points` order statistics.

    F(x) = #{observations <= x} / n, evaluated at observed values only -- so
    the curve is exact where it is reported, and thinning drops points rather
    than interpolating between them.
    """
    values = sorted(float(v) for v in values if v is not None)
    n = len(values)
    doc = {"count": n, "values": [], "probabilities": [], "thinned": False}
    if n == 0:
        return doc
    indexes = range(n)
    if n > max_points:
        indexes = sorted({round(i * (n - 1) / (max_points - 1)) for i in range(max_points)})
        doc["thinned"] = True
    doc["values"] = [values[i] for i in indexes]
    doc["probabilities"] = [(i + 1) / n for i in indexes]
    return doc


def tail_probability(
    values,
    threshold: float,
    direction: str = "below",
    estimand: str = "tail_probability",
    confidence: float = DEFAULT_CONFIDENCE,
    inclusive: bool = False,
) -> Estimate:
    """P(X < t) / P(X > t) as a proportion, with a Wilson interval.

    A tail probability *is* a Bernoulli parameter, so it gets the same
    interval machinery (and the same 0/n and n/n handling) as the bankruptcy
    rate rather than a separate ad-hoc calculation.
    """
    if direction not in ("below", "above"):
        raise ValueError("direction must be 'below' or 'above'")
    accumulator = BernoulliAccumulator()
    for value in values:
        if value is None:
            continue
        if direction == "below":
            hit = value <= threshold if inclusive else value < threshold
        else:
            hit = value >= threshold if inclusive else value > threshold
        accumulator.add(hit)
    estimate = proportion_interval(
        accumulator,
        estimand=estimand,
        confidence=confidence,
        method="wilson",
        unit="probability",
        population="all_runs",
    )
    comparator = {"below": "<=" if inclusive else "<", "above": ">=" if inclusive else ">"}[
        direction
    ]
    estimate.extra["condition"] = f"X {comparator} {threshold}"
    return estimate


# --------------------------------------------------------------------------
# Time to bankruptcy (censoring-aware)
# --------------------------------------------------------------------------


def survival_curve(event_days, censor_days, max_points: int = DEFAULT_CURVE_POINTS) -> dict:
    """Kaplan-Meier survival curve for time to bankruptcy.

    The censoring-aware counterpart to the `conditional_bankruptcy_day`
    estimand. A run that finished the horizon solvent is *censored* at that
    horizon -- it did not survive forever, it was simply not observed longer
    -- and averaging bankruptcy days over only the failures (which is what
    that estimand does, by design and by name) answers a different question
    from "what fraction is still solvent on day d".
    """
    observations = [(float(d), True) for d in event_days if d is not None]
    observations += [(float(d), False) for d in censor_days if d is not None]
    observations.sort(key=lambda item: (item[0], item[1]))
    n_total = len(observations)
    doc = {
        "count": n_total,
        "events": sum(1 for _, is_event in observations if is_event),
        "censored": sum(1 for _, is_event in observations if not is_event),
        "times": [],
        "survival": [],
        "at_risk": [],
        "thinned": False,
    }
    if n_total == 0:
        return doc

    at_risk = n_total
    survival = 1.0
    times, survivals, risks = [], [], []
    index = 0
    while index < n_total:
        time = observations[index][0]
        events_here = 0
        removed_here = 0
        while index < n_total and observations[index][0] == time:
            removed_here += 1
            if observations[index][1]:
                events_here += 1
            index += 1
        if events_here and at_risk > 0:
            survival *= 1.0 - events_here / at_risk
            times.append(time)
            survivals.append(survival)
            risks.append(at_risk)
        at_risk -= removed_here

    if len(times) > max_points:
        keep = sorted({round(i * (len(times) - 1) / (max_points - 1)) for i in range(max_points)})
        times = [times[i] for i in keep]
        survivals = [survivals[i] for i in keep]
        risks = [risks[i] for i in keep]
        doc["thinned"] = True

    doc["times"] = times
    doc["survival"] = survivals
    doc["at_risk"] = risks
    return doc


# --------------------------------------------------------------------------
# Reading a published batch's raw observations
# --------------------------------------------------------------------------


class RunObservation:
    """One run's analysis-relevant fields, read back from a CSV.

    Deliberately duck-type-compatible with `metrics.run_results.RunResult` for
    every attribute the estimand registry extracts, so the *same* estimand
    extraction functions run over a live batch stream and over an archived
    CSV. That is what lets `farm-c`'s CSV go through this inference layer
    unchanged (plan Section "Python and C Boundary"): the C port stays a
    deterministic raw-data producer and grows no statistics of its own.
    """

    __slots__ = (
        "strategy",
        "seed",
        "replicate_id",
        "days_simulated",
        "final_money",
        "net_profit",
        "bankrupt",
        "bankruptcy_day",
        "avg_profit_per_day",
        "minimum_cash_balance",
        "first_upgrade_day",
        "second_upgrade_day",
        "crop_loss_rate",
    )

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))


def _to_float(raw):
    if raw is None or raw == "" or raw == "None":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _to_int(raw):
    value = _to_float(raw)
    return None if value is None else int(value)


def _to_bool(raw):
    """Parse a bankruptcy flag from either producer's spelling.

    Python's `csv.DictWriter` writes Python's `True`/`False`; farm-c writes
    `1`/`0`. Accepting both is what makes one loader serve both CSVs.
    """
    if raw is None or raw == "":
        return None
    text = str(raw).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def load_observations(csv_path: str) -> dict:
    """Load a batch CSV into {strategy: [RunObservation, ...]}.

    Missing columns stay `None` rather than being defaulted to zero: farm-c's
    CSV carries no `first_upgrade_day`, and an estimand over a column that was
    never recorded must come back undefined (n = 0), not as a cohort of
    confident zeros.
    """
    by_strategy: dict[str, list] = {}
    with open(csv_path, newline="") as handle:
        for row in csv.DictReader(handle):
            days = _to_int(row.get("days_simulated"))
            net_profit = _to_float(row.get("net_profit"))
            avg_profit_per_day = _to_float(row.get("avg_profit_per_day"))
            if avg_profit_per_day is None and net_profit is not None and days:
                # farm-c records the components but not the ratio; recompute it
                # the way build_run_result does (per-run ratio, not pooled).
                avg_profit_per_day = net_profit / days
            minimum_cash = _to_float(row.get("minimum_cash_balance"))
            if minimum_cash is None:
                minimum_cash = _to_float(row.get("lowest_money"))
            observation = RunObservation(
                strategy=row.get("strategy"),
                seed=_to_int(row.get("seed")),
                replicate_id=_to_int(row.get("replicate_id")),
                days_simulated=days,
                final_money=_to_float(row.get("final_money")),
                net_profit=net_profit,
                bankrupt=_to_bool(row.get("bankrupt")),
                bankruptcy_day=_to_int(row.get("bankruptcy_day")),
                avg_profit_per_day=avg_profit_per_day,
                minimum_cash_balance=minimum_cash,
                first_upgrade_day=_to_int(row.get("first_upgrade_day")),
                second_upgrade_day=_to_int(row.get("second_upgrade_day")),
                crop_loss_rate=_to_float(row.get("crop_loss_rate")),
            )
            by_strategy.setdefault(observation.strategy, []).append(observation)
    return by_strategy


def observed_values(observations, estimand_id: str) -> list:
    """Every defined observation of one estimand, in run order."""
    estimand = estimand_registry.get(estimand_id)
    values = []
    for observation in observations:
        value = estimand.observe(observation)
        if value is None:
            continue
        values.append(float(value) if not isinstance(value, bool) else float(bool(value)))
    return values


def analyze_strategy(
    observations,
    horizon_days: int | None = None,
    probabilities=DEFAULT_QUANTILE_PROBABILITIES,
    tail_thresholds=(0.0,),
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
    strategy: str = "",
) -> dict:
    """Exact distribution analysis for one strategy's runs.

    Every cohort in the output names itself (`all_runs`, `survivors`,
    `bankrupt`) so a chart or a table can never present a survivor-only
    distribution as if it covered the batch.
    """
    final_money = [o.final_money for o in observations if o.final_money is not None]
    survivors = [o.final_money for o in observations if o.bankrupt is False]
    bankrupt = [o.final_money for o in observations if o.bankrupt is True]
    bankruptcy_days = [o.bankruptcy_day for o in observations if o.bankruptcy_day is not None]
    censor_days = [
        (o.days_simulated if horizon_days is None else horizon_days)
        for o in observations
        if o.bankrupt is False
    ]

    quantile_estimates = {
        key: estimate.to_dict()
        for key, estimate in bootstrap_quantile_set(
            final_money,
            probabilities,
            confidence=confidence,
            replications=replications,
            analysis_seed=derive_analysis_seed(base_seed, "final_money_quantile", strategy),
            unit="currency",
            population="all_runs",
        ).items()
    }

    tails = {}
    for threshold in tail_thresholds:
        estimate = tail_probability(
            final_money,
            threshold,
            direction="below",
            estimand="final_money_tail_below",
            confidence=confidence,
        )
        tails[f"below_{threshold:g}"] = estimate.to_dict()

    return publish_mapping(
        {
            "cohorts": {
                "all_runs": describe(final_money, probabilities),
                "survivors": describe(survivors, probabilities),
                "bankrupt": describe(bankrupt, probabilities),
                "bankruptcy_day": describe(bankruptcy_days, probabilities),
            },
            "histograms": {
                "final_money_all_runs": histogram(final_money),
                "final_money_survivors": histogram(survivors),
                "bankruptcy_day": histogram(bankruptcy_days),
            },
            "ecdf": {
                "final_money_all_runs": ecdf(final_money),
                "bankruptcy_day": ecdf(bankruptcy_days),
            },
            "quantile_estimates": quantile_estimates,
            "tail_probabilities": tails,
            "time_to_bankruptcy": survival_curve(bankruptcy_days, censor_days),
        }
    )


def analyze_csv(
    csv_path: str,
    horizon_days: int | None = None,
    probabilities=DEFAULT_QUANTILE_PROBABILITIES,
    tail_thresholds=(0.0,),
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
) -> dict:
    """The published `distributions.json` document for one batch CSV."""
    by_strategy = load_observations(csv_path)
    return {
        "source_csv": csv_path,
        "quantile_convention": EMPIRICAL_QUANTILE_CONVENTION,
        "skewness_convention": SKEWNESS_CONVENTION,
        "confidence": confidence,
        "bootstrap_replications": replications,
        "base_seed": base_seed,
        "exact": True,
        "notes": (
            "Computed from exact per-run observations in the CSV, not from the "
            "bounded median reservoir in metrics/aggregate_results.py."
        ),
        "strategies": {
            strategy: analyze_strategy(
                observations,
                horizon_days=horizon_days,
                probabilities=probabilities,
                tail_thresholds=tail_thresholds,
                confidence=confidence,
                replications=replications,
                base_seed=base_seed,
                strategy=strategy,
            )
            for strategy, observations in by_strategy.items()
        },
    }
