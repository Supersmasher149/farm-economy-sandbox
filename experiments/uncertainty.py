"""Epistemic parameter uncertainty: what if the config numbers are wrong?

Every batch so far answers "how does the economy behave *given these config
values*". This module answers the other question -- "how much of what we
concluded survives the config values being uncertain" -- by sampling
configurations from declared distributions and re-running the simulation
under each one.

Three boundaries, all deliberate:

* **The uncertainty spec lives outside `config/*.json`.** Those files are the
  runtime contract, validated by `simulation/configuration.py`, and read by
  the C port from the same directory. Adding `{"distribution": "beta"}` beside
  `loss_chance` would break both. A separate `farm-uncertainty-v1` document
  references parameters by stable id-based path instead.
* **Every sampled configuration is deep-copied and revalidated.** A sampled
  value can land outside what the validator accepts (a negative price, a
  probability above 1); the sample is then rejected or resampled by a
  *declared* rule, and the count of rejections is published. Silently clamping
  would quietly change the distribution being sampled from.
* **Aleatory and epistemic variation are never pooled.** The nested design is
  configuration sample -> aleatory replicates -> strategies, so the variance
  of an outcome can be split into "we do not know the parameters" and "the
  simulation is stochastic". Those two call for completely different
  responses -- more field data versus more runs -- and a single pooled number
  hides which one is binding.

Correlated parameters are sampled through a Gaussian copula on a declared
correlation group; a parameter naming an undeclared group is an error rather
than being sampled independently, because independent sampling of parameters
a domain expert said move together is exactly the failure that makes an
uncertainty analysis over-confident.
"""

import copy
import json
import math
import random
import re
import statistics
from dataclasses import dataclass, field

from metrics.inference import (
    DEFAULT_CONFIDENCE,
    MomentAccumulator,
    beta_quantile,
    derive_analysis_seed,
    normal_quantile,
    publish_mapping,
)

UNCERTAINTY_SCHEMA = "farm-uncertainty-v1"
RESULT_SCHEMA = "farm-uncertainty-results-v1"

CLASSIFICATIONS = ("epistemic", "aleatory")
INVALID_POLICIES = ("resample", "reject")
DEFAULT_MAX_RESAMPLE_ATTEMPTS = 50


class UncertaintySpecError(ValueError):
    """Raised for a malformed or internally inconsistent specification."""


# --------------------------------------------------------------------------
# Distributions (inverse-CDF form)
# --------------------------------------------------------------------------
#
# Every distribution exposes `ppf(u)` rather than only a `sample(rng)`. That is
# what lets one sampling engine serve plain Monte Carlo, Latin hypercube,
# Morris trajectories and Sobol matrices: each design produces uniforms in
# [0, 1] and this layer maps them into parameter space. It is also what makes
# a correlated sample possible at all -- the Gaussian copula works on uniforms.


def _require(condition, message):
    if not condition:
        raise UncertaintySpecError(message)


class Distribution:
    name = "abstract"

    def ppf(self, u: float) -> float:
        raise NotImplementedError

    def sample(self, rng: random.Random) -> float:
        return self.ppf(rng.random())

    def describe(self) -> dict:
        return {"distribution": self.name}


@dataclass
class Uniform(Distribution):
    low: float
    high: float
    name: str = "uniform"

    def ppf(self, u):
        return self.low + (self.high - self.low) * u

    def describe(self):
        return {"distribution": "uniform", "low": self.low, "high": self.high}


@dataclass
class Normal(Distribution):
    mean: float
    sd: float
    name: str = "normal"

    def ppf(self, u):
        return self.mean + self.sd * normal_quantile(min(max(u, 1e-12), 1 - 1e-12))

    def describe(self):
        return {"distribution": "normal", "mean": self.mean, "sd": self.sd}


@dataclass
class LogNormal(Distribution):
    mu: float
    sigma: float
    name: str = "lognormal"

    def ppf(self, u):
        return math.exp(self.mu + self.sigma * normal_quantile(min(max(u, 1e-12), 1 - 1e-12)))

    def describe(self):
        return {"distribution": "lognormal", "mu": self.mu, "sigma": self.sigma}


@dataclass
class Beta(Distribution):
    alpha: float
    beta: float
    name: str = "beta"

    def ppf(self, u):
        return beta_quantile(min(max(u, 0.0), 1.0), self.alpha, self.beta)

    def describe(self):
        return {"distribution": "beta", "alpha": self.alpha, "beta": self.beta}


@dataclass
class Triangular(Distribution):
    low: float
    mode: float
    high: float
    name: str = "triangular"

    def ppf(self, u):
        span = self.high - self.low
        if span <= 0:
            return self.low
        cut = (self.mode - self.low) / span
        if u < cut:
            return self.low + math.sqrt(u * span * (self.mode - self.low))
        return self.high - math.sqrt((1 - u) * span * (self.high - self.mode))

    def describe(self):
        return {
            "distribution": "triangular",
            "low": self.low,
            "mode": self.mode,
            "high": self.high,
        }


@dataclass
class Discrete(Distribution):
    values: list
    weights: list = None
    name: str = "discrete"

    def ppf(self, u):
        weights = self.weights or [1.0] * len(self.values)
        total = sum(weights)
        cumulative = 0.0
        for value, weight in zip(self.values, weights, strict=True):
            cumulative += weight / total
            if u <= cumulative:
                return value
        return self.values[-1]

    def describe(self):
        return {"distribution": "discrete", "values": self.values, "weights": self.weights}


@dataclass
class Constant(Distribution):
    value: float
    name: str = "constant"

    def ppf(self, u):
        return self.value

    def describe(self):
        return {"distribution": "constant", "value": self.value}


def build_distribution(name: str, parameters: dict) -> Distribution:
    builders = {
        "uniform": lambda p: Uniform(float(p["low"]), float(p["high"])),
        "normal": lambda p: Normal(float(p["mean"]), float(p["sd"])),
        "lognormal": lambda p: LogNormal(float(p["mu"]), float(p["sigma"])),
        "beta": lambda p: Beta(float(p["alpha"]), float(p["beta"])),
        "triangular": lambda p: Triangular(float(p["low"]), float(p["mode"]), float(p["high"])),
        "discrete": lambda p: Discrete(list(p["values"]), p.get("weights")),
        "constant": lambda p: Constant(float(p["value"])),
    }
    builder = builders.get(name)
    _require(builder is not None, f"unknown distribution {name!r}; known: {sorted(builders)}")
    try:
        distribution = builder(parameters)
    except KeyError as exc:
        raise UncertaintySpecError(
            f"distribution {name!r} is missing parameter {exc.args[0]!r}"
        ) from None
    _validate_distribution(distribution)
    return distribution


def _validate_distribution(distribution: Distribution) -> None:
    if isinstance(distribution, Uniform):
        _require(distribution.high > distribution.low, "uniform needs high > low")
    elif isinstance(distribution, Normal):
        _require(distribution.sd > 0, "normal needs sd > 0")
    elif isinstance(distribution, LogNormal):
        _require(distribution.sigma > 0, "lognormal needs sigma > 0")
    elif isinstance(distribution, Beta):
        _require(
            distribution.alpha > 0 and distribution.beta > 0,
            "beta needs alpha > 0 and beta > 0",
        )
    elif isinstance(distribution, Triangular):
        _require(
            distribution.low <= distribution.mode <= distribution.high
            and distribution.high > distribution.low,
            "triangular needs low <= mode <= high and high > low",
        )
    elif isinstance(distribution, Discrete):
        _require(bool(distribution.values), "discrete needs at least one value")
        if distribution.weights is not None:
            _require(
                len(distribution.weights) == len(distribution.values),
                "discrete weights must match values",
            )
            _require(all(w >= 0 for w in distribution.weights), "discrete weights must be >= 0")
            _require(sum(distribution.weights) > 0, "discrete weights must not all be zero")


# --------------------------------------------------------------------------
# Parameter paths
# --------------------------------------------------------------------------
#
# `crops[id=quickweed].loss_chance` rather than `crops[0].loss_chance`: an
# index silently retargets the moment a crop is inserted, and an uncertainty
# study that quietly starts perturbing a different crop is worse than one that
# fails loudly.

_SEGMENT = re.compile(r"^([A-Za-z_][\w-]*)(?:\[(.+)\])?$")


def _split_path(path: str) -> list:
    segments = []
    for raw in path.split("."):
        match = _SEGMENT.match(raw.strip())
        _require(match is not None, f"malformed path segment {raw!r} in {path!r}")
        name, selector = match.group(1), match.group(2)
        segments.append((name, selector))
    return segments


def _select(container, selector, path):
    if selector is None:
        return container
    if "=" in selector:
        key, value = selector.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        _require(isinstance(container, list), f"{path!r}: [{selector}] needs a list")
        matches = [item for item in container if str(item.get(key)) == value]
        _require(
            len(matches) == 1,
            f"{path!r}: selector [{selector}] matched {len(matches)} entries, expected exactly 1",
        )
        return matches[0]
    _require(selector.isdigit(), f"{path!r}: selector [{selector}] must be key=value or an index")
    index = int(selector)
    _require(
        isinstance(container, list) and 0 <= index < len(container),
        f"{path!r}: index {index} out of range",
    )
    return container[index]


def resolve_path(document, path: str):
    """Return `(container, key)` for a parameter path, ready to read or write."""
    segments = _split_path(path)
    current = document
    for name, selector in segments[:-1]:
        _require(
            isinstance(current, dict) and name in current,
            f"{path!r}: no such key {name!r}",
        )
        current = _select(current[name], selector, path)
    name, selector = segments[-1]
    _require(
        isinstance(current, dict) and name in current,
        f"{path!r}: no such key {name!r}",
    )
    if selector is not None:
        current = _select(current[name], selector, path)
        return current, None
    return current, name


def read_path(document, path: str):
    container, key = resolve_path(document, path)
    return container if key is None else container[key]


def write_path(document, path: str, value) -> None:
    container, key = resolve_path(document, path)
    _require(key is not None, f"{path!r} does not address a scalar value")
    container[key] = value


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------


@dataclass
class UncertainParameter:
    id: str
    path: str
    distribution: Distribution
    classification: str = "epistemic"
    unit: str = ""
    transform: str = "identity"
    source: str = ""
    correlation_group: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    integer: bool = False
    notes: str = ""

    def apply_transform(self, value):
        if self.transform in ("identity", ""):
            pass
        elif self.transform == "exp":
            value = math.exp(value)
        elif self.transform == "logit":
            value = 1.0 / (1.0 + math.exp(-value))
        elif self.transform == "percent_to_fraction":
            value = value / 100.0
        else:
            raise UncertaintySpecError(f"unknown transform {self.transform!r}")
        if self.integer:
            value = int(round(value))
        return value

    def admissible(self, value) -> bool:
        below = self.minimum is not None and value < self.minimum
        above = self.maximum is not None and value > self.maximum
        return not (below or above)

    def describe(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            **self.distribution.describe(),
            "classification": self.classification,
            "unit": self.unit,
            "transform": self.transform,
            "source": self.source,
            "correlation_group": self.correlation_group,
            "constraints": {"min": self.minimum, "max": self.maximum, "integer": self.integer},
            **({"notes": self.notes} if self.notes else {}),
        }


@dataclass
class UncertaintySpec:
    parameters: list
    correlation_groups: dict = field(default_factory=dict)
    on_invalid: str = "resample"
    max_attempts: int = DEFAULT_MAX_RESAMPLE_ATTEMPTS
    name: str = ""
    description: str = ""

    def epistemic(self) -> list:
        return [p for p in self.parameters if p.classification == "epistemic"]

    def describe(self) -> dict:
        return {
            "schema": UNCERTAINTY_SCHEMA,
            "name": self.name,
            "description": self.description,
            "on_invalid": self.on_invalid,
            "max_attempts": self.max_attempts,
            "correlation_groups": self.correlation_groups,
            "parameters": [p.describe() for p in self.parameters],
        }


def load_spec(path: str) -> UncertaintySpec:
    with open(path) as handle:
        return parse_spec(json.load(handle))


def parse_spec(document: dict) -> UncertaintySpec:
    _require(
        document.get("schema") == UNCERTAINTY_SCHEMA,
        f"specification schema must be {UNCERTAINTY_SCHEMA!r}, got {document.get('schema')!r}",
    )
    raw_parameters = document.get("parameters")
    _require(
        isinstance(raw_parameters, list) and raw_parameters,
        "specification needs a non-empty 'parameters' list",
    )
    groups = document.get("correlation_groups", {}) or {}
    for name, group in groups.items():
        rho = group.get("correlation")
        _require(
            isinstance(rho, (int, float)) and 0.0 <= rho < 1.0,
            f"correlation group {name!r} needs 'correlation' in [0, 1)",
        )

    on_invalid = document.get("on_invalid", "resample")
    _require(on_invalid in INVALID_POLICIES, f"on_invalid must be one of {INVALID_POLICIES}")

    parameters = []
    seen = set()
    for entry in raw_parameters:
        path = entry.get("path")
        _require(isinstance(path, str) and path, "every parameter needs a 'path'")
        parameter_id = entry.get("id") or path
        _require(parameter_id not in seen, f"duplicate parameter id {parameter_id!r}")
        seen.add(parameter_id)
        classification = entry.get("classification", "epistemic")
        _require(
            classification in CLASSIFICATIONS,
            f"{parameter_id!r}: classification must be one of {CLASSIFICATIONS}",
        )
        group = entry.get("correlation_group")
        if group is not None:
            _require(
                group in groups,
                f"{parameter_id!r} names correlation group {group!r}, which is not declared. "
                "Declare it (with a correlation) or remove the reference -- parameters an "
                "expert said move together must not be sampled independently by accident.",
            )
        constraints = entry.get("constraints", {}) or {}
        parameters.append(
            UncertainParameter(
                id=parameter_id,
                path=path,
                distribution=build_distribution(
                    entry.get("distribution", ""), entry.get("parameters", {}) or {}
                ),
                classification=classification,
                unit=entry.get("unit", ""),
                transform=entry.get("transform", "identity"),
                source=entry.get("source", ""),
                correlation_group=group,
                minimum=constraints.get("min"),
                maximum=constraints.get("max"),
                integer=bool(constraints.get("integer", False)),
                notes=entry.get("notes", ""),
            )
        )
    return UncertaintySpec(
        parameters=parameters,
        correlation_groups=groups,
        on_invalid=on_invalid,
        max_attempts=int(document.get("max_attempts", DEFAULT_MAX_RESAMPLE_ATTEMPTS)),
        name=document.get("name", ""),
        description=document.get("description", ""),
    )


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def correlated_uniforms(spec: UncertaintySpec, rng: random.Random) -> dict:
    """One uniform per parameter, correlated within declared groups.

    Gaussian copula: members of a group share a latent normal,
    Z_i = sqrt(rho) * Z_group + sqrt(1 - rho) * E_i, mapped back through the
    normal CDF. That induces the declared correlation between the *ranks* of
    the parameters while leaving each marginal distribution exactly as
    specified -- which is the point, since the marginals are what the domain
    evidence actually pins down.
    """
    latents = {name: rng.gauss(0.0, 1.0) for name in spec.correlation_groups}
    uniforms = {}
    for parameter in spec.parameters:
        if parameter.correlation_group:
            rho = spec.correlation_groups[parameter.correlation_group]["correlation"]
            z = math.sqrt(rho) * latents[parameter.correlation_group] + math.sqrt(
                1.0 - rho
            ) * rng.gauss(0.0, 1.0)
            uniforms[parameter.id] = _standard_normal_cdf(z)
        else:
            uniforms[parameter.id] = rng.random()
    return uniforms


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def values_from_uniforms(spec: UncertaintySpec, uniforms: dict) -> dict:
    values = {}
    for parameter in spec.parameters:
        raw = parameter.distribution.ppf(uniforms[parameter.id])
        values[parameter.id] = parameter.apply_transform(raw)
    return values


def apply_values(bundle: dict, spec: UncertaintySpec, values: dict) -> dict:
    """Deep-copy the config bundle and write the sampled values into it.

    Deep copy, always: the caller's bundle is the baseline for every other
    sample and for the un-perturbed reference run, and an in-place write would
    make sample k+1 start from sample k.
    """
    sampled = copy.deepcopy(bundle)
    for parameter in spec.parameters:
        write_path(sampled, parameter.path, values[parameter.id])
    return sampled


@dataclass
class ConfigurationSample:
    sample_id: int
    values: dict
    bundle: dict
    attempts: int = 1
    valid: bool = True
    rejection_reason: str | None = None

    def record(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "values": self.values,
            "attempts": self.attempts,
            "valid": self.valid,
            "rejection_reason": self.rejection_reason,
        }


def sample_configuration(
    bundle: dict,
    spec: UncertaintySpec,
    rng: random.Random,
    sample_id: int,
    validator=None,
    uniforms: dict | None = None,
) -> ConfigurationSample:
    """One epistemic configuration sample, validated by the real validator.

    `on_invalid` decides what happens when a sampled configuration is not
    admissible: `resample` draws again (up to `max_attempts`) and `reject`
    records the sample as invalid and moves on. Either way the outcome is
    recorded per sample, so a study whose spec produces mostly-invalid
    configurations is visible rather than quietly thinned.
    """
    attempts = 0
    last_reason = None
    fixed_uniforms = uniforms
    while attempts < max(1, spec.max_attempts):
        attempts += 1
        drawn = fixed_uniforms if fixed_uniforms is not None else correlated_uniforms(spec, rng)
        values = values_from_uniforms(spec, drawn)
        inadmissible = [
            parameter.id
            for parameter in spec.parameters
            if not parameter.admissible(values[parameter.id])
        ]
        if inadmissible:
            last_reason = f"outside declared constraints: {inadmissible}"
        else:
            sampled = apply_values(bundle, spec, values)
            if validator is None:
                return ConfigurationSample(sample_id, values, sampled, attempts, True)
            try:
                validator(sampled)
                return ConfigurationSample(sample_id, values, sampled, attempts, True)
            except Exception as exc:
                last_reason = f"failed configuration validation: {exc}"
        if spec.on_invalid == "reject" or fixed_uniforms is not None:
            break
    return ConfigurationSample(
        sample_id,
        values,
        bundle,
        attempts,
        False,
        last_reason or "no admissible sample found",
    )


# --------------------------------------------------------------------------
# Nested execution
# --------------------------------------------------------------------------


@dataclass
class ScenarioResponse:
    """Aleatory summary of one (configuration sample, strategy) cell."""

    sample_id: int
    strategy: str
    estimand: str
    mean: float | None
    stdev: float | None
    standard_error: float | None
    n: int

    def record(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "strategy": self.strategy,
            "estimand": self.estimand,
            "mean": self.mean,
            "aleatory_stdev": self.stdev,
            "standard_error": self.standard_error,
            "n": self.n,
        }


def summarize_runs(results, sample_id: int, estimand_ids) -> list:
    """Collapse one configuration's replicates into per-strategy responses.

    The response *is* the aleatory mean, so the between-sample spread of these
    values is epistemic variance and the reported `aleatory_stdev` is the
    within-sample part. Keeping them separate here is what makes the
    decomposition below meaningful rather than a relabelled total variance.
    """
    from metrics.estimands import get as get_estimand

    accumulators = {}
    for result in results:
        for estimand_id in estimand_ids:
            estimand = get_estimand(estimand_id)
            value = estimand.observe(result)
            if value is None:
                continue
            key = (result.strategy, estimand_id)
            accumulators.setdefault(key, MomentAccumulator()).add(float(value))
    responses = []
    for (strategy, estimand_id), accumulator in sorted(accumulators.items()):
        responses.append(
            ScenarioResponse(
                sample_id=sample_id,
                strategy=strategy,
                estimand=estimand_id,
                mean=accumulator.mean(),
                stdev=accumulator.stdev(),
                standard_error=accumulator.standard_error(),
                n=accumulator.count,
            )
        )
    return responses


def decompose_variance(responses: list, estimand_id: str, strategy: str | None = None) -> dict:
    """Split outcome variance into epistemic (between-config) and aleatory.

    Between-sample variance of the per-sample means is the epistemic part;
    the mean of the within-sample variances is the aleatory part. Reporting
    the ratio answers the question a study is actually for: would more runs
    help, or only better parameter knowledge?
    """
    selected = [
        r
        for r in responses
        if r.estimand == estimand_id and (strategy is None or r.strategy == strategy)
    ]
    means = [r.mean for r in selected if r.mean is not None]
    within = [r.stdev**2 for r in selected if r.stdev is not None]
    epistemic = statistics.variance(means) if len(means) > 1 else None
    aleatory = statistics.fmean(within) if within else None
    total = None
    if epistemic is not None and aleatory is not None:
        total = epistemic + aleatory
    return {
        "estimand": estimand_id,
        "strategy": strategy,
        "configuration_samples": len(means),
        "epistemic_variance": epistemic,
        "aleatory_variance": aleatory,
        "total_variance": total,
        "epistemic_share": (
            epistemic / total if total not in (None, 0) and epistemic is not None else None
        ),
        "note": (
            "Epistemic = between-configuration variance of per-configuration means. "
            "Aleatory = mean within-configuration variance across replicates. "
            "They are reported separately and never summed into a single interval."
        ),
    }


def run_study(
    bundle: dict,
    spec: UncertaintySpec,
    simulate,
    design,
    estimand_ids=("expected_final_money", "bankruptcy_probability"),
    validator=None,
    analysis_seed: int = 0,
    confidence: float = DEFAULT_CONFIDENCE,
) -> dict:
    """Run a nested epistemic study and return the publishable document.

    `simulate(config_bundle, sample_id) -> iterable[RunResult]` is injected by
    the caller so this module stays free of the agent registry and the batch
    runner; `design` yields `(sample_id, uniforms | None)` pairs, which is what
    lets the same executor serve Monte Carlo, LHS, Morris and Sobol.
    """
    rng = random.Random(derive_analysis_seed(analysis_seed, "uncertainty", spec.name))
    samples = []
    responses = []
    for sample_id, uniforms in design:
        sample = sample_configuration(
            bundle, spec, rng, sample_id, validator=validator, uniforms=uniforms
        )
        samples.append(sample)
        if not sample.valid:
            continue
        results = list(simulate(sample.bundle, sample_id))
        responses.extend(summarize_runs(results, sample_id, estimand_ids))

    strategies = sorted({r.strategy for r in responses})
    decomposition = [
        decompose_variance(responses, estimand_id, strategy)
        for estimand_id in estimand_ids
        for strategy in strategies
    ]
    return publish_mapping(
        {
            "schema": RESULT_SCHEMA,
            "confidence": confidence,
            "analysis_seed": analysis_seed,
            "specification": spec.describe(),
            "estimands": list(estimand_ids),
            "parameter_matrix": [sample.record() for sample in samples],
            "valid_samples": sum(1 for sample in samples if sample.valid),
            "rejected_samples": sum(1 for sample in samples if not sample.valid),
            "responses": [response.record() for response in responses],
            "variance_decomposition": decomposition,
        }
    )
