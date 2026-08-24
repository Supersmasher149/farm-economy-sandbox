"""Tests for experiments/uncertainty.py and experiments/sensitivity.py.

Two failures would make an uncertainty study actively misleading rather than
merely imprecise, so both are pinned here: silently sampling correlated
parameters independently, and silently accepting a sampled configuration the
simulator itself would reject.
"""

import math
import random
import statistics
from types import SimpleNamespace

import pytest

from experiments import sensitivity
from experiments.uncertainty import (
    UNCERTAINTY_SCHEMA,
    UncertaintySpecError,
    apply_values,
    build_distribution,
    correlated_uniforms,
    decompose_variance,
    parse_spec,
    read_path,
    run_study,
    sample_configuration,
    summarize_runs,
    values_from_uniforms,
    write_path,
)


def _spec(parameters, **document):
    return parse_spec({"schema": UNCERTAINTY_SCHEMA, "parameters": parameters, **document})


def _uniform_parameter(pid="p", path="x.p", low=0.0, high=1.0, **extra):
    return {
        "id": pid,
        "path": path,
        "distribution": "uniform",
        "parameters": {"low": low, "high": high},
        **extra,
    }


BUNDLE = {
    "crops": [
        {"id": "quickweed", "loss_chance": 0.19, "base_price": 6},
        {"id": "greenleaf", "loss_chance": 0.26, "base_price": 6},
    ],
    "simulation_settings": {"start_money": 33, "days": 30},
}


# --------------------------------------------------------------------------
# Distributions
# --------------------------------------------------------------------------


def test_distributions_invert_their_own_cdf_at_the_median():
    assert build_distribution("uniform", {"low": 2, "high": 4}).ppf(0.5) == pytest.approx(3.0)
    assert build_distribution("normal", {"mean": 5, "sd": 2}).ppf(0.5) == pytest.approx(5.0)
    assert build_distribution("beta", {"alpha": 2, "beta": 2}).ppf(0.5) == pytest.approx(
        0.5, abs=1e-6
    )
    assert build_distribution("triangular", {"low": 0, "mode": 0, "high": 1}).ppf(
        0.75
    ) == pytest.approx(0.5)
    assert build_distribution("constant", {"value": 7}).ppf(0.13) == 7


def test_beta_mean_is_recovered_by_sampling():
    distribution = build_distribution("beta", {"alpha": 19, "beta": 81})
    rng = random.Random(0)
    draws = [distribution.sample(rng) for _ in range(4000)]
    assert statistics.fmean(draws) == pytest.approx(19 / 100, abs=0.01)


def test_discrete_distribution_respects_weights():
    distribution = build_distribution("discrete", {"values": [1, 2], "weights": [3, 1]})
    assert distribution.ppf(0.1) == 1
    assert distribution.ppf(0.9) == 2


def test_malformed_distributions_are_rejected():
    with pytest.raises(UncertaintySpecError, match="unknown distribution"):
        build_distribution("cauchy", {})
    with pytest.raises(UncertaintySpecError, match="missing parameter"):
        build_distribution("normal", {"mean": 1})
    with pytest.raises(UncertaintySpecError, match="sd > 0"):
        build_distribution("normal", {"mean": 1, "sd": 0})
    with pytest.raises(UncertaintySpecError, match="high > low"):
        build_distribution("uniform", {"low": 5, "high": 1})


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def test_id_selector_addresses_the_intended_entry():
    assert read_path(BUNDLE, "crops[id=greenleaf].loss_chance") == 0.26
    assert read_path(BUNDLE, "simulation_settings.start_money") == 33


def test_index_selector_is_supported_but_id_selection_is_stable():
    assert read_path(BUNDLE, "crops[0].loss_chance") == 0.19
    reordered = {"crops": list(reversed(BUNDLE["crops"])), "simulation_settings": {}}
    assert read_path(reordered, "crops[id=greenleaf].loss_chance") == 0.26
    assert read_path(reordered, "crops[0].loss_chance") == 0.26, "an index silently retargets"


def test_ambiguous_or_missing_paths_fail_loudly():
    with pytest.raises(UncertaintySpecError, match="no such key"):
        read_path(BUNDLE, "crops[id=quickweed].nonexistent")
    with pytest.raises(UncertaintySpecError, match="matched 0 entries"):
        read_path(BUNDLE, "crops[id=nope].loss_chance")
    with pytest.raises(UncertaintySpecError, match="malformed"):
        read_path(BUNDLE, "crops[[.loss_chance")


def test_write_path_only_targets_scalars():
    bundle = {"crops": [{"id": "a", "loss_chance": 0.1}]}
    write_path(bundle, "crops[id=a].loss_chance", 0.5)
    assert bundle["crops"][0]["loss_chance"] == 0.5
    with pytest.raises(UncertaintySpecError, match="does not address a scalar"):
        write_path(bundle, "crops[id=a]", 1.0)


# --------------------------------------------------------------------------
# Specification
# --------------------------------------------------------------------------


def test_spec_requires_its_schema_and_parameters():
    with pytest.raises(UncertaintySpecError, match="schema"):
        parse_spec({"parameters": [_uniform_parameter()]})
    with pytest.raises(UncertaintySpecError, match="non-empty"):
        parse_spec({"schema": UNCERTAINTY_SCHEMA, "parameters": []})


def test_duplicate_parameter_ids_are_rejected():
    with pytest.raises(UncertaintySpecError, match="duplicate"):
        _spec([_uniform_parameter("p", "x.a"), _uniform_parameter("p", "x.b")])


def test_undeclared_correlation_group_is_an_error_not_independent_sampling():
    with pytest.raises(UncertaintySpecError, match="not declared"):
        _spec([_uniform_parameter(correlation_group="weather")])


def test_correlation_group_needs_a_valid_correlation():
    with pytest.raises(UncertaintySpecError, match="correlation"):
        _spec(
            [_uniform_parameter(correlation_group="weather")],
            correlation_groups={"weather": {"correlation": 1.0}},
        )


def test_unknown_classification_and_invalid_policy_are_rejected():
    with pytest.raises(UncertaintySpecError, match="classification"):
        _spec([_uniform_parameter(classification="guessed")])
    with pytest.raises(UncertaintySpecError, match="on_invalid"):
        _spec([_uniform_parameter()], on_invalid="clamp")


def test_describe_publishes_provenance_and_constraints():
    spec = _spec(
        [
            _uniform_parameter(
                source="field-study-2026",
                unit="probability",
                constraints={"min": 0.0, "max": 1.0},
            )
        ]
    )
    described = spec.describe()["parameters"][0]
    assert described["source"] == "field-study-2026"
    assert described["constraints"]["max"] == 1.0
    assert described["classification"] == "epistemic"


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def test_correlated_group_members_move_together():
    spec = _spec(
        [
            _uniform_parameter("a", "x.a", correlation_group="g"),
            _uniform_parameter("b", "x.b", correlation_group="g"),
            _uniform_parameter("c", "x.c"),
        ],
        correlation_groups={"g": {"correlation": 0.8}},
    )
    rng = random.Random(1)
    draws = [correlated_uniforms(spec, rng) for _ in range(2000)]
    grouped = statistics.correlation([d["a"] for d in draws], [d["b"] for d in draws])
    ungrouped = statistics.correlation([d["a"] for d in draws], [d["c"] for d in draws])
    assert grouped > 0.6, "declared correlation must actually appear in the samples"
    assert abs(ungrouped) < 0.15


def test_marginals_are_preserved_under_correlation():
    spec = _spec(
        [
            _uniform_parameter("a", "x.a", low=10, high=20, correlation_group="g"),
            _uniform_parameter("b", "x.b", low=0, high=1, correlation_group="g"),
        ],
        correlation_groups={"g": {"correlation": 0.9}},
    )
    rng = random.Random(2)
    values = [values_from_uniforms(spec, correlated_uniforms(spec, rng)) for _ in range(3000)]
    a_values = [v["a"] for v in values]
    assert statistics.fmean(a_values) == pytest.approx(15.0, abs=0.3)
    assert min(a_values) >= 10 and max(a_values) <= 20


def test_apply_values_deep_copies_the_baseline():
    spec = _spec([_uniform_parameter("loss", "crops[id=quickweed].loss_chance", 0.0, 1.0)])
    sampled = apply_values(BUNDLE, spec, {"loss": 0.42})
    assert sampled["crops"][0]["loss_chance"] == 0.42
    assert BUNDLE["crops"][0]["loss_chance"] == 0.19, "the baseline must never be mutated"


def test_transform_and_integer_constraint_are_applied():
    spec = _spec(
        [
            _uniform_parameter(
                "money",
                "simulation_settings.start_money",
                20,
                60,
                constraints={"integer": True},
            )
        ]
    )
    values = values_from_uniforms(spec, {"money": 0.5})
    assert values["money"] == 40 and isinstance(values["money"], int)


def test_sample_resamples_until_the_validator_accepts():
    spec = _spec(
        [_uniform_parameter("loss", "crops[id=quickweed].loss_chance", 0.0, 1.0)],
        on_invalid="resample",
    )

    def validator(bundle):
        if bundle["crops"][0]["loss_chance"] > 0.2:
            raise ValueError("loss_chance too high for this economy")

    sample = sample_configuration(BUNDLE, spec, random.Random(3), 0, validator=validator)
    assert sample.valid is True
    assert sample.values["loss"] <= 0.2
    assert sample.attempts >= 1


def test_reject_policy_records_the_failure_instead_of_resampling():
    spec = _spec(
        [_uniform_parameter("loss", "crops[id=quickweed].loss_chance", 0.9, 1.0)],
        on_invalid="reject",
    )

    def validator(bundle):
        raise ValueError("always invalid")

    sample = sample_configuration(BUNDLE, spec, random.Random(4), 7, validator=validator)
    assert sample.valid is False
    assert sample.attempts == 1
    assert "failed configuration validation" in sample.rejection_reason
    assert sample.record()["sample_id"] == 7


def test_constraint_violations_are_reported_not_clamped():
    spec = _spec(
        [
            _uniform_parameter(
                "loss",
                "crops[id=quickweed].loss_chance",
                0.5,
                1.0,
                constraints={"max": 0.2},
            )
        ],
        on_invalid="reject",
    )
    sample = sample_configuration(BUNDLE, spec, random.Random(5), 0)
    assert sample.valid is False
    assert "outside declared constraints" in sample.rejection_reason


# --------------------------------------------------------------------------
# Nested execution and variance decomposition
# --------------------------------------------------------------------------


def _run_result(strategy, value, bankrupt=False):
    return SimpleNamespace(
        strategy=strategy,
        final_money=value,
        bankrupt=bankrupt,
        avg_profit_per_day=value / 30.0,
        bankruptcy_day=None,
        minimum_cash_balance=0.0,
        first_upgrade_day=None,
        crop_loss_rate=None,
    )


def test_summarize_runs_reports_the_aleatory_spread_per_cell():
    results = [_run_result("a", value) for value in (10.0, 20.0, 30.0)]
    responses = summarize_runs(results, 0, ["expected_final_money"])
    assert len(responses) == 1
    assert responses[0].mean == pytest.approx(20.0)
    assert responses[0].stdev == pytest.approx(statistics.stdev([10.0, 20.0, 30.0]))
    assert responses[0].n == 3


def test_variance_decomposition_separates_epistemic_from_aleatory():
    responses = [
        SimpleNamespace(
            sample_id=index,
            strategy="a",
            estimand="expected_final_money",
            mean=mean,
            stdev=2.0,
        )
        for index, mean in enumerate([10.0, 20.0, 30.0, 40.0])
    ]
    decomposition = decompose_variance(responses, "expected_final_money", "a")
    assert decomposition["epistemic_variance"] == pytest.approx(
        statistics.variance([10.0, 20.0, 30.0, 40.0])
    )
    assert decomposition["aleatory_variance"] == pytest.approx(4.0)
    assert decomposition["epistemic_share"] == pytest.approx(
        decomposition["epistemic_variance"] / decomposition["total_variance"]
    )


def test_run_study_publishes_the_parameter_matrix_and_rejections():
    spec = _spec([_uniform_parameter("loss", "crops[id=quickweed].loss_chance", 0.0, 0.5)])
    design, metadata = sensitivity.one_at_a_time_design(spec)

    def simulate(bundle, sample_id):
        loss = bundle["crops"][0]["loss_chance"]
        return [_run_result("a", 100.0 * (1 - loss)) for _ in range(3)]

    study = run_study(BUNDLE, spec, simulate, design, estimand_ids=["expected_final_money"])
    assert study["valid_samples"] == len(design)
    assert study["rejected_samples"] == 0
    assert len(study["parameter_matrix"]) == len(design)
    assert all("values" in row for row in study["parameter_matrix"])
    assert study["specification"]["schema"] == UNCERTAINTY_SCHEMA


# --------------------------------------------------------------------------
# Designs
# --------------------------------------------------------------------------


def test_design_sizes_match_their_definitions():
    spec = _spec([_uniform_parameter(f"p{i}", f"x.p{i}") for i in range(3)])
    assert len(sensitivity.one_at_a_time_design(spec)[0]) == 2 * 3 + 1
    assert len(sensitivity.morris_design(spec, trajectories=4)[0]) == 4 * (3 + 1)
    assert len(sensitivity.sobol_design(spec, base_samples=8)[0]) == 8 * (3 + 2)
    assert len(sensitivity.latin_hypercube_design(spec, 12)[0]) == 12
    assert sensitivity.design_cost(spec, "sobol", samples=8) == 8 * 5


def test_stratifying_designs_refuse_a_correlated_spec():
    spec = _spec(
        [_uniform_parameter("a", "x.a", correlation_group="g")],
        correlation_groups={"g": {"correlation": 0.5}},
    )
    for builder in (
        lambda: sensitivity.latin_hypercube_design(spec, 4),
        lambda: sensitivity.morris_design(spec, trajectories=2),
        lambda: sensitivity.sobol_design(spec, base_samples=4),
    ):
        with pytest.raises(UncertaintySpecError, match="correlation group"):
            builder()
    # Monte Carlo is the design that honours the copula, so it must not refuse.
    points, metadata = sensitivity.monte_carlo_design(spec, 4)
    assert metadata["honours_correlation_groups"] is True


def test_latin_hypercube_covers_every_stratum_once():
    spec = _spec([_uniform_parameter("a", "x.a")])
    points, _ = sensitivity.latin_hypercube_design(spec, 10, seed=1)
    strata = sorted(int(uniforms["a"] * 10) for _, uniforms in points)
    assert strata == list(range(10))


def test_one_at_a_time_holds_every_other_parameter_at_its_median():
    spec = _spec([_uniform_parameter("a", "x.a"), _uniform_parameter("b", "x.b")])
    points, metadata = sensitivity.one_at_a_time_design(spec)
    base = dict(points[0][1])
    assert base == {"a": 0.5, "b": 0.5}
    varied = [uniforms for _, uniforms in points[1:]]
    for uniforms in varied:
        moved = [pid for pid, value in uniforms.items() if value != 0.5]
        assert len(moved) == 1
    assert metadata["caveat"]


def test_scenario_design_rejects_unknown_parameters():
    spec = _spec([_uniform_parameter("a", "x.a")])
    with pytest.raises(UncertaintySpecError, match="unknown parameter"):
        sensitivity.scenario_design(spec, {"bad": {"nope": 0.5}})


# --------------------------------------------------------------------------
# Sensitivity analysis
# --------------------------------------------------------------------------


def _analytic_study(spec, design, function):
    responses = []
    for sample_id, uniforms in design:
        values = values_from_uniforms(spec, uniforms)
        responses.append(
            {
                "sample_id": sample_id,
                "strategy": "a",
                "estimand": "expected_final_money",
                "mean": function(values),
            }
        )
    return {"responses": responses}


def test_one_at_a_time_ranks_the_parameter_with_the_larger_swing_first():
    spec = _spec([_uniform_parameter("a", "x.a", 0, 1), _uniform_parameter("b", "x.b", 0, 1)])
    design, metadata = sensitivity.one_at_a_time_design(spec)
    study = _analytic_study(spec, design, lambda v: 10 * v["a"] + v["b"])
    effects = sensitivity.one_at_a_time_effects(study, metadata, "expected_final_money", "a")[
        "effects"
    ]
    assert effects[0]["parameter"] == "a"
    assert effects[0]["swing"] > effects[1]["swing"]


def test_morris_ranks_an_influential_parameter_above_an_inert_one():
    spec = _spec([_uniform_parameter("a", "x.a", 0, 1), _uniform_parameter("b", "x.b", 0, 1)])
    design, metadata = sensitivity.morris_design(spec, trajectories=12, seed=2)
    study = _analytic_study(spec, design, lambda v: 5 * v["a"])
    indices = sensitivity.morris_indices(study, metadata, "expected_final_money", "a")["indices"]
    assert indices[0]["parameter"] == "a"
    assert indices[0]["mu_star"] > 1.0
    assert indices[-1]["parameter"] == "b"
    assert indices[-1]["mu_star"] == pytest.approx(0.0, abs=1e-9)


def test_sobol_recovers_the_ishigami_analytic_indices():
    """The standard benchmark: an estimator that is wired up wrong (A and B
    matrices swapped, say) reproduces neither S1 nor ST here."""
    spec = _spec([_uniform_parameter(f"x{i}", f"x.x{i}", -math.pi, math.pi) for i in (1, 2, 3)])
    design, metadata = sensitivity.sobol_design(spec, base_samples=4096, seed=1)
    a, b = 7.0, 0.1
    study = _analytic_study(
        spec,
        design,
        lambda v: (
            math.sin(v["x1"]) + a * math.sin(v["x2"]) ** 2 + b * v["x3"] ** 4 * math.sin(v["x1"])
        ),
    )
    indices = {
        row["parameter"]: row
        for row in sensitivity.sobol_indices(study, metadata, "expected_final_money", "a")[
            "indices"
        ]
    }
    variance = a**2 / 8 + b * math.pi**4 / 5 + b**2 * math.pi**8 / 18 + 0.5
    expected_first = {
        "x1": 0.5 * (1 + b * math.pi**4 / 5) ** 2 / variance,
        "x2": (a**2 / 8) / variance,
        "x3": 0.0,
    }
    expected_total_x3 = (b**2 * math.pi**8 * 8 / 225) / variance
    for name, expected in expected_first.items():
        assert indices[name]["first_order"] == pytest.approx(expected, abs=0.05)
    assert indices["x3"]["total_effect"] == pytest.approx(expected_total_x3, abs=0.05)
    assert indices["x2"]["interaction_share"] == pytest.approx(0.0, abs=0.05)


def test_sobol_reports_no_indices_when_too_few_samples_are_valid():
    spec = _spec([_uniform_parameter("a", "x.a")])
    _, metadata = sensitivity.sobol_design(spec, base_samples=4, seed=1)
    result = sensitivity.sobol_indices({"responses": []}, metadata, "e", "a")
    assert result["indices"] == []
    assert "not enough valid" in result["note"]
