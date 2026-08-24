"""Tests for runner/adaptive.py.

The behaviours worth pinning are the ones that make an early stop honest:
stopping only at predeclared checkpoints, spending alpha rather than reusing
it, enforcing the minimum and maximum, and reporting a rare-event shortfall as
its own outcome instead of hiding it inside "we ran out of budget".
"""

import math
from types import SimpleNamespace

import pytest

from metrics.inference import InferenceAccumulator
from runner.adaptive import (
    STOP_MAX_RUNS_REACHED,
    STOP_PRECISION_REACHED,
    STOP_RARE_EVENT_UNMET,
    AdaptiveBatch,
    AdaptiveConfig,
    StoppingRule,
    alpha_spent,
    fixed_convergence_document,
    look_confidence,
    summarize_stability,
)
from runner.sampling_plan import IndependentHashedV1


def _result(strategy, value, bankrupt=False, replicate=0):
    return SimpleNamespace(
        strategy=strategy,
        seed=replicate,
        replicate_id=replicate,
        final_money=value,
        bankrupt=bankrupt,
        bankruptcy_day=5 if bankrupt else None,
        avg_profit_per_day=value / 30.0,
        minimum_cash_balance=0.0,
        first_upgrade_day=None,
        crop_loss_rate=None,
    )


class _FakeBatch:
    """Deterministic stand-in for run_batch: value depends only on replicate."""

    def __init__(self, spread=0.0, bankrupt_every=None):
        self.spread = spread
        self.bankrupt_every = bankrupt_every
        self.blocks = []

    def __call__(self, agents, start_replicate, count):
        self.blocks.append((start_replicate, count))
        for replicate in range(start_replicate, start_replicate + count):
            for agent in agents:
                value = 100.0 + self.spread * math.sin(replicate)
                bankrupt = self.bankrupt_every is not None and replicate % self.bankrupt_every == 0
                yield _result(agent.name, value, bankrupt, replicate)


def _agents(*names):
    return [SimpleNamespace(name=name) for name in names]


class _InferenceOnlyAggregator:
    """The slice of BatchAggregator that AdaptiveBatch actually depends on.

    Using this instead of the real aggregator keeps these tests about the
    controller: the full aggregator needs every descriptive RunResult field
    (crop counts, revenue channels, quality mix) that has nothing to do with
    when sampling stops.
    """

    def __init__(self):
        self._by_strategy = {}
        self._counts = {}

    def add(self, result):
        accumulator = self._by_strategy.get(result.strategy)
        if accumulator is None:
            accumulator = InferenceAccumulator()
            self._by_strategy[result.strategy] = accumulator
        accumulator.add(result)
        self._counts[result.strategy] = self._counts.get(result.strategy, 0) + 1

    def inference_accumulators(self):
        return dict(self._by_strategy)

    def counts(self):
        return dict(self._counts)


# --------------------------------------------------------------------------
# Design
# --------------------------------------------------------------------------


def test_checkpoint_schedule_is_declared_up_front_and_ends_at_the_maximum():
    config = AdaptiveConfig(min_runs=500, max_runs=2000, checkpoint_runs=250, rules=[])
    assert config.checkpoint_schedule() == [500, 750, 1000, 1250, 1500, 1750, 2000]


def test_checkpoint_schedule_always_includes_the_maximum_even_when_uneven():
    config = AdaptiveConfig(min_runs=100, max_runs=350, checkpoint_runs=200, rules=[])
    assert config.checkpoint_schedule() == [100, 300, 350]


def test_invalid_designs_are_rejected():
    with pytest.raises(ValueError):
        AdaptiveConfig(min_runs=100, max_runs=50, checkpoint_runs=10, rules=[])
    with pytest.raises(ValueError):
        AdaptiveConfig(min_runs=10, max_runs=20, checkpoint_runs=5, rules=[], mode="either")
    with pytest.raises(ValueError):
        AdaptiveConfig(
            min_runs=10, max_runs=20, checkpoint_runs=5, rules=[], alpha_spending="bonferroni"
        )


def test_stopping_rule_needs_a_target():
    with pytest.raises(ValueError, match="absolute or relative target"):
        StoppingRule(estimand="expected_final_money")


def test_stopping_rule_rejects_an_estimand_without_a_streaming_interval():
    with pytest.raises(ValueError, match="does not support adaptive"):
        StoppingRule(estimand="final_money_quantile", target_half_width=1.0)


# --------------------------------------------------------------------------
# Alpha spending
# --------------------------------------------------------------------------


def test_alpha_spending_functions_reach_the_full_budget_at_the_end():
    for method in ("obrien_fleming", "pocock"):
        assert alpha_spent(method, 1.0, 0.05) == pytest.approx(0.05, abs=1e-9)
        assert alpha_spent(method, 0.0, 0.05) == 0.0


def test_obrien_fleming_spends_less_early_than_pocock():
    assert alpha_spent("obrien_fleming", 0.25, 0.05) < alpha_spent("pocock", 0.25, 0.05)


def test_look_confidences_are_wider_early_and_sum_to_the_declared_alpha():
    config = AdaptiveConfig(
        min_runs=100, max_runs=500, checkpoint_runs=100, rules=[], confidence=0.95
    )
    levels = [look_confidence(config, i) for i in range(len(config.checkpoint_schedule()))]
    assert levels[0] > levels[-1], "an early look must clear a higher bar"
    assert sum(1 - level for level in levels) == pytest.approx(0.05, abs=1e-6)


def test_alpha_spending_none_uses_the_nominal_level_at_every_look():
    config = AdaptiveConfig(
        min_runs=10, max_runs=30, checkpoint_runs=10, rules=[], alpha_spending="none"
    )
    assert [look_confidence(config, i) for i in range(3)] == [0.95, 0.95, 0.95]


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _drive(config, run_block, agents=None, estimands=None):
    agents = agents or _agents("a")
    aggregator = _InferenceOnlyAggregator()
    driver = AdaptiveBatch(
        config,
        aggregator,
        run_block,
        agents,
        IndependentHashedV1(1),
        estimand_ids=estimands,
    )
    results = list(driver.stream())
    return driver, aggregator, results


def test_stops_at_a_declared_checkpoint_once_the_target_is_met():
    config = AdaptiveConfig(
        min_runs=20,
        max_runs=200,
        checkpoint_runs=20,
        rules=[StoppingRule("expected_final_money", target_half_width=5.0)],
    )
    driver, _, results = _drive(config, _FakeBatch(spread=1.0))
    assert driver.stop_reason == STOP_PRECISION_REACHED
    assert driver.realized_runs == 20, "a tight cohort should stop at the first declared look"
    assert len(results) == 20
    assert driver.unmet_criteria == []


def test_never_stops_before_the_minimum():
    config = AdaptiveConfig(
        min_runs=60,
        max_runs=200,
        checkpoint_runs=20,
        rules=[StoppingRule("expected_final_money", target_half_width=1e9)],
    )
    driver, _, results = _drive(config, _FakeBatch(spread=1.0))
    assert driver.realized_runs == 60
    assert len(results) == 60


def test_stops_only_at_declared_checkpoints():
    config = AdaptiveConfig(
        min_runs=25,
        max_runs=100,
        checkpoint_runs=25,
        rules=[StoppingRule("expected_final_money", target_half_width=0.5)],
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=20.0))
    assert driver.realized_runs in config.checkpoint_schedule()
    looks = [cp["runs_per_strategy"] for cp in driver.convergence_document()["checkpoints"]]
    assert all(look in config.checkpoint_schedule() for look in looks)


def test_reaching_the_maximum_records_max_runs_reached_and_what_was_unmet():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=30,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=50.0))
    assert driver.stop_reason == STOP_MAX_RUNS_REACHED
    assert driver.realized_runs == 30
    assert driver.unmet_criteria
    assert driver.unmet_criteria[0]["criterion"] == "expected_final_money"


def test_rare_event_shortfall_is_its_own_stop_reason():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=30,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e9)],
        min_bankruptcies=5,
    )
    # No run ever goes bankrupt, so the rare-event minimum can never be met.
    driver, _, _ = _drive(config, _FakeBatch(spread=1.0))
    assert driver.stop_reason == STOP_RARE_EVENT_UNMET
    assert driver.realized_runs == 30, "it stops at the maximum rather than running forever"
    assert any(entry["criterion"] == "min_bankruptcies" for entry in driver.unmet_criteria)


def test_rare_event_minimum_delays_a_stop_that_precision_alone_would_allow():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=40,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e9)],
        min_bankruptcies=3,
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=1.0, bankrupt_every=8))
    assert driver.stop_reason == STOP_PRECISION_REACHED
    assert driver.realized_runs >= 20


def test_stopping_mode_any_stops_earlier_than_all():
    rules = [
        StoppingRule("expected_final_money", target_half_width=1e9),
        StoppingRule("bankruptcy_probability", target_half_width=1e-9),
    ]
    strict = AdaptiveConfig(min_runs=10, max_runs=30, checkpoint_runs=10, rules=rules, mode="all")
    loose = AdaptiveConfig(min_runs=10, max_runs=30, checkpoint_runs=10, rules=rules, mode="any")
    strict_driver, _, _ = _drive(strict, _FakeBatch(spread=1.0, bankrupt_every=3))
    loose_driver, _, _ = _drive(loose, _FakeBatch(spread=1.0, bankrupt_every=3))
    assert strict_driver.realized_runs == 30
    assert loose_driver.realized_runs == 10


def test_every_strategy_gets_the_same_number_of_runs():
    config = AdaptiveConfig(
        min_runs=20,
        max_runs=60,
        checkpoint_runs=20,
        rules=[StoppingRule("expected_final_money", target_half_width=5.0)],
    )
    driver, aggregator, _ = _drive(config, _FakeBatch(spread=1.0), agents=_agents("a", "b", "c"))
    counts = set(aggregator.counts().values())
    assert len(counts) == 1, "stopping is evaluated only after a complete block"


def test_blocks_are_contiguous_and_never_repeat_a_replicate():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=40,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    fake = _FakeBatch(spread=50.0)
    _drive(config, fake)
    assert fake.blocks == [(0, 10), (10, 10), (20, 10), (30, 10)]


# --------------------------------------------------------------------------
# Convergence record
# --------------------------------------------------------------------------


def test_checkpoint_history_reproduces_the_final_estimate():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=30,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    driver, aggregator, _ = _drive(config, _FakeBatch(spread=10.0))
    document = driver.convergence_document()
    final_checkpoint = document["checkpoints"][-1]
    recorded = final_checkpoint["strategies"]["a"]["estimates"]["expected_final_money"]["estimate"]
    live = aggregator.inference_accumulators()["a"].estimate("expected_final_money").value
    assert recorded == pytest.approx(live, rel=1e-9)


def test_checkpoints_record_movement_between_looks():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=30,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=10.0))
    checkpoints = driver.convergence_document()["checkpoints"]
    assert (
        checkpoints[0]["strategies"]["a"]["estimates"]["expected_final_money"][
            "change_from_previous"
        ]
        is None
    )
    assert (
        checkpoints[1]["strategies"]["a"]["estimates"]["expected_final_money"][
            "change_from_previous"
        ]
        is not None
    )


def test_convergence_document_publishes_the_design_and_the_plan():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=20,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=10.0))
    document = driver.convergence_document()
    assert document["design"]["checkpoint_schedule"] == [10, 20]
    assert document["design"]["alpha_spending"] == "obrien_fleming"
    assert document["sampling_plan"]["plan"] == "independent-hashed-v1"
    assert document["stop_reason"] in (STOP_MAX_RUNS_REACHED, STOP_PRECISION_REACHED)


def test_fixed_batch_document_is_a_single_terminal_checkpoint():
    aggregator = _InferenceOnlyAggregator()
    for replicate in range(10):
        aggregator.add(_result("a", 100.0 + replicate, replicate=replicate))
    document = fixed_convergence_document(
        aggregator, 0.95, ["expected_final_money"], 10, {"plan": "legacy-mt19937-v1"}
    )
    assert len(document["checkpoints"]) == 1
    assert document["design"]["mode"] == "fixed"
    assert document["stop_reason"] == "fixed_sample"
    assert document["checkpoints"][0]["decision"] == "fixed_sample"


def test_summarize_stability_reports_drift_over_the_window():
    config = AdaptiveConfig(
        min_runs=10,
        max_runs=50,
        checkpoint_runs=10,
        rules=[StoppingRule("expected_final_money", target_half_width=1e-9)],
    )
    driver, _, _ = _drive(config, _FakeBatch(spread=5.0))
    stability = summarize_stability(driver.convergence_document())
    assert stability["checkpoints"] == 5
    assert "expected_final_money" in stability["max_relative_drift"]
    assert stability["settled"] in (True, False)
