"""Tests for metrics/trends.py -- charting a metric's value across published
run history, one line per strategy.

Mirrors tests/test_visualize.py's `captured_figure` pattern (monkeypatch
`_save` to inspect the Figure instead of a PNG) and reuses
tests/test_view.py's `_write_run` helper for building fake published runs
on disk, since `load_history` is just `view.list_runs`/`view.load_run`
under the hood.
"""

import os

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
import matplotlib.pyplot  # noqa: E402

from metrics import trends  # noqa: E402
from tests.test_view import STRATEGIES, _write_run  # noqa: E402


@pytest.fixture
def captured_figure(monkeypatch):
    captured = {}

    def fake_save(fig, out_dir, name, dpi):
        captured["fig"] = fig
        captured["name"] = name
        return os.path.join(out_dir, name)

    monkeypatch.setattr(trends, "_save", fake_save)
    return captured


def _lines_by_label(fig, panel=0):
    return {line.get_label(): line for line in fig.axes[panel].lines}


# --- chart_field_trend ------------------------------------------------------


def test_one_line_per_strategy_with_matching_values(captured_figure):
    history = [
        (
            "run-1",
            {"strategies": {"s1": {"avg_final_money": 100.0}, "s2": {"avg_final_money": 50.0}}},
        ),
        (
            "run-2",
            {"strategies": {"s1": {"avg_final_money": 110.0}, "s2": {"avg_final_money": 60.0}}},
        ),
    ]

    trends.chart_field_trend(matplotlib.pyplot, history, "avg_final_money", "unused", 60)

    lines = _lines_by_label(captured_figure["fig"])
    assert set(lines) == {"s1", "s2"}
    assert list(lines["s1"].get_ydata()) == [100.0, 110.0]
    assert list(lines["s2"].get_ydata()) == [50.0, 60.0]


def test_none_values_are_skipped_not_plotted_as_zero(captured_figure):
    history = [
        ("run-1", {"strategies": {"s1": {"avg_final_money": 100.0}}}),
        ("run-2", {"strategies": {"s1": {"avg_final_money": None}}}),
        ("run-3", {"strategies": {"s1": {"avg_final_money": 120.0}}}),
    ]

    trends.chart_field_trend(matplotlib.pyplot, history, "avg_final_money", "unused", 60)

    lines = _lines_by_label(captured_figure["fig"])
    # Run 2's undefined value is dropped, not plotted at 0 -- the x-data
    # skips straight from index 0 to index 2.
    assert list(lines["s1"].get_xdata()) == [0, 2]
    assert list(lines["s1"].get_ydata()) == [100.0, 120.0]


def test_fewer_than_two_points_returns_none():
    history = [("run-1", {"strategies": {"s1": {"avg_final_money": 100.0}}})]

    assert (
        trends.chart_field_trend(matplotlib.pyplot, history, "avg_final_money", "unused", 60)
        is None
    )


def test_field_undefined_everywhere_returns_none():
    history = [
        ("run-1", {"strategies": {"s1": {"avg_final_money": None}}}),
        ("run-2", {"strategies": {"s1": {"avg_final_money": None}}}),
    ]

    assert (
        trends.chart_field_trend(matplotlib.pyplot, history, "avg_final_money", "unused", 60)
        is None
    )


def test_strategy_missing_from_an_older_run_does_not_crash(captured_figure):
    """The agent roster can change between batches (AGENT_REGISTRY edits).
    A strategy absent from an older run's `strategies` dict should just
    start its line later, not raise a KeyError."""
    history = [
        ("run-1", {"strategies": {"s1": {"avg_final_money": 100.0}}}),
        (
            "run-2",
            {"strategies": {"s1": {"avg_final_money": 110.0}, "s2": {"avg_final_money": 40.0}}},
        ),
        (
            "run-3",
            {"strategies": {"s1": {"avg_final_money": 120.0}, "s2": {"avg_final_money": 45.0}}},
        ),
    ]

    trends.chart_field_trend(matplotlib.pyplot, history, "avg_final_money", "unused", 60)

    lines = _lines_by_label(captured_figure["fig"])
    assert list(lines["s1"].get_xdata()) == [0, 1, 2]
    assert list(lines["s2"].get_xdata()) == [1, 2]


# --- render_all --------------------------------------------------------


def test_render_all_returns_empty_list_with_fewer_than_two_runs():
    assert trends.render_all([], "unused", 60) == []
    assert trends.render_all([("run-1", {"strategies": {}})], "unused", 60) == []


def test_render_all_writes_a_chart_per_default_field(tmp_path):
    history = [
        ("run-1", {"strategies": {"s1": {"avg_final_money": 100.0, "bankruptcy_rate": 10.0}}}),
        ("run-2", {"strategies": {"s1": {"avg_final_money": 110.0, "bankruptcy_rate": 5.0}}}),
    ]

    paths = trends.render_all(
        history, str(tmp_path), dpi=60, fields=("avg_final_money", "bankruptcy_rate")
    )

    assert len(paths) == 2
    for path in paths:
        assert os.path.getsize(path) > 0


# --- load_history / append_current / _run_label -----------------------------


def test_load_history_reads_published_runs_oldest_first(tmp_path):
    reports_dir = str(tmp_path)
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)
    _write_run(reports_dir, "20260102T000000-b", STRATEGIES)

    history = trends.load_history(reports_dir)

    assert [run_id for run_id, _ in history] == ["20260101T000000-a", "20260102T000000-b"]
    assert history[0][1]["strategies"] == STRATEGIES


def test_load_history_skips_a_run_missing_summary_json(tmp_path):
    reports_dir = str(tmp_path)
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)
    os.makedirs(os.path.join(reports_dir, "runs", "20260102T000000-b"))  # no summary.json

    history = trends.load_history(reports_dir)

    assert [run_id for run_id, _ in history] == ["20260101T000000-a"]


def test_load_history_empty_when_nothing_published(tmp_path):
    assert trends.load_history(str(tmp_path)) == []


def test_append_current_adds_a_labeled_point():
    history = [("run-1", {"strategies": {}})]

    result = trends.append_current(history, {"strategies": {"s1": {}}})

    assert result[-1] == ("current", {"strategies": {"s1": {}}})
    assert result[:-1] == history


def test_run_label_parses_run_id_timestamp():
    assert trends._run_label("20260815T165711-jddr0juk") == "08-15 16:57"


def test_run_label_falls_back_for_unparseable_id():
    assert trends._run_label("current") == "current"
