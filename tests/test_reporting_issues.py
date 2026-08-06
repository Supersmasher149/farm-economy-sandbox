import csv
import json
import os
from types import SimpleNamespace

import pytest

import main
from agents.fast_seller import FastSeller
from metrics.aggregate_results import (
    MEDIAN_RESERVOIR_CAPACITY,
    BatchAggregator,
    aggregate,
)
from metrics.report import generate_markdown_report
from metrics.run_results import build_run_result, write_csv
from runner import batch_run
from runner.batch_run import _execute, run_batch
from runner.single_run import run_single
from tests.test_aggregate_results import _make_run_result
from tests.test_engine import FERTILIZER_CONFIG, WATERING_SETTINGS, make_crops, make_upgrades


def test_upgrade_reach_counts_and_rates_are_aggregated():
    results = [
        _make_run_result(first_upgrade_day=2, second_upgrade_day=5),
        _make_run_result(first_upgrade_day=3),
        _make_run_result(),
    ]

    stats = aggregate(results)["test"]

    assert stats["first_upgrade_count"] == 2
    assert stats["second_upgrade_count"] == 1
    assert stats["first_upgrade_rate"] == 66.67
    assert stats["second_upgrade_rate"] == 33.33


def test_upgrade_slow_warning_uses_reach_rate():
    stats = aggregate([_make_run_result() for _ in range(10)])
    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})

    assert any("0.0% of runs purchased one" in warning for warning in warnings)


def test_omitted_base_seed_is_resolved_to_a_fresh_32_bit_value(monkeypatch):
    class StubSystemRandom:
        def randrange(self, stop):
            assert stop == 2**32
            return 0x12345678

    monkeypatch.setattr(batch_run.random, "SystemRandom", StubSystemRandom)

    assert batch_run.resolve_base_seed(None) == 0x12345678
    assert batch_run.resolve_base_seed(7) == 7


def test_median_reservoir_is_bounded_and_reported_as_approximate():
    aggregator = BatchAggregator()
    for value in range(MEDIAN_RESERVOIR_CAPACITY + 100):
        aggregator.add(_make_run_result(final_money=value, seed=value))

    accumulator = aggregator._by_strategy["test"]
    assert len(accumulator.all_money_values.values) == MEDIAN_RESERVOIR_CAPACITY
    summary = aggregator.finalize()
    assert summary["test"]["median_approximate"] is True
    assert summary["test"]["median_reservoir_capacity"] == MEDIAN_RESERVOIR_CAPACITY

    report = generate_markdown_report(
        {"days": 10, "start_money": 60, "start_slots": 1},
        MEDIAN_RESERVOIR_CAPACITY + 100,
        summary,
        [],
        {},
        base_seed=42,
    )
    assert "approximate medians" in report


def test_serialized_money_reconciles_from_rounded_components(tmp_path):
    crops, upgrades = make_crops(), make_upgrades()
    player, seed, _ = run_single(
        {"start_money": 100, "start_slots": 1, "days": 1},
        FastSeller(),
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        seed=1,
    )
    player.revenue_by_channel = {"spot": 0.105, "contract": 0.105}
    player.expenses_by_category = {"seeds": 0.105, "contract_penalties": 0.105}
    result = build_run_result(player, "test", seed, player.day, crops, upgrades)

    assert result.revenue_by_channel == {"spot": 0.11, "contract": 0.11}
    assert result.expenses_by_category == {"seeds": 0.11, "contract_penalties": 0.11}
    assert result.total_revenue == 0.22
    assert result.total_expenses == result.total_costs == 0.22
    assert result.net_profit == result.net_cash_change == 0.0
    assert result.gross_profit == 0.11
    assert result.operating_profit == 0.0

    csv_path = tmp_path / "serialized-money.csv"
    write_csv([result], str(csv_path), [crop["id"] for crop in crops])
    with open(csv_path, newline="") as csv_file:
        row = next(csv.DictReader(csv_file))
    assert float(row["total_revenue"]) == sum(json.loads(row["revenue_by_channel"]).values())
    assert float(row["total_expenses"]) == sum(json.loads(row["expenses_by_category"]).values())


def test_batch_snapshot_and_report_include_resolved_seed(tmp_path, monkeypatch, capsys):
    crops, upgrades = make_crops(), make_upgrades()
    config = {"start_money": 60, "start_slots": 1, "days": 1}
    world = {
        "watering": WATERING_SETTINGS,
        "fertilizer": FERTILIZER_CONFIG,
        "markets": {"channels": [{"id": "spot", "price_multiplier": 1.0}]},
    }

    class TestAgent:
        name = "test"
        description = "test strategy"

    monkeypatch.setattr(main, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(main, "AGENT_REGISTRY", {"test": TestAgent})
    monkeypatch.setattr(main, "load_config", lambda: (crops, upgrades, config, world))
    monkeypatch.setattr(
        main,
        "resolve_base_seed",
        lambda seed: 0x12345678 if seed is None else seed,
    )
    monkeypatch.setattr(
        main,
        "run_batch",
        lambda *args, **kwargs: iter([_make_run_result(strategy="test")]),
    )

    main.cmd_batch(SimpleNamespace(runs=1, seed=None, workers=1, days=None, start_money=None))
    capsys.readouterr()

    with open(tmp_path / "config_snapshot.json") as snapshot_file:
        snapshot = json.load(snapshot_file)
    assert snapshot["base_seed"] == 0x12345678
    assert "Base seed: **305419896**" in (tmp_path / "summary_report.md").read_text()


def test_report_publication_rolls_back_if_replacement_fails(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    reports = tmp_path / "reports"
    staging.mkdir()
    reports.mkdir()
    names = ("run_results.csv", "config_snapshot.json", "summary_report.md")
    for name in names:
        (staging / name).write_text(f"new {name}")
        (reports / name).write_text(f"old {name}")

    original_replace = os.replace
    failed = False

    def fail_config_replacement(source, destination):
        nonlocal failed
        if os.path.basename(destination) == "config_snapshot.json" and not failed:
            failed = True
            raise OSError("injected publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(main.os, "replace", fail_config_replacement)

    with pytest.raises(OSError, match="injected publication failure"):
        main._publish_report_artifacts(str(staging), str(reports))

    for name in names:
        assert (reports / name).read_text() == f"old {name}"


def test_worker_error_includes_strategy_and_seed(monkeypatch):
    def fail_run(*args, **kwargs):
        raise ValueError("broken simulation")

    monkeypatch.setattr(batch_run, "run_single", fail_run)

    with pytest.raises(RuntimeError, match=r"strategy=test, seed=99"):
        _execute(
            SimpleNamespace(name="test"),
            99,
            {},
            [],
            [],
            {},
            {},
            None,
        )


@pytest.mark.parametrize(
    "option, value",
    [
        ("--runs", "0"),
        ("--runs", "-1"),
        ("--workers", "0"),
        ("--days", "0"),
        ("--start-money", "-1"),
    ],
)
def test_batch_parser_rejects_invalid_numeric_arguments(option, value):
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["batch", option, value])


def test_programmatic_batch_rejects_invalid_values_before_iteration():
    with pytest.raises(ValueError, match="num_runs"):
        run_batch(
            {"start_money": 60, "start_slots": 1, "days": 1},
            [],
            [],
            [],
            {},
            {},
            num_runs=0,
            workers=1,
        )

    with pytest.raises(ValueError, match="workers"):
        run_batch(
            {"start_money": 60, "start_slots": 1, "days": 1},
            [],
            [],
            [],
            {},
            {},
            num_runs=1,
            workers=0,
        )

    with pytest.raises(ValueError, match="start_money"):
        run_batch(
            {"start_money": -1, "start_slots": 1, "days": 1},
            [],
            [],
            [],
            {},
            {},
            num_runs=1,
            workers=1,
        )


def test_invalid_batch_values_do_not_create_report_directory(monkeypatch):
    created = False

    def record_makedirs(*_args, **_kwargs):
        nonlocal created
        created = True

    monkeypatch.setattr(
        main,
        "load_config",
        lambda: (
            make_crops(),
            make_upgrades(),
            {"start_money": 60, "start_slots": 1, "days": 1},
            {"watering": FERTILIZER_CONFIG, "fertilizer": FERTILIZER_CONFIG},
        ),
    )
    monkeypatch.setattr(main.os, "makedirs", record_makedirs)

    with pytest.raises(ValueError, match="num_runs"):
        main.cmd_batch(
            SimpleNamespace(
                runs=0,
                seed=1,
                workers=1,
                days=None,
                start_money=None,
            )
        )

    assert not created
