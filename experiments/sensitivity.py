"""Sensitivity designs: which uncertain inputs actually move the outcome?

Implemented in the order plan Section 8 asks for, which is also the order of
increasing cost per answer:

1. **One-at-a-time (low/base/high).** k parameters, 2k + 1 configurations.
   Cheap, immediately readable, and honest about its limitation: it explores a
   cross through the parameter space and cannot see interactions at all.
2. **Scenario robustness.** Named corners a designer already cares about
   ("pessimistic weather, optimistic prices"), each a full configuration.
3. **Latin hypercube.** Stratified coverage of the whole space at a chosen
   sample count -- better space-filling than plain Monte Carlo at the same n.
4. **Morris screening.** Elementary effects over random trajectories: ranks
   many parameters by influence, and its sigma separates "large effect" from
   "effect that depends on everything else", at r * (k + 1) configurations.
5. **Sobol indices.** Variance decomposition (first-order and total) at
   N * (k + 2) configurations -- the most informative and by far the most
   expensive, which is exactly why it is last rather than first.

Every design produces uniforms in [0, 1] per parameter; the marginal
distributions are applied by `experiments/uncertainty.py`. That split is what
lets a spec change its distributions without touching a single design.

**Latin hypercube, Morris and Sobol all require independent inputs.** Each
stratifies or permutes one parameter at a time, which destroys any declared
correlation structure. Rather than silently sampling correlated parameters
independently -- the exact failure mode that makes an uncertainty study
over-confident -- these designs refuse to run against a spec that declares
correlation groups, and say to use the plain Monte Carlo design (which does
honour the copula) instead.
"""

import random
import statistics

from experiments.uncertainty import UncertaintySpecError


def _reject_correlated(spec, design_name: str) -> None:
    if spec.correlation_groups:
        raise UncertaintySpecError(
            f"{design_name} stratifies each parameter independently, which would discard the "
            f"declared correlation group(s) {sorted(spec.correlation_groups)}. Use the "
            "monte_carlo design (it samples through the Gaussian copula) or remove the "
            "correlation groups from the specification."
        )


def _parameter_ids(spec) -> list:
    return [parameter.id for parameter in spec.epistemic()]


def monte_carlo_design(spec, samples: int, seed: int = 0):
    """Plain Monte Carlo. Yields `(sample_id, None)` so the sampler draws --
    including through the correlation copula, which only this design keeps."""
    points = [(index, None) for index in range(samples)]
    metadata = {
        "design": "monte_carlo",
        "samples": samples,
        "honours_correlation_groups": True,
        "points": {str(index): {"kind": "monte_carlo"} for index, _ in points},
    }
    return points, metadata


def latin_hypercube_design(spec, samples: int, seed: int = 0):
    """Latin hypercube: one observation per stratum per parameter."""
    _reject_correlated(spec, "Latin hypercube sampling")
    rng = random.Random(seed)
    ids = _parameter_ids(spec)
    columns = {}
    for parameter_id in ids:
        strata = [(i + rng.random()) / samples for i in range(samples)]
        rng.shuffle(strata)
        columns[parameter_id] = strata
    points = [(index, {pid: columns[pid][index] for pid in ids}) for index in range(samples)]
    metadata = {
        "design": "latin_hypercube",
        "samples": samples,
        "honours_correlation_groups": False,
        "points": {str(index): {"kind": "lhs"} for index, _ in points},
    }
    return points, metadata


def one_at_a_time_design(spec, low: float = 0.05, high: float = 0.95, base: float = 0.5):
    """Low/base/high cross: 2k + 1 configurations.

    Levels are given as *quantiles* of each parameter's own distribution, not
    as raw values, so "low" means the same degree of pessimism for a beta and
    for a triangular without the spec author converting anything by hand.
    """
    ids = _parameter_ids(spec)
    points = [(0, {pid: base for pid in ids})]
    labels = {"0": {"kind": "base", "parameter": None, "level": "base"}}
    sample_id = 1
    for parameter_id in ids:
        for level, quantile in (("low", low), ("high", high)):
            uniforms = {pid: base for pid in ids}
            uniforms[parameter_id] = quantile
            points.append((sample_id, uniforms))
            labels[str(sample_id)] = {
                "kind": "oat",
                "parameter": parameter_id,
                "level": level,
                "quantile": quantile,
            }
            sample_id += 1
    metadata = {
        "design": "one_at_a_time",
        "samples": len(points),
        "levels": {"low": low, "base": base, "high": high},
        "honours_correlation_groups": False,
        "caveat": (
            "A cross through parameter space: it measures each parameter's effect "
            "with every other at its median, and cannot detect interactions."
        ),
        "points": labels,
    }
    return points, metadata


def scenario_design(spec, scenarios: dict):
    """Named corners, each a dict of {parameter_id: quantile in [0, 1]}.

    Unnamed parameters sit at their median, so a scenario document only has to
    state what it actually means to change.
    """
    ids = _parameter_ids(spec)
    points = []
    labels = {}
    for sample_id, (name, assignment) in enumerate(scenarios.items()):
        unknown = sorted(set(assignment) - set(ids))
        if unknown:
            raise UncertaintySpecError(f"scenario {name!r} names unknown parameter(s): {unknown}")
        uniforms = {pid: 0.5 for pid in ids}
        uniforms.update({pid: float(q) for pid, q in assignment.items()})
        points.append((sample_id, uniforms))
        labels[str(sample_id)] = {"kind": "scenario", "name": name, "assignment": assignment}
    metadata = {
        "design": "scenarios",
        "samples": len(points),
        "honours_correlation_groups": False,
        "points": labels,
    }
    return points, metadata


def morris_design(spec, trajectories: int = 10, levels: int = 4, seed: int = 0):
    """Morris trajectories: r * (k + 1) configurations of one-step changes."""
    _reject_correlated(spec, "Morris screening")
    rng = random.Random(seed)
    ids = _parameter_ids(spec)
    if levels < 2 or levels % 2:
        raise UncertaintySpecError("Morris needs an even number of levels >= 2")
    delta = levels / (2.0 * (levels - 1))
    grid = [i / (levels - 1) for i in range(levels)]
    admissible_start = [value for value in grid if value + delta <= 1.0 + 1e-12]

    points = []
    labels = {}
    sample_id = 0
    for trajectory in range(trajectories):
        current = {pid: rng.choice(admissible_start) for pid in ids}
        order = list(ids)
        rng.shuffle(order)
        points.append((sample_id, dict(current)))
        labels[str(sample_id)] = {
            "kind": "morris",
            "trajectory": trajectory,
            "step": 0,
            "changed": None,
        }
        sample_id += 1
        for step, parameter_id in enumerate(order, start=1):
            current = dict(current)
            current[parameter_id] = min(1.0, current[parameter_id] + delta)
            points.append((sample_id, dict(current)))
            labels[str(sample_id)] = {
                "kind": "morris",
                "trajectory": trajectory,
                "step": step,
                "changed": parameter_id,
                "delta": delta,
            }
            sample_id += 1
    metadata = {
        "design": "morris",
        "trajectories": trajectories,
        "levels": levels,
        "delta": delta,
        "samples": len(points),
        "parameters": ids,
        "honours_correlation_groups": False,
        "points": labels,
    }
    return points, metadata


def sobol_design(spec, base_samples: int = 32, seed: int = 0):
    """Saltelli sampling: A, B and the k AB_i matrices -- N * (k + 2) configs."""
    _reject_correlated(spec, "Sobol sampling")
    rng = random.Random(seed)
    ids = _parameter_ids(spec)
    k = len(ids)
    matrix_a = [{pid: rng.random() for pid in ids} for _ in range(base_samples)]
    matrix_b = [{pid: rng.random() for pid in ids} for _ in range(base_samples)]

    points = []
    labels = {}
    sample_id = 0
    for row in range(base_samples):
        points.append((sample_id, dict(matrix_a[row])))
        labels[str(sample_id)] = {"kind": "sobol", "matrix": "A", "row": row}
        sample_id += 1
    for row in range(base_samples):
        points.append((sample_id, dict(matrix_b[row])))
        labels[str(sample_id)] = {"kind": "sobol", "matrix": "B", "row": row}
        sample_id += 1
    for parameter_id in ids:
        for row in range(base_samples):
            # AB_i is A with column i taken from B -- the orientation the
            # Saltelli/Jansen estimators below are written for. Building B with
            # a column from A instead silently swaps the roles of f(A) and
            # f(B) in both formulas and yields indices that do not even
            # reproduce a known analytic case.
            combined = dict(matrix_a[row])
            combined[parameter_id] = matrix_b[row][parameter_id]
            points.append((sample_id, combined))
            labels[str(sample_id)] = {
                "kind": "sobol",
                "matrix": "AB",
                "parameter": parameter_id,
                "row": row,
            }
            sample_id += 1
    metadata = {
        "design": "sobol",
        "base_samples": base_samples,
        "parameters": ids,
        "samples": len(points),
        "total_configurations": base_samples * (k + 2),
        "honours_correlation_groups": False,
        "estimators": "Saltelli first-order, Jansen total-effect",
        "points": labels,
    }
    return points, metadata


# --------------------------------------------------------------------------
# Analysis of a completed study
# --------------------------------------------------------------------------


def _response_map(document: dict, estimand: str, strategy: str) -> dict:
    return {
        entry["sample_id"]: entry["mean"]
        for entry in document.get("responses", [])
        if entry["estimand"] == estimand
        and entry["strategy"] == strategy
        and entry["mean"] is not None
    }


def one_at_a_time_effects(document: dict, metadata: dict, estimand: str, strategy: str) -> dict:
    """Per-parameter low/base/high response and the swing between them."""
    responses = _response_map(document, estimand, strategy)
    labels = metadata.get("points", {})
    base_id = next((int(sid) for sid, label in labels.items() if label.get("kind") == "base"), None)
    base_value = responses.get(base_id)
    rows = {}
    for sid, label in labels.items():
        if label.get("kind") != "oat":
            continue
        value = responses.get(int(sid))
        if value is None:
            continue
        rows.setdefault(label["parameter"], {})[label["level"]] = value
    effects = []
    for parameter_id, levels in rows.items():
        low = levels.get("low")
        high = levels.get("high")
        swing = None if low is None or high is None else high - low
        effects.append(
            {
                "parameter": parameter_id,
                "low": low,
                "base": base_value,
                "high": high,
                "swing": swing,
                "relative_swing": (
                    swing / abs(base_value)
                    if swing is not None and base_value not in (None, 0)
                    else None
                ),
            }
        )
    effects.sort(key=lambda row: abs(row["swing"] or 0.0), reverse=True)
    return {
        "method": "one_at_a_time",
        "estimand": estimand,
        "strategy": strategy,
        "base_response": base_value,
        "effects": effects,
        "caveat": metadata.get("caveat"),
    }


def morris_indices(document: dict, metadata: dict, estimand: str, strategy: str) -> dict:
    """Elementary-effect statistics: mu, mu* and sigma per parameter.

    `mu_star` (mean absolute effect) ranks influence without effects of
    opposite sign cancelling; `sigma` large relative to `mu_star` is the
    signature of a parameter whose effect depends on the others, i.e. an
    interaction that a one-at-a-time cross would have missed entirely.
    """
    responses = _response_map(document, estimand, strategy)
    labels = metadata.get("points", {})
    by_trajectory = {}
    for sid, label in labels.items():
        if label.get("kind") != "morris":
            continue
        by_trajectory.setdefault(label["trajectory"], []).append((int(sid), label))
    effects = {}
    for steps in by_trajectory.values():
        steps.sort(key=lambda item: item[1]["step"])
        for (previous_id, _), (current_id, label) in zip(steps, steps[1:], strict=False):
            if previous_id not in responses or current_id not in responses:
                continue
            delta = label.get("delta") or metadata.get("delta")
            if not delta:
                continue
            effect = (responses[current_id] - responses[previous_id]) / delta
            effects.setdefault(label["changed"], []).append(effect)
    rows = []
    for parameter_id, values in effects.items():
        rows.append(
            {
                "parameter": parameter_id,
                "mu": statistics.fmean(values),
                "mu_star": statistics.fmean(abs(v) for v in values),
                "sigma": statistics.stdev(values) if len(values) > 1 else None,
                "elementary_effects": len(values),
            }
        )
    rows.sort(key=lambda row: row["mu_star"], reverse=True)
    return {
        "method": "morris",
        "estimand": estimand,
        "strategy": strategy,
        "indices": rows,
        "note": (
            "mu* ranks influence; sigma large relative to mu* indicates the "
            "parameter's effect depends on the others (interaction or nonlinearity)."
        ),
    }


def sobol_indices(document: dict, metadata: dict, estimand: str, strategy: str) -> dict:
    """First-order (Saltelli) and total-effect (Jansen) Sobol indices."""
    responses = _response_map(document, estimand, strategy)
    labels = metadata.get("points", {})
    base_samples = metadata.get("base_samples", 0)
    ids = metadata.get("parameters", [])

    a_values = [None] * base_samples
    b_values = [None] * base_samples
    ab_values = {pid: [None] * base_samples for pid in ids}
    for sid, label in labels.items():
        value = responses.get(int(sid))
        if value is None:
            continue
        if label["matrix"] == "A":
            a_values[label["row"]] = value
        elif label["matrix"] == "B":
            b_values[label["row"]] = value
        else:
            ab_values[label["parameter"]][label["row"]] = value

    usable = [
        row
        for row in range(base_samples)
        if a_values[row] is not None
        and b_values[row] is not None
        and all(ab_values[pid][row] is not None for pid in ids)
    ]
    if len(usable) < 2:
        return {
            "method": "sobol",
            "estimand": estimand,
            "strategy": strategy,
            "indices": [],
            "note": "not enough valid configuration samples to estimate indices",
        }

    a = [a_values[row] for row in usable]
    b = [b_values[row] for row in usable]
    variance = statistics.variance(a + b)
    rows = []
    for parameter_id in ids:
        ab = [ab_values[parameter_id][row] for row in usable]
        if variance <= 0:
            first_order = total = None
        else:
            first_order = (
                statistics.fmean(
                    b_i * (ab_i - a_i) for a_i, b_i, ab_i in zip(a, b, ab, strict=True)
                )
                / variance
            )
            total = statistics.fmean((a_i - ab_i) ** 2 for a_i, ab_i in zip(a, ab, strict=True)) / (
                2.0 * variance
            )
        rows.append(
            {
                "parameter": parameter_id,
                "first_order": first_order,
                "total_effect": total,
                "interaction_share": (
                    total - first_order if first_order is not None and total is not None else None
                ),
            }
        )
    rows.sort(key=lambda row: row["total_effect"] or 0.0, reverse=True)
    return {
        "method": "sobol",
        "estimand": estimand,
        "strategy": strategy,
        "output_variance": variance,
        "usable_rows": len(usable),
        "indices": rows,
        "note": (
            "First-order = share of output variance explained by this parameter alone; "
            "total effect additionally includes every interaction it takes part in. "
            "Estimates are noisy at small base_samples -- an index slightly below zero "
            "is sampling noise, not a negative contribution."
        ),
    }


def analyze(document: dict, metadata: dict, estimand: str, strategy: str) -> dict:
    """Dispatch to whichever analysis the design supports."""
    design = metadata.get("design")
    if design == "one_at_a_time":
        return one_at_a_time_effects(document, metadata, estimand, strategy)
    if design == "morris":
        return morris_indices(document, metadata, estimand, strategy)
    if design == "sobol":
        return sobol_indices(document, metadata, estimand, strategy)
    responses = _response_map(document, estimand, strategy)
    values = list(responses.values())
    labels = metadata.get("points", {})
    return {
        "method": design,
        "estimand": estimand,
        "strategy": strategy,
        "samples": len(values),
        "response_mean": statistics.fmean(values) if values else None,
        "response_stdev": statistics.stdev(values) if len(values) > 1 else None,
        "response_min": min(values) if values else None,
        "response_max": max(values) if values else None,
        "scenarios": (
            {
                labels[str(sid)].get("name", str(sid)): value
                for sid, value in sorted(responses.items())
                if str(sid) in labels
            }
            if design == "scenarios"
            else None
        ),
    }


def design_cost(spec, design: str, samples: int = 0, trajectories: int = 0) -> int:
    """Configurations a design will require -- for the CLI's pre-run warning."""
    k = len(_parameter_ids(spec))
    if design == "one_at_a_time":
        return 2 * k + 1
    if design == "morris":
        return trajectories * (k + 1)
    if design == "sobol":
        return samples * (k + 2)
    return samples
