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
    # first_upgrade_rate is unrounded (it feeds a warning threshold
    # comparison in metrics/warnings.py -- see #28); round only for display.
    assert round(stats["first_upgrade_rate"], 2) == 66.67
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

    main.cmd_batch(
        SimpleNamespace(runs=1, seed=None, workers=1, days=None, start_money=None, progress=False)
    )
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


# --- runaway-economy threshold scales with run length ---------------------


def test_runaway_multiple_scales_with_run_length():
    """A fixed multiple of start_money is a different test at every horizon.

    A reinvesting farm compounds, so a flat 20x meant $1,200 on a $60 start
    whether the run was 30 days or 365 -- at 365 every surviving strategy
    cleared it, and it even flagged a strategy that went bankrupt in 63.5%
    of its runs as a "runaway economy".
    """
    thresholds = main.evaluate_warnings.__globals__["DEFAULT_THRESHOLDS"]
    from metrics.warnings import runaway_money_multiple

    reference = thresholds["runaway_reference_days"]
    assert (
        runaway_money_multiple({"days": reference}, thresholds)
        == (thresholds["runaway_money_multiple"])
    )

    longer = runaway_money_multiple({"days": reference * 10}, thresholds)
    assert longer == thresholds["runaway_money_multiple"] * 10


def test_runaway_warning_respects_the_scaled_multiple():
    stats = aggregate([_make_run_result(final_money=5000.0) for _ in range(3)])

    short_run = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})
    long_run = main.evaluate_warnings(stats, {"days": 365, "start_money": 60})

    # 5000 is 83x a $60 start: runaway over 30 days, unremarkable over 365.
    assert any("runaway economy" in w for w in short_run)
    assert not any("runaway economy" in w for w in long_run)


def test_runaway_warning_still_fires_for_a_genuine_outlier():
    stats = aggregate([_make_run_result(final_money=500_000.0) for _ in range(3)])
    warnings = main.evaluate_warnings(stats, {"days": 365, "start_money": 60})
    assert any("runaway economy" in w for w in warnings)


# --- #31: partial threshold overrides merge with DEFAULT_THRESHOLDS -------


def test_partial_threshold_override_merges_with_defaults():
    from metrics.warnings import DEFAULT_THRESHOLDS

    # 30% bankruptcy: above the default 20% threshold, below a raised 50%.
    results = [_make_run_result(bankrupt=True) for _ in range(3)] + [
        _make_run_result(bankrupt=False) for _ in range(7)
    ]
    stats = aggregate(results)
    config = {"days": 30, "start_money": 60}

    at_default = main.evaluate_warnings(stats, config)
    assert any("High bankruptcy rate" in w for w in at_default)

    # A partial override used to replace DEFAULT_THRESHOLDS wholesale, so any
    # key it didn't mention (here, upgrade/crop/runaway thresholds) would
    # KeyError the moment evaluate_warnings tried to read them.
    overridden = main.evaluate_warnings(stats, config, {"high_bankruptcy_pct": 50})
    assert not any("High bankruptcy rate" in w for w in overridden)
    assert DEFAULT_THRESHOLDS["high_bankruptcy_pct"] == 20  # default mapping untouched


def test_full_threshold_override_still_works():
    stats = aggregate([_make_run_result(bankrupt=True) for _ in range(10)])
    config = {"days": 30, "start_money": 60}

    warnings = main.evaluate_warnings(stats, config, {"high_bankruptcy_pct": 5})

    assert any("High bankruptcy rate" in w for w in warnings)


def test_empty_threshold_override_uses_defaults():
    stats = aggregate([_make_run_result(bankrupt=True) for _ in range(10)])
    config = {"days": 30, "start_money": 60}

    with_empty = main.evaluate_warnings(stats, config, {})
    with_none = main.evaluate_warnings(stats, config, None)

    assert with_empty == with_none


def test_unknown_threshold_key_is_rejected():
    stats = aggregate([_make_run_result() for _ in range(3)])
    with pytest.raises(ValueError, match="typo_pct"):
        main.evaluate_warnings(stats, {"days": 30, "start_money": 60}, {"typo_pct": 50})


def test_non_numeric_threshold_value_is_rejected():
    stats = aggregate([_make_run_result() for _ in range(3)])
    with pytest.raises(TypeError):
        main.evaluate_warnings(
            stats, {"days": 30, "start_money": 60}, {"high_bankruptcy_pct": "20"}
        )
    with pytest.raises(TypeError):
        main.evaluate_warnings(
            stats, {"days": 30, "start_money": 60}, {"high_bankruptcy_pct": True}
        )


def test_repeated_calls_do_not_accumulate_mutations():
    from metrics.warnings import DEFAULT_THRESHOLDS

    stats = aggregate([_make_run_result() for _ in range(3)])
    config = {"days": 30, "start_money": 60}

    main.evaluate_warnings(stats, config, {"dominant_crop_pct": 1})
    main.evaluate_warnings(stats, config, {"dead_crop_pct": 99})

    assert DEFAULT_THRESHOLDS["dominant_crop_pct"] == 70
    assert DEFAULT_THRESHOLDS["dead_crop_pct"] == 5


# --- #28: warnings compare full precision, not the rounded display value --


def test_bankruptcy_warning_fires_just_above_the_threshold_before_rounding():
    # 4001/20001 = 20.0039998...%, which rounds to display as 20.0% -- a
    # warning gated on the rounded value would have missed this.
    results = [_make_run_result(bankrupt=True) for _ in range(4001)] + [
        _make_run_result(bankrupt=False) for _ in range(20001 - 4001)
    ]
    stats = aggregate(results)

    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})

    assert any("High bankruptcy rate" in w for w in warnings)


def test_bankruptcy_warning_does_not_fire_just_below_the_threshold():
    results = [_make_run_result(bankrupt=True) for _ in range(20)] + [
        _make_run_result(bankrupt=False) for _ in range(80)
    ]
    stats = aggregate(results)

    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})

    assert not any("High bankruptcy rate" in w for w in warnings)


def test_dominant_crop_warning_uses_unrounded_percentage():
    # 701/1000 = 70.1%, rounds to 70.1 either way, but the *comparison*
    # itself must use the unrounded ratio -- pin a case whose true value
    # sits just past the boundary at higher precision than 2 decimals.
    results = [
        _make_run_result(crop_counts={"quickweed": 1}, crops_planted=1) for _ in range(70005)
    ] + [_make_run_result(crop_counts={"greenleaf": 1}, crops_planted=1) for _ in range(29995)]
    stats = aggregate(results)

    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})

    assert any("Dominant crop: 'quickweed'" in w for w in warnings)


# --- #32: undefined per-run ratios aren't aggregated in as zero -----------


def test_crop_loss_rate_averages_only_observed_runs():
    # One run with a real 100% loss, nine with no harvest events at all (so
    # crop_loss_rate is undefined, not 0%). Averaging the undefined runs in
    # as 0.0 would report 10% and suppress the >30% warning entirely.
    results = [_make_run_result(crop_loss_rate=100.0)] + [
        _make_run_result(crop_loss_rate=None) for _ in range(9)
    ]
    stats = aggregate(results)

    assert stats["test"]["avg_crop_loss_rate"] == 100.0
    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})
    assert any("High crop loss rate" in w for w in warnings)


def test_crop_loss_rate_is_none_when_no_run_observed_it():
    stats = aggregate([_make_run_result(crop_loss_rate=None) for _ in range(5)])

    assert stats["test"]["avg_crop_loss_rate"] is None
    # None must not raise when compared against the threshold.
    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})
    assert not any("crop loss" in w for w in warnings)


def test_occupied_watering_rate_averages_only_observed_runs():
    results = [_make_run_result(occupied_watering_rate=80.0)] + [
        _make_run_result(occupied_watering_rate=None) for _ in range(4)
    ]
    stats = aggregate(results)

    assert stats["test"]["avg_occupied_watering_rate"] == 80.0


def test_no_plantings_cohort_gets_a_single_diagnostic_not_dead_crop_spam():
    # Every crop in the catalog would otherwise show up as "0.0% of
    # plantings" -- indistinguishable from every crop actually being dead.
    results = [
        _make_run_result(crop_counts={"quickweed": 0, "greenleaf": 0}, crops_planted=0)
        for _ in range(5)
    ]
    stats = aggregate(results)

    assert stats["test"]["crop_usage_pct"] == {}
    assert stats["test"]["crop_usage_observed"] is False

    warnings = main.evaluate_warnings(stats, {"days": 30, "start_money": 60})
    assert any("No crops were planted" in w for w in warnings)
    assert not any("Dead crop" in w for w in warnings)


def test_mixed_cohort_still_reports_real_crop_usage():
    results = [_make_run_result(crop_counts={"quickweed": 2}, crops_planted=2) for _ in range(3)]
    stats = aggregate(results)

    assert stats["test"]["crop_usage_observed"] is True
    assert stats["test"]["crop_usage_pct"] == {"quickweed": 100.0}
