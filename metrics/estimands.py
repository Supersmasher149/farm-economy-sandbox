"""The estimand registry: what each reported number formally *means*.

An "average final money" is not one number until three questions are
answered: averaged over which runs, extracted from which field, and undefined
or zero when the cohort is empty. This module answers those questions once, in
data, so every inferential result downstream can cite an estimand id instead
of re-litigating the denominator in each renderer.

Each entry carries, per plan Section 1:

* a stable id and display name;
* the unit of analysis (always one simulated run here -- never one planting,
  never one day, which is what keeps run-weighted and planting-weighted
  metrics from being silently mixed);
* the population or conditional cohort it is defined over;
* the per-run extraction function;
* the missing-value policy;
* the weighting convention;
* the confidence-interval method it supports;
* whether it can drive adaptive stopping and strategy comparisons.

Three distinctions this registry exists to keep visible, all of which the
codebase already makes but did not previously name:

* **Mean of ratios vs ratio of pooled totals.** `expected_profit_per_day` is
  the mean of each run's own `net_profit / days_simulated`, matching
  `aggregate_results.avg_profit_per_day`. It is not total profit over total
  days, which would weight long runs more and understate strategies that go
  bankrupt early.
* **Survivor-only vs all-run.** `expected_final_money` includes bankrupt
  runs. `expected_final_money_survivors` does not, and says so in its
  population field.
* **Undefined vs observed zero.** A run that never matured a crop has no
  crop-loss rate; `missing_policy="skip"` means it is left out of the
  denominator rather than averaged in as 0%.

And one the plan calls out explicitly: `conditional_bankruptcy_day` is
E[D_B | B_T = 1] -- the mean day among runs that *did* go bankrupt. It is not
a survival estimate, because runs that never went bankrupt are censored, not
observed at day infinity. `metrics/distributions.survival_curve` is the
censoring-aware view of the same question.
"""

from collections.abc import Callable
from dataclasses import dataclass

# Bumped whenever an estimand's definition, extraction, or cohort changes in a
# way that moves a published number. Recorded in analysis metadata so an old
# report can be read against the definitions it was produced under.
ESTIMAND_REGISTRY_VERSION = "farm-estimands-v1"

UNIT_OF_ANALYSIS = "simulation_run"


@dataclass(frozen=True)
class Estimand:
    id: str
    name: str
    kind: str  # "mean" | "proportion" | "quantile"
    unit: str
    population: str
    definition: str
    extract: Callable
    missing_policy: str = "none"
    weighting: str = "equal_per_run"
    ci_method: str = "student_t"
    unit_of_analysis: str = UNIT_OF_ANALYSIS
    supports_adaptive: bool = True
    supports_comparison: bool = True
    quantile_p: float | None = None
    cohort: Callable | None = None
    notes: str = ""

    def observe(self, run):
        """Value this run contributes, or None if it is outside the cohort or
        did not observe the quantity. `None` is always "not observed" and is
        never folded into a mean as a real zero."""
        if self.cohort is not None and not self.cohort(run):
            return None
        return self.extract(run)

    def to_metadata(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "unit": self.unit,
            "unit_of_analysis": self.unit_of_analysis,
            "population": self.population,
            "definition": self.definition,
            "missing_policy": self.missing_policy,
            "weighting": self.weighting,
            "ci_method": self.ci_method,
            "supports_adaptive": self.supports_adaptive,
            "supports_comparison": self.supports_comparison,
            **({"quantile_p": self.quantile_p} if self.quantile_p is not None else {}),
            **({"notes": self.notes} if self.notes else {}),
        }


def _attr(name):
    return lambda run: getattr(run, name)


def _bankrupt(run) -> bool:
    return bool(run.bankrupt)


def _survived(run) -> bool:
    return not run.bankrupt


_ESTIMAND_LIST = (
    Estimand(
        id="expected_final_money",
        name="Expected final money",
        kind="mean",
        unit="currency",
        population="all_runs",
        definition="E[M_T] over all runs, bankrupt and surviving alike",
        extract=_attr("final_money"),
        ci_method="student_t",
        notes=(
            "Read from the canonical cent-rounded RunResult.final_money, so the "
            "interval brackets the same number the descriptive report prints."
        ),
    ),
    Estimand(
        id="bankruptcy_probability",
        name="Bankruptcy probability",
        kind="proportion",
        unit="probability",
        population="all_runs",
        definition="P(B_T = 1): bankruptcy by the configured simulation horizon",
        extract=_bankrupt,
        ci_method="wilson",
        notes=(
            "A horizon-bounded probability, not a hazard rate: a run that would "
            "have failed on day H+1 counts as a survivor here."
        ),
    ),
    Estimand(
        id="expected_profit_per_day",
        name="Expected profit per day",
        kind="mean",
        unit="currency_per_day",
        population="all_runs",
        definition="E[(M_T - M_0) / D]: the mean of per-run ratios",
        extract=_attr("avg_profit_per_day"),
        weighting="equal_per_run_mean_of_ratios",
        ci_method="student_t",
        notes=(
            "Mean of ratios, not ratio of pooled totals -- matches "
            "aggregate_results.avg_profit_per_day. Pooling would weight long "
            "runs more and flatter strategies that go bankrupt early."
        ),
    ),
    Estimand(
        id="final_money_quantile",
        name="Final-money quantile",
        kind="quantile",
        unit="currency",
        population="all_runs",
        definition="Q_p(M_T) = inf {m : F(m) >= p}, over all runs",
        extract=_attr("final_money"),
        ci_method="percentile_bootstrap",
        quantile_p=0.5,
        supports_adaptive=False,
        notes=(
            "Formal quantiles come from the exact per-run observations in "
            "run_results.csv, never from the bounded median reservoir, which is "
            "approximate above its capacity and labelled as such."
        ),
    ),
    Estimand(
        id="expected_final_money_survivors",
        name="Expected final money (survivors)",
        kind="mean",
        unit="currency",
        population="surviving_runs",
        definition="E[M_T | B_T = 0]",
        extract=_attr("final_money"),
        cohort=_survived,
        missing_policy="skip_outside_cohort",
        ci_method="student_t",
        notes="Conditional on survival; not comparable to expected_final_money.",
    ),
    Estimand(
        id="conditional_bankruptcy_day",
        name="Conditional bankruptcy day",
        kind="mean",
        unit="day",
        population="bankrupt_runs",
        definition="E[D_B | B_T = 1]",
        extract=_attr("bankruptcy_day"),
        cohort=_bankrupt,
        missing_policy="skip_outside_cohort",
        ci_method="student_t",
        supports_adaptive=False,
        notes=(
            "Explicitly conditional, and explicitly NOT a survival estimate: "
            "surviving runs are censored at the horizon, not observed later. "
            "See metrics.distributions.survival_curve for the censoring-aware view."
        ),
    ),
    Estimand(
        id="expected_minimum_cash_balance",
        name="Expected minimum cash balance",
        kind="mean",
        unit="currency",
        population="all_runs",
        definition="E[min_t M_t]: the mean of each run's own low-water mark",
        extract=_attr("minimum_cash_balance"),
        ci_method="student_t",
    ),
    Estimand(
        id="expected_crop_loss_rate",
        name="Expected crop loss rate",
        kind="mean",
        unit="percent",
        population="runs_with_harvest_events",
        definition="E[100 * lost / matured | matured > 0]",
        extract=_attr("crop_loss_rate"),
        missing_policy="skip_undefined",
        ci_method="student_t",
        notes=(
            "Runs that matured nothing contribute no observation. Averaging "
            "them in as 0% would read as flawless husbandry rather than as "
            "nothing having happened."
        ),
    ),
    Estimand(
        id="first_upgrade_probability",
        name="First-upgrade probability",
        kind="proportion",
        unit="probability",
        population="all_runs",
        definition="P(at least one upgrade purchased by the horizon)",
        extract=lambda run: run.first_upgrade_day is not None,
        ci_method="wilson",
    ),
)

REGISTRY = {estimand.id: estimand for estimand in _ESTIMAND_LIST}

# Estimands rendered in the default inference block of every batch summary.
DEFAULT_ESTIMANDS = (
    "expected_final_money",
    "bankruptcy_probability",
    "expected_profit_per_day",
    "expected_final_money_survivors",
    "conditional_bankruptcy_day",
    "expected_minimum_cash_balance",
    "first_upgrade_probability",
)

# Estimands that a comparison table reports by default.
DEFAULT_COMPARISON_ESTIMANDS = (
    "expected_final_money",
    "expected_profit_per_day",
    "bankruptcy_probability",
)


class UnknownEstimand(KeyError):
    """Raised for an estimand id that is not registered."""


def get(estimand_id: str) -> Estimand:
    try:
        return REGISTRY[estimand_id]
    except KeyError:
        raise UnknownEstimand(
            f"Unknown estimand {estimand_id!r}. Known: {', '.join(sorted(REGISTRY))}"
        ) from None


def adaptive_estimands() -> list[str]:
    return [eid for eid, estimand in REGISTRY.items() if estimand.supports_adaptive]


def comparable_estimands() -> list[str]:
    return [eid for eid, estimand in REGISTRY.items() if estimand.supports_comparison]


def metadata_document(estimand_ids=None) -> dict:
    """The `estimands` block published in summary.json."""
    ids = list(estimand_ids) if estimand_ids else list(REGISTRY)
    return {eid: get(eid).to_metadata() for eid in ids}
