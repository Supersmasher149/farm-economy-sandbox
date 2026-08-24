"""Strategy-vs-strategy comparisons, independent and paired.

Two comparison paths exist here and the output can never confuse them,
because the pairing method travels with every result:

* **Independent** -- the legacy batch schedule gives each strategy its own
  per-run seeds (`runner/batch_run.py` mints them agent-major from one
  stream), so strategy A's run 7 and strategy B's run 7 share nothing but a
  position. Differences are between two independent samples; the interval is
  Welch's, which does not assume equal variances (a bankrupt-heavy strategy
  and a stable one have wildly different spread).
* **Paired** -- `runner/sampling_plan.py`'s `shared-initial-seed-v1` gives
  every strategy the *same* run seed for replicate N. Differences are then
  computed per replicate before aggregation, which is what buys the variance
  reduction; the interval is over those per-replicate differences.

`shared-initial-seed-v1` is honest about being **weak** pairing: identical
initial seeds do not give identical weather once the strategies' decisions
consume different numbers of RNG draws. So this module reports the *measured*
correlation and the *measured* variance reduction rather than assuming either
-- if pairing bought nothing on a given estimand, the output says so, and
plan Section 7's staged path to strong common random numbers is what would
change that.

Win probability follows the Mann-Whitney interpretation,
P(Y_A > Y_B) + 0.5 * P(Y_A = Y_B), with strict wins and ties also reported
separately so a reader can see how much of a "win rate" is really ties.

Multiplicity: 11 strategies is 55 pairs per estimand, and at 95% nominal
coverage roughly three of those intervals would exclude the truth by chance
alone. `adjust_family` applies the family-wide correction and records which
one was used; nothing here reports an all-pairs table without a recorded
family size.
"""

import bisect
import math
import statistics
from dataclasses import asdict, dataclass, field

from metrics.inference import (
    DEFAULT_BOOTSTRAP_REPLICATIONS,
    DEFAULT_CONFIDENCE,
    MomentAccumulator,
    bootstrap_paired_interval,
    derive_analysis_seed,
    publish_mapping,
    student_t_cdf,
    student_t_quantile,
    wilson_interval,
)

COMPARISON_VERSION = "farm-comparisons-v1"

PAIRING_INDEPENDENT = "independent"
PAIRING_PAIRED = "paired_by_replicate"

CORRECTION_METHODS = ("none", "bonferroni", "holm", "benjamini_hochberg")


@dataclass
class Comparison:
    """One A-vs-B result, carrying everything needed to read it correctly."""

    estimand: str
    strategy_a: str
    strategy_b: str
    pairing: str
    method: str
    confidence: float = DEFAULT_CONFIDENCE
    difference: float | None = None
    relative_difference: float | None = None
    lower: float | None = None
    upper: float | None = None
    standard_error: float | None = None
    p_value: float | None = None
    adjusted_p_value: float | None = None
    n_a: int = 0
    n_b: int = 0
    n_pairs: int | None = None
    win_probability: float | None = None
    win_rate: float | None = None
    loss_rate: float | None = None
    tie_rate: float | None = None
    correlation: float | None = None
    variance_reduction: float | None = None
    family: str | None = None
    family_size: int | None = None
    correction: str = "none"
    notes: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        doc = publish_mapping(asdict(self))
        if not doc.get("extra"):
            doc.pop("extra", None)
        if doc.get("notes") is None:
            doc.pop("notes")
        return doc


# --------------------------------------------------------------------------
# Win probability
# --------------------------------------------------------------------------


def win_probability(values_a, values_b) -> dict:
    """Mann-Whitney win probability with ties reported separately.

    O((n + m) log m) by binary-searching each distinct value of A into a
    sorted B, rather than the O(n * m) double loop: at 20,000 runs per strategy the naive form is 4e8
    comparisons per pair and 55 pairs per estimand, which is the difference
    between a report that renders and one that does not.
    """
    a = sorted(values_a)
    b = sorted(values_b)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return {
            "win_probability": None,
            "strict_wins": None,
            "ties": None,
            "losses": None,
        }
    strict = 0
    ties = 0
    index = 0
    while index < n:
        value = a[index]
        group = 0
        while index < n and a[index] == value:
            group += 1
            index += 1
        less = bisect.bisect_left(b, value)
        equal = bisect.bisect_right(b, value) - less
        strict += group * less
        ties += group * equal
    total = n * m
    losses = total - strict - ties
    return {
        "win_probability": (strict + 0.5 * ties) / total,
        "strict_wins": strict / total,
        "ties": ties / total,
        "losses": losses / total,
    }


# --------------------------------------------------------------------------
# Independent comparisons
# --------------------------------------------------------------------------


def _welch(mean_a, var_a, n_a, mean_b, var_b, n_b):
    """Welch's standard error and Satterthwaite degrees of freedom."""
    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return 0.0, None
    numerator = (var_a / n_a + var_b / n_b) ** 2
    denominator = (var_a / n_a) ** 2 / (n_a - 1) + (var_b / n_b) ** 2 / (n_b - 1)
    df = numerator / denominator if denominator > 0 else None
    return se, df


def compare_means_independent(
    values_a,
    values_b,
    estimand: str,
    strategy_a: str,
    strategy_b: str,
    confidence: float = DEFAULT_CONFIDENCE,
    include_win_probability: bool = True,
) -> Comparison:
    """Difference in means between two independent samples (Welch)."""
    a = [float(v) for v in values_a if v is not None]
    b = [float(v) for v in values_b if v is not None]
    comparison = Comparison(
        estimand=estimand,
        strategy_a=strategy_a,
        strategy_b=strategy_b,
        pairing=PAIRING_INDEPENDENT,
        method="welch_t",
        confidence=confidence,
        n_a=len(a),
        n_b=len(b),
    )
    if len(a) < 2 or len(b) < 2:
        comparison.method = "welch_t_undefined"
        comparison.notes = "each arm needs at least two observations"
        return comparison

    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    var_a, var_b = statistics.variance(a), statistics.variance(b)
    comparison.difference = mean_a - mean_b
    # Relative difference only against a denominator that can carry one: a
    # reference mean at or near zero makes "+400%" arithmetic noise.
    if abs(mean_b) > 1e-9:
        comparison.relative_difference = comparison.difference / abs(mean_b)
    se, df = _welch(mean_a, var_a, len(a), mean_b, var_b, len(b))
    comparison.standard_error = se
    if df:
        critical = student_t_quantile(0.5 + confidence / 2.0, df)
        comparison.lower = comparison.difference - critical * se
        comparison.upper = comparison.difference + critical * se
        t_statistic = comparison.difference / se if se else 0.0
        comparison.p_value = 2.0 * (1.0 - student_t_cdf(abs(t_statistic), df))
        comparison.extra["degrees_of_freedom"] = df
    else:
        comparison.notes = "zero pooled variance; interval undefined"
    if include_win_probability:
        wins = win_probability(a, b)
        comparison.win_probability = wins["win_probability"]
        comparison.win_rate = wins["strict_wins"]
        comparison.tie_rate = wins["ties"]
        comparison.loss_rate = wins["losses"]
    return comparison


def compare_proportions_independent(
    successes_a: int,
    trials_a: int,
    successes_b: int,
    trials_b: int,
    estimand: str,
    strategy_a: str,
    strategy_b: str,
    confidence: float = DEFAULT_CONFIDENCE,
) -> Comparison:
    """Difference of two independent proportions, Newcombe hybrid-score.

    Built from each arm's Wilson interval rather than from a Wald difference,
    for the same reason the single-arm interval is Wilson: a bankruptcy rate
    of 0/1000 is common here, and a Wald difference interval around it is
    both too narrow and capable of leaving [-1, 1].
    """
    comparison = Comparison(
        estimand=estimand,
        strategy_a=strategy_a,
        strategy_b=strategy_b,
        pairing=PAIRING_INDEPENDENT,
        method="newcombe_hybrid_score",
        confidence=confidence,
        n_a=trials_a,
        n_b=trials_b,
    )
    if trials_a <= 0 or trials_b <= 0:
        comparison.method = "newcombe_hybrid_score_undefined"
        comparison.notes = "both arms need at least one trial"
        return comparison
    p_a = successes_a / trials_a
    p_b = successes_b / trials_b
    comparison.difference = p_a - p_b
    if abs(p_b) > 1e-9:
        comparison.relative_difference = comparison.difference / abs(p_b)
    lower_a, upper_a = wilson_interval(successes_a, trials_a, confidence)
    lower_b, upper_b = wilson_interval(successes_b, trials_b, confidence)
    comparison.lower = comparison.difference - math.sqrt(
        (p_a - lower_a) ** 2 + (upper_b - p_b) ** 2
    )
    comparison.upper = comparison.difference + math.sqrt(
        (upper_a - p_a) ** 2 + (p_b - lower_b) ** 2
    )
    comparison.lower = max(-1.0, comparison.lower)
    comparison.upper = min(1.0, comparison.upper)
    # Pooled two-proportion score test, the standard companion to Newcombe.
    pooled = (successes_a + successes_b) / (trials_a + trials_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / trials_a + 1 / trials_b))
    comparison.standard_error = se
    if se > 0:
        z = comparison.difference / se
        comparison.p_value = 2.0 * (1.0 - _standard_normal_cdf(abs(z)))
    comparison.extra["proportion_a"] = p_a
    comparison.extra["proportion_b"] = p_b
    return comparison


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# --------------------------------------------------------------------------
# Paired comparisons
# --------------------------------------------------------------------------


def pair_by_replicate(observations_a, observations_b) -> tuple[list, list, dict]:
    """Align two strategies' runs by `replicate_id`.

    Returns `(paired_a, paired_b, diagnostics)`. Runs whose replicate id is
    missing on either side are excluded and *counted* -- a silently dropped
    pair is how a paired comparison quietly becomes an unbalanced one. Pair
    ordering is by replicate id, so the result does not depend on the order
    runs arrive in.
    """
    by_id_a = {}
    by_id_b = {}
    duplicates = 0
    for observation in observations_a:
        replicate = getattr(observation, "replicate_id", None)
        if replicate is None:
            continue
        if replicate in by_id_a:
            duplicates += 1
            continue
        by_id_a[replicate] = observation
    for observation in observations_b:
        replicate = getattr(observation, "replicate_id", None)
        if replicate is None:
            continue
        if replicate in by_id_b:
            duplicates += 1
            continue
        by_id_b[replicate] = observation
    shared = sorted(set(by_id_a) & set(by_id_b))
    diagnostics = {
        "pairs": len(shared),
        "unmatched_a": len(by_id_a) - len(shared),
        "unmatched_b": len(by_id_b) - len(shared),
        "missing_replicate_id_a": sum(
            1 for o in observations_a if getattr(o, "replicate_id", None) is None
        ),
        "missing_replicate_id_b": sum(
            1 for o in observations_b if getattr(o, "replicate_id", None) is None
        ),
        "duplicate_replicate_ids": duplicates,
    }
    return (
        [by_id_a[r] for r in shared],
        [by_id_b[r] for r in shared],
        diagnostics,
    )


def compare_means_paired(
    values_a,
    values_b,
    estimand: str,
    strategy_a: str,
    strategy_b: str,
    confidence: float = DEFAULT_CONFIDENCE,
    bootstrap: bool = True,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
    diagnostics: dict | None = None,
) -> Comparison:
    """Mean paired difference over aligned replicates.

    Reports the *measured* correlation between arms and the variance
    reduction that pairing actually achieved, defined as
    1 - Var(A - B) / (Var(A) + Var(B)) -- the denominator being what the
    difference's variance would have been had the arms been independent.
    A value at or below zero means pairing bought nothing on this estimand,
    which is a real and reportable outcome for weak pairing.
    """
    a = [float(v) for v in values_a]
    b = [float(v) for v in values_b]
    if len(a) != len(b):
        raise ValueError("paired comparison needs equal-length aligned arms")
    comparison = Comparison(
        estimand=estimand,
        strategy_a=strategy_a,
        strategy_b=strategy_b,
        pairing=PAIRING_PAIRED,
        method="paired_t",
        confidence=confidence,
        n_a=len(a),
        n_b=len(b),
        n_pairs=len(a),
    )
    if diagnostics:
        comparison.extra["pairing_diagnostics"] = diagnostics
    if len(a) < 2:
        comparison.method = "paired_t_undefined"
        comparison.notes = "fewer than two complete pairs"
        return comparison

    differences = [x - y for x, y in zip(a, b, strict=True)]
    accumulator = MomentAccumulator()
    for difference in differences:
        accumulator.add(difference)
    comparison.difference = accumulator.mean()
    mean_b = statistics.fmean(b)
    if abs(mean_b) > 1e-9:
        comparison.relative_difference = comparison.difference / abs(mean_b)
    se = accumulator.standard_error()
    comparison.standard_error = se
    critical = student_t_quantile(0.5 + confidence / 2.0, len(a) - 1)
    comparison.lower = comparison.difference - critical * se
    comparison.upper = comparison.difference + critical * se
    if se > 0:
        t_statistic = comparison.difference / se
        comparison.p_value = 2.0 * (1.0 - student_t_cdf(abs(t_statistic), len(a) - 1))

    var_a, var_b = statistics.variance(a), statistics.variance(b)
    var_diff = statistics.variance(differences)
    independent_variance = var_a + var_b
    if independent_variance > 0:
        comparison.variance_reduction = 1.0 - var_diff / independent_variance
    if var_a > 0 and var_b > 0:
        comparison.correlation = statistics.correlation(a, b)

    wins = sum(1 for d in differences if d > 0)
    losses = sum(1 for d in differences if d < 0)
    ties = len(differences) - wins - losses
    comparison.win_rate = wins / len(differences)
    comparison.loss_rate = losses / len(differences)
    comparison.tie_rate = ties / len(differences)
    comparison.win_probability = comparison.win_rate + 0.5 * comparison.tie_rate

    if bootstrap:
        interval = bootstrap_paired_interval(
            differences,
            estimand=f"{estimand}_paired_difference",
            confidence=confidence,
            replications=replications,
            analysis_seed=derive_analysis_seed(
                base_seed, estimand, "paired", strategy_a, strategy_b
            ),
        )
        comparison.extra["bootstrap"] = {
            "lower": interval.lower,
            "upper": interval.upper,
            "replications": replications,
            "analysis_seed": interval.extra.get("analysis_seed"),
            "method": interval.method,
        }
    return comparison


# --------------------------------------------------------------------------
# Multiplicity
# --------------------------------------------------------------------------


def bonferroni_confidence(confidence: float, family_size: int) -> float:
    """Per-comparison level giving `confidence` simultaneous coverage."""
    if family_size < 1:
        raise ValueError("family_size must be at least 1")
    return 1.0 - (1.0 - confidence) / family_size


def holm_adjusted(p_values: list) -> list:
    """Holm step-down adjusted p-values, in the input's order.

    Uniformly more powerful than Bonferroni at the same family-wise error
    rate, which is why it is the default when p-values are reported at all.
    """
    indexed = sorted((p, i) for i, p in enumerate(p_values) if p is not None)
    m = len(indexed)
    adjusted = [None] * len(p_values)
    running = 0.0
    for rank, (p, index) in enumerate(indexed):
        value = min(1.0, (m - rank) * p)
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def benjamini_hochberg_adjusted(p_values: list) -> list:
    """BH adjusted p-values (q-values), in the input's order.

    Controls the false discovery rate, not the family-wise error rate --
    strictly an exploratory option here, and labelled as such wherever it is
    used, because a table of "significant" pairs under FDR is a different
    claim from one under FWER.
    """
    indexed = sorted(((p, i) for i, p in enumerate(p_values) if p is not None), reverse=True)
    m = len(indexed)
    adjusted = [None] * len(p_values)
    running = 1.0
    for position, (p, index) in enumerate(indexed):
        rank = m - position
        running = min(running, m * p / rank)
        adjusted[index] = running
    return adjusted


def adjust_family(comparisons: list, method: str = "holm", family: str = "") -> list:
    """Apply a multiplicity correction across one family of comparisons.

    Bonferroni widens the *intervals* (which is what a simultaneous
    all-pairs table needs); Holm and BH adjust *p-values* only, leaving the
    per-comparison intervals as they are. Both facts are recorded on every
    comparison, so no reader has to guess which correction an interval
    already reflects.
    """
    if method not in CORRECTION_METHODS:
        raise ValueError(f"unknown correction method {method!r}; expected {CORRECTION_METHODS}")
    family_size = len(comparisons)
    for comparison in comparisons:
        comparison.family = family or None
        comparison.family_size = family_size
        comparison.correction = method
    if method == "none" or family_size == 0:
        return comparisons
    if method == "bonferroni":
        # Bonferroni is applied to the intervals themselves, so an all-pairs
        # table has simultaneous coverage rather than 55 separate 95% claims.
        for comparison in comparisons:
            comparison.extra["per_comparison_confidence"] = bonferroni_confidence(
                comparison.confidence, family_size
            )
        p_values = [c.p_value for c in comparisons]
        for comparison, p in zip(comparisons, p_values, strict=True):
            comparison.adjusted_p_value = None if p is None else min(1.0, p * family_size)
        return comparisons
    adjuster = holm_adjusted if method == "holm" else benjamini_hochberg_adjusted
    for comparison, adjusted in zip(
        comparisons, adjuster([c.p_value for c in comparisons]), strict=True
    ):
        comparison.adjusted_p_value = adjusted
    return comparisons


# --------------------------------------------------------------------------
# Families of comparisons
# --------------------------------------------------------------------------


def _estimand_values(observations, estimand_id: str):
    from metrics import estimands as estimand_registry

    estimand = estimand_registry.get(estimand_id)
    values = []
    for observation in observations:
        value = estimand.observe(observation)
        if value is None:
            continue
        values.append(float(value))
    return values, estimand


def compare_pair(
    observations_a,
    observations_b,
    estimand_id: str,
    strategy_a: str,
    strategy_b: str,
    pairing: str = PAIRING_INDEPENDENT,
    confidence: float = DEFAULT_CONFIDENCE,
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
) -> Comparison:
    """One A-vs-B comparison on one estimand, under the requested pairing."""
    from metrics import estimands as estimand_registry

    estimand = estimand_registry.get(estimand_id)
    if not estimand.supports_comparison:
        raise ValueError(f"estimand {estimand_id!r} does not support comparisons")

    if pairing == PAIRING_PAIRED:
        aligned_a, aligned_b, diagnostics = pair_by_replicate(observations_a, observations_b)
        values_a = [estimand.observe(o) for o in aligned_a]
        values_b = [estimand.observe(o) for o in aligned_b]
        keep = [
            i
            for i, (x, y) in enumerate(zip(values_a, values_b, strict=True))
            if x is not None and y is not None
        ]
        diagnostics["pairs_with_both_observed"] = len(keep)
        if estimand.kind == "proportion":
            # A paired proportion difference is just the mean of per-pair
            # (0/1 - 0/1) differences, which the paired-t path handles
            # directly; no separate McNemar machinery is needed for an
            # interval on the difference.
            values_a = [float(bool(values_a[i])) for i in keep]
            values_b = [float(bool(values_b[i])) for i in keep]
        else:
            values_a = [float(values_a[i]) for i in keep]
            values_b = [float(values_b[i]) for i in keep]
        return compare_means_paired(
            values_a,
            values_b,
            estimand_id,
            strategy_a,
            strategy_b,
            confidence=confidence,
            replications=replications,
            base_seed=base_seed,
            diagnostics=diagnostics,
        )

    values_a, _ = _estimand_values(observations_a, estimand_id)
    values_b, _ = _estimand_values(observations_b, estimand_id)
    if estimand.kind == "proportion":
        return compare_proportions_independent(
            int(sum(values_a)),
            len(values_a),
            int(sum(values_b)),
            len(values_b),
            estimand_id,
            strategy_a,
            strategy_b,
            confidence=confidence,
        )
    return compare_means_independent(
        values_a, values_b, estimand_id, strategy_a, strategy_b, confidence=confidence
    )


def compare_all_pairs(
    observations_by_strategy: dict,
    estimand_ids=None,
    pairing: str = PAIRING_INDEPENDENT,
    confidence: float = DEFAULT_CONFIDENCE,
    correction: str = "holm",
    replications: int = DEFAULT_BOOTSTRAP_REPLICATIONS,
    base_seed=None,
    baseline: str | None = None,
) -> dict:
    """Every pair (or every pair against one baseline) for each estimand.

    The multiplicity family is *one estimand's* pair set, not the whole
    document: correcting across estimands as well would be correcting for
    comparisons nobody made jointly. Family and family size are recorded on
    every comparison either way.

    Pair ordering is fixed by sorted strategy name, so A-vs-B is computed
    once and the table does not change if the input dict's order changes.
    """
    from metrics import estimands as estimand_registry

    if estimand_ids is None:
        estimand_ids = estimand_registry.DEFAULT_COMPARISON_ESTIMANDS
    names = sorted(observations_by_strategy)
    if baseline is not None and baseline not in observations_by_strategy:
        raise ValueError(f"unknown baseline strategy {baseline!r}")

    document = {
        "version": COMPARISON_VERSION,
        "pairing": pairing,
        "confidence": confidence,
        "correction": correction,
        "baseline": baseline,
        "bootstrap_replications": replications if pairing == PAIRING_PAIRED else None,
        "estimands": {},
    }
    for estimand_id in estimand_ids:
        pairs = []
        for i, strategy_a in enumerate(names):
            for strategy_b in names[i + 1 :]:
                if baseline is not None and baseline not in (strategy_a, strategy_b):
                    continue
                pairs.append(
                    compare_pair(
                        observations_by_strategy[strategy_a],
                        observations_by_strategy[strategy_b],
                        estimand_id,
                        strategy_a,
                        strategy_b,
                        pairing=pairing,
                        confidence=confidence,
                        replications=replications,
                        base_seed=base_seed,
                    )
                )
        adjust_family(pairs, method=correction, family=f"{estimand_id}:{pairing}")
        document["estimands"][estimand_id] = [c.to_dict() for c in pairs]
    return document
