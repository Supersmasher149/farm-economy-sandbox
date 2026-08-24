"""Adaptive sampling: run until a precision target is met, then stop honestly.

The naive version of this feature is statistically wrong, so it is worth
naming what is *not* done here: repeatedly computing an ordinary fixed-sample
95% interval and stopping the first time it looks narrow enough does not give
95% coverage. Each extra look is another chance for the interval to be
transiently narrow, and the realized coverage of "peek every 250 runs until
happy" is materially below its nominal level.

What this module does instead (plan Section 3, option 1):

* **Checkpoints are predeclared.** The full schedule of look points is fixed
  from `min_runs`, `max_runs` and `checkpoint_runs` *before the first run*,
  and stopping can only happen at one of them. Nothing is evaluated between
  checkpoints, and the schedule is published in `convergence.json`.
* **Alpha is spent, not reused.** Each look gets its own slice of the total
  error budget from a Lan-DeMets spending function (O'Brien-Fleming by
  default -- very little alpha spent early, most of it at the end, which is
  what makes an early stop trustworthy). The interval evaluated at look k
  uses that look's slice, so an early look is judged against a much wider
  interval than a late one.
* **The union bound is the guarantee.** Consuming each look's alpha slice
  independently ignores the positive correlation between successive looks,
  which makes the procedure *conservative*: family-wise coverage is at least
  the nominal level, never below it. The exact group-sequential boundary
  would be slightly tighter (fewer runs for the same guarantee); trading a
  few runs for a bound that is easy to state and impossible to get wrong is
  the right side of that trade here, where runs are cheap.

Rare-event rules never run forever: if `min_bankruptcies` is not reached by
`max_runs`, the batch publishes with stop reason `rare_event_minimum_unmet`
rather than sampling indefinitely -- an unmet precision target is a finding to
report, not a reason to hang.

Determinism: blocks are minted by an *addressed* sampling plan
(`runner/sampling_plan.py`), so replicate N's seed for strategy S does not
depend on how many blocks preceded it, how many workers ran it, or in what
order results came back. A given `--seed` therefore produces the same runs,
the same checkpoints and the same stop decision at any worker count.
"""

import math
import statistics
from dataclasses import dataclass, field

from metrics.estimands import get as get_estimand
from metrics.inference import (
    DEFAULT_CONFIDENCE,
    normal_quantile,
    publish_mapping,
)

ADAPTIVE_VERSION = "farm-adaptive-v1"

STOP_PRECISION_REACHED = "precision_reached"
STOP_MAX_RUNS_REACHED = "max_runs_reached"
STOP_RARE_EVENT_UNMET = "rare_event_minimum_unmet"

ALPHA_SPENDING_METHODS = ("obrien_fleming", "pocock", "none")

# How many checkpoints back the "did the estimate stop moving" window looks.
DEFAULT_STABILITY_WINDOW = 4


@dataclass
class StoppingRule:
    """One precision target on one estimand."""

    estimand: str
    target_half_width: float | None = None
    target_relative_half_width: float | None = None

    def __post_init__(self):
        if self.target_half_width is None and self.target_relative_half_width is None:
            raise ValueError(
                f"stopping rule for {self.estimand!r} needs an absolute or relative target"
            )
        estimand = get_estimand(self.estimand)
        if not estimand.supports_adaptive:
            raise ValueError(
                f"estimand {self.estimand!r} does not support adaptive stopping "
                "(quantiles and other bootstrap-only estimands are excluded: their "
                "interval is not available from a streaming accumulator)"
            )

    def evaluate(self, estimate) -> dict:
        """Whether this rule is satisfied by an estimate, and by how much."""
        state = {
            "estimand": self.estimand,
            "target_half_width": self.target_half_width,
            "target_relative_half_width": self.target_relative_half_width,
            "half_width": estimate.half_width,
            "relative_half_width": estimate.relative_half_width,
            "met": False,
            "reason": None,
        }
        half_width = estimate.half_width
        if half_width is None or math.isinf(half_width):
            state["reason"] = "interval undefined"
            return state
        absolute_met = self.target_half_width is None or half_width <= self.target_half_width
        relative = estimate.relative_half_width
        if self.target_relative_half_width is None:
            relative_met = True
        elif relative is None:
            # Estimate sits at ~0, where relative precision is meaningless
            # (plan Section 4). Treat the relative leg as unmet rather than
            # vacuously satisfied, and say why.
            relative_met = False
            state["reason"] = "estimate too close to zero for a relative target"
        else:
            relative_met = relative <= self.target_relative_half_width
        state["met"] = bool(absolute_met and relative_met)
        return state


@dataclass
class AdaptiveConfig:
    """The declared sampling design. Fixed before the first run."""

    min_runs: int
    max_runs: int
    checkpoint_runs: int
    rules: list = field(default_factory=list)
    confidence: float = DEFAULT_CONFIDENCE
    mode: str = "all"  # "all": every rule must be met; "any": one suffices
    min_bankruptcies: int = 0
    min_survivals: int = 0
    alpha_spending: str = "obrien_fleming"
    stability_window: int = DEFAULT_STABILITY_WINDOW
    track_quantiles: bool = False

    def __post_init__(self):
        if self.min_runs <= 0 or self.max_runs <= 0 or self.checkpoint_runs <= 0:
            raise ValueError("min_runs, max_runs and checkpoint_runs must be positive")
        if self.min_runs > self.max_runs:
            raise ValueError("min_runs must not exceed max_runs")
        if self.mode not in ("all", "any"):
            raise ValueError("mode must be 'all' or 'any'")
        if self.alpha_spending not in ALPHA_SPENDING_METHODS:
            raise ValueError(f"alpha_spending must be one of {ALPHA_SPENDING_METHODS}")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must be strictly inside (0, 1)")

    def checkpoint_schedule(self) -> list:
        """Every run count at which stopping may be evaluated.

        Declared up front and published, so "we stopped at 1,250" can always
        be checked against "1,250 was a declared look point".
        """
        points = []
        n = self.min_runs
        while n < self.max_runs:
            points.append(n)
            n += self.checkpoint_runs
        points.append(self.max_runs)
        return points

    def describe(self) -> dict:
        return {
            "version": ADAPTIVE_VERSION,
            "min_runs": self.min_runs,
            "max_runs": self.max_runs,
            "checkpoint_runs": self.checkpoint_runs,
            "confidence": self.confidence,
            "mode": self.mode,
            "alpha_spending": self.alpha_spending,
            "min_bankruptcies": self.min_bankruptcies,
            "min_survivals": self.min_survivals,
            "stability_window": self.stability_window,
            "track_quantiles": self.track_quantiles,
            "checkpoint_schedule": self.checkpoint_schedule(),
            "rules": [
                {
                    "estimand": rule.estimand,
                    "target_half_width": rule.target_half_width,
                    "target_relative_half_width": rule.target_relative_half_width,
                }
                for rule in self.rules
            ],
        }


def alpha_spent(method: str, information_fraction: float, total_alpha: float) -> float:
    """Cumulative alpha a spending function has spent by information fraction t.

    O'Brien-Fleming: alpha*(t) = 2 - 2 * Phi(z_{1-a/2} / sqrt(t)) -- spends
    almost nothing early, so an early stop has to clear a very high bar.
    Pocock: alpha*(t) = a * ln(1 + (e - 1) t) -- spends evenly, stops earlier
    on average but with wider final intervals.
    """
    t = min(1.0, max(0.0, information_fraction))
    if t <= 0.0:
        return 0.0
    if method == "none":
        return total_alpha
    if method == "pocock":
        return total_alpha * math.log1p((math.e - 1.0) * t)
    z = normal_quantile(1.0 - total_alpha / 2.0)
    return 2.0 - 2.0 * _standard_normal_cdf(z / math.sqrt(t))


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def look_confidence(config: AdaptiveConfig, look_index: int) -> float:
    """Confidence level to evaluate the interval at one declared look.

    The look's own slice of the alpha budget: cumulative spend at this look
    minus cumulative spend at the previous one. Under the union bound over the
    declared looks, total spend is the nominal alpha, so overall coverage is
    at least `config.confidence`.
    """
    schedule = config.checkpoint_schedule()
    total_alpha = 1.0 - config.confidence
    if config.alpha_spending == "none":
        return config.confidence
    maximum = schedule[-1]
    current = alpha_spent(config.alpha_spending, schedule[look_index] / maximum, total_alpha)
    previous = (
        alpha_spent(config.alpha_spending, schedule[look_index - 1] / maximum, total_alpha)
        if look_index > 0
        else 0.0
    )
    increment = max(current - previous, 1e-12)
    return max(0.5, min(1.0 - 1e-12, 1.0 - increment))


class ConvergenceRecorder:
    """Builds the per-checkpoint history published as `convergence.json`.

    Reads accumulators via their non-destructive `snapshot()` / interval
    methods, so recording a checkpoint cannot perturb the batch it measures --
    and, since inference here never touches a `random.Random` the simulator
    owns, a checkpoint consumes no simulation RNG draws.
    """

    def __init__(self, config: AdaptiveConfig, estimand_ids, quantile_probability: float = 0.5):
        self.config = config
        self.estimand_ids = list(estimand_ids)
        self.quantile_probability = quantile_probability
        self.checkpoints = []

    def record(
        self,
        runs_per_strategy: int,
        look_index: int,
        accumulators: dict,
        counts: dict,
        rule_states: dict,
        decision: str,
        confidence: float,
        exact_values: dict | None = None,
    ) -> dict:
        previous = self.checkpoints[-1] if self.checkpoints else None
        window = self.config.stability_window
        windowed = self.checkpoints[-window] if len(self.checkpoints) >= window else None
        entry = {
            "look": look_index,
            "runs_per_strategy": runs_per_strategy,
            "total_runs": sum(counts.values()),
            "look_confidence": confidence,
            "decision": decision,
            "strategies": {},
        }
        for strategy, accumulator in sorted(accumulators.items()):
            estimates = {}
            for estimand_id in self.estimand_ids:
                if estimand_id not in accumulator.accumulators:
                    continue
                estimate = accumulator.estimate(estimand_id, confidence=confidence)
                record = {
                    "n": estimate.n,
                    "estimate": estimate.value,
                    "stdev": estimate.stdev,
                    "standard_error": estimate.standard_error,
                    "lower": estimate.lower,
                    "upper": estimate.upper,
                    "half_width": estimate.half_width,
                    "relative_half_width": estimate.relative_half_width,
                }
                record["change_from_previous"] = _delta(
                    record["estimate"], _previous_value(previous, strategy, estimand_id)
                )
                record["change_over_window"] = _delta(
                    record["estimate"], _previous_value(windowed, strategy, estimand_id)
                )
                record["stdev_change_from_previous"] = _delta(
                    record["stdev"],
                    _previous_value(previous, strategy, estimand_id, key="stdev"),
                )
                record["half_width_ratio_to_previous"] = _ratio(
                    record["half_width"],
                    _previous_value(previous, strategy, estimand_id, key="half_width"),
                )
                estimates[estimand_id] = record
            strategy_entry = {
                "runs": counts.get(strategy, 0),
                "estimates": estimates,
                "rare_events": rule_states.get(strategy, {}).get("rare_events", {}),
                "stop_rules": rule_states.get(strategy, {}).get("rules", []),
                "eligible_to_stop": rule_states.get(strategy, {}).get("eligible", False),
            }
            if exact_values and strategy in exact_values:
                values = exact_values[strategy]
                from metrics.distributions import quantile

                current_quantile = quantile(values, self.quantile_probability)
                strategy_entry["quantile"] = {
                    "p": self.quantile_probability,
                    "value": current_quantile,
                    "drift_from_previous": _delta(
                        current_quantile,
                        (previous or {})
                        .get("strategies", {})
                        .get(strategy, {})
                        .get("quantile", {})
                        .get("value"),
                    ),
                    "exact": True,
                }
            entry["strategies"][strategy] = strategy_entry
        self.checkpoints.append(entry)
        return entry

    def document(self, stop_reason: str, unmet, realized_runs: int, sampling_plan: dict) -> dict:
        return publish_mapping(
            {
                "version": ADAPTIVE_VERSION,
                "design": self.config.describe(),
                "sampling_plan": sampling_plan,
                "stop_reason": stop_reason,
                "unmet_criteria": list(unmet),
                "realized_runs_per_strategy": realized_runs,
                "estimands": self.estimand_ids,
                "checkpoints": self.checkpoints,
                "notes": (
                    "Checkpoints are the only points at which stopping was evaluated. "
                    "Each look's interval uses its own alpha slice from the declared "
                    "spending function; coverage over the whole sequence is at least "
                    "the configured confidence."
                ),
            }
        )


def _previous_value(entry, strategy: str, estimand_id: str, key: str = "estimate"):
    if not entry:
        return None
    return (
        entry.get("strategies", {})
        .get(strategy, {})
        .get("estimates", {})
        .get(estimand_id, {})
        .get(key)
    )


def _delta(current, previous):
    if current is None or previous is None:
        return None
    return current - previous


def _ratio(current, previous):
    if current is None or previous in (None, 0):
        return None
    return current / previous


class AdaptiveBatch:
    """Drive a batch in predeclared blocks, stopping at a declared checkpoint.

    Streams `RunResult`s exactly like `runner.batch_run.run_batch` does, so
    the caller's single-pass CSV write and aggregation are unchanged; the
    control logic lives between blocks, where no result is in flight.
    """

    def __init__(
        self,
        config: AdaptiveConfig,
        aggregator,
        run_block,
        agents,
        sampling_plan,
        estimand_ids=None,
    ):
        self.config = config
        self.aggregator = aggregator
        self.run_block = run_block
        self.agents = agents
        self.sampling_plan = sampling_plan
        rule_estimands = [rule.estimand for rule in config.rules]
        self.estimand_ids = list(estimand_ids) if estimand_ids else rule_estimands
        self.recorder = ConvergenceRecorder(config, self.estimand_ids)
        self.stop_reason = None
        self.unmet_criteria = []
        self.realized_runs = 0
        self._exact_values = {} if config.track_quantiles else None

    def _rule_states(self, accumulators, confidence: float) -> dict:
        states = {}
        for strategy, accumulator in accumulators.items():
            bankruptcy = accumulator.accumulators.get("bankruptcy_probability")
            bankruptcies = bankruptcy.successes if bankruptcy else None
            survivals = bankruptcy.failures if bankruptcy else None
            rules = []
            for rule in self.config.rules:
                estimate = accumulator.estimate(rule.estimand, confidence=confidence)
                rules.append(rule.evaluate(estimate))
            precision_met = (
                all(state["met"] for state in rules)
                if self.config.mode == "all"
                else any(state["met"] for state in rules)
            )
            rare_ok = True
            rare_unmet = []
            if self.config.min_bankruptcies and (
                bankruptcies is None or bankruptcies < self.config.min_bankruptcies
            ):
                rare_ok = False
                rare_unmet.append("min_bankruptcies")
            if self.config.min_survivals and (
                survivals is None or survivals < self.config.min_survivals
            ):
                rare_ok = False
                rare_unmet.append("min_survivals")
            states[strategy] = {
                "rules": rules,
                "eligible": bool(precision_met and rare_ok and rules),
                "precision_met": bool(precision_met and rules),
                "rare_events": {
                    "bankruptcies": bankruptcies,
                    "survivals": survivals,
                    "min_bankruptcies": self.config.min_bankruptcies,
                    "min_survivals": self.config.min_survivals,
                    "unmet": rare_unmet,
                },
            }
        return states

    def stream(self):
        """Yield every RunResult the adaptive design ends up running."""
        schedule = self.config.checkpoint_schedule()
        completed = 0
        for look_index, target in enumerate(schedule):
            block = target - completed
            if block <= 0:
                continue
            for result in self.run_block(self.agents, completed, block):
                self.aggregator.add(result)
                if self._exact_values is not None and result.final_money is not None:
                    self._exact_values.setdefault(result.strategy, []).append(result.final_money)
                yield result
            completed = target
            self.realized_runs = completed

            # Stopping is evaluated only here: after a *complete* block, with
            # every strategy at the same run count.
            confidence = look_confidence(self.config, look_index)
            accumulators = self.aggregator.inference_accumulators()
            states = self._rule_states(accumulators, confidence)
            all_eligible = bool(states) and all(state["eligible"] for state in states.values())
            is_last = look_index == len(schedule) - 1
            if all_eligible:
                decision = STOP_PRECISION_REACHED
            elif is_last:
                decision = self._terminal_reason(states)
            else:
                decision = "continue"
            self.recorder.record(
                runs_per_strategy=completed,
                look_index=look_index,
                accumulators=accumulators,
                counts=self.aggregator.counts(),
                rule_states=states,
                decision=decision,
                confidence=confidence,
                exact_values=self._exact_values,
            )
            if decision != "continue":
                self.stop_reason = decision
                self.unmet_criteria = _unmet(states)
                return
        self.stop_reason = self.stop_reason or STOP_MAX_RUNS_REACHED

    def _terminal_reason(self, states) -> str:
        """Why the maximum was reached without stopping earlier.

        A rare-event shortfall is reported as its own reason rather than
        folded into `max_runs_reached`: "we never saw 20 bankruptcies" and
        "the mean is still imprecise" call for completely different follow-up,
        and only the first one means the published rate is barely estimable.
        """
        for state in states.values():
            if state["rare_events"]["unmet"]:
                return STOP_RARE_EVENT_UNMET
        return STOP_MAX_RUNS_REACHED

    def convergence_document(self) -> dict:
        return self.recorder.document(
            self.stop_reason or STOP_MAX_RUNS_REACHED,
            self.unmet_criteria,
            self.realized_runs,
            self.sampling_plan.describe(),
        )


def _unmet(states) -> list:
    unmet = []
    for strategy, state in sorted(states.items()):
        for rule in state["rules"]:
            if not rule["met"]:
                unmet.append(
                    {
                        "strategy": strategy,
                        "criterion": rule["estimand"],
                        "half_width": rule["half_width"],
                        "target_half_width": rule["target_half_width"],
                        "target_relative_half_width": rule["target_relative_half_width"],
                        "reason": rule["reason"],
                    }
                )
        for criterion in state["rare_events"]["unmet"]:
            unmet.append(
                {
                    "strategy": strategy,
                    "criterion": criterion,
                    "bankruptcies": state["rare_events"]["bankruptcies"],
                    "survivals": state["rare_events"]["survivals"],
                }
            )
    return unmet


def fixed_convergence_document(
    aggregator, confidence: float, estimand_ids, runs: int, sampling_plan: dict
) -> dict:
    """A single terminal checkpoint for a fixed `--runs` batch.

    A fixed batch takes exactly one look, at the end, so its convergence
    document is a one-entry history rather than a missing artifact -- the
    published set stays the same shape whichever mode ran, and `main.py view`
    or a chart never has to special-case its absence.
    """
    config = AdaptiveConfig(
        min_runs=runs,
        max_runs=runs,
        checkpoint_runs=runs,
        rules=[],
        confidence=confidence,
    )
    recorder = ConvergenceRecorder(config, estimand_ids)
    accumulators = aggregator.inference_accumulators()
    recorder.record(
        runs_per_strategy=runs,
        look_index=0,
        accumulators=accumulators,
        counts=aggregator.counts(),
        rule_states={},
        decision="fixed_sample",
        confidence=confidence,
    )
    document = recorder.document("fixed_sample", [], runs, sampling_plan)
    document["design"]["mode"] = "fixed"
    return document


def summarize_stability(document: dict) -> dict:
    """Compact stability read-out over a convergence document.

    Answers the one question a reader of a convergence chart actually has --
    "has this settled?" -- as the largest relative estimate movement across
    the last window of checkpoints, per estimand.
    """
    checkpoints = document.get("checkpoints", [])
    if len(checkpoints) < 2:
        return {"checkpoints": len(checkpoints), "settled": None, "max_relative_drift": {}}
    window = document.get("design", {}).get("stability_window", DEFAULT_STABILITY_WINDOW)
    recent = checkpoints[-window:]
    drift = {}
    for estimand_id in document.get("estimands", []):
        movements = []
        for strategy in recent[-1].get("strategies", {}):
            values = [
                cp["strategies"]
                .get(strategy, {})
                .get("estimates", {})
                .get(estimand_id, {})
                .get("estimate")
                for cp in recent
            ]
            values = [v for v in values if v is not None]
            if len(values) < 2:
                continue
            scale = max(abs(statistics.fmean(values)), 1e-9)
            movements.append((max(values) - min(values)) / scale)
        if movements:
            drift[estimand_id] = max(movements)
    return {
        "checkpoints": len(checkpoints),
        "window": window,
        "max_relative_drift": drift,
        "settled": all(value < 0.01 for value in drift.values()) if drift else None,
    }
