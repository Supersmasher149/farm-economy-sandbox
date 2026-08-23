"""Tests for metrics/dashboard.py -- bundling metrics.visualize's PNGs into
one self-contained HTML page.

Mirrors tests/test_visualize.py's fixtures (same RunResult builder, same
strategies) since this module is a thin repackaging of that one's output,
not a second implementation of the charts themselves.
"""

import os
import sys

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from metrics.dashboard import render_dashboard_html, write_no_charts_placeholder  # noqa: E402
from metrics.run_results import write_csv  # noqa: E402
from tests.test_aggregate_results import _make_run_result  # noqa: E402
from tests.test_view import STRATEGIES, _write_run  # noqa: E402

CROP_IDS = ["quickweed", "greenleaf"]


def _write_csv(tmp_path, results):
    path = os.path.join(tmp_path, "run_results.csv")
    write_csv(results, path, CROP_IDS)
    return path


def _representative_results():
    results = []
    for strategy in ("profit_optimizer", "reckless_spender"):
        for seed in range(3):
            results.append(
                _make_run_result(
                    strategy=strategy,
                    seed=seed,
                    final_money=100.0 + seed,
                    bankrupt=(strategy == "reckless_spender" and seed == 0),
                    bankruptcy_day=5 if strategy == "reckless_spender" and seed == 0 else None,
                )
            )
    return results


def test_dashboard_embeds_every_chart_as_one_self_contained_page(tmp_path):
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    result_path = render_dashboard_html(
        csv_path, out_path, title="Test Report", subtitle="3 runs x 2 strategies"
    )

    assert result_path == out_path
    with open(out_path) as f:
        content = f.read()

    assert content.startswith("<!doctype html>")
    assert "Test Report" in content
    assert "3 runs x 2 strategies" in content
    # Charts are embedded as data URIs, not linked files -- the page must
    # not depend on anything else existing on disk.
    assert "data:image/png;base64," in content
    assert "Final money distribution" in content
    assert "Bankruptcy rate" in content


def test_dashboard_writes_placeholder_when_matplotlib_missing(tmp_path, monkeypatch):
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    # sys.modules[name] = None forces `import name` to raise ImportError,
    # without actually uninstalling matplotlib for the rest of the suite.
    monkeypatch.setitem(sys.modules, "matplotlib", None)

    render_dashboard_html(csv_path, out_path, title="Test Report")

    with open(out_path) as f:
        content = f.read()
    assert "matplotlib" in content.lower()
    assert "Test Report" in content


def test_write_no_charts_placeholder(tmp_path):
    out_path = os.path.join(tmp_path, "dashboard.html")

    write_no_charts_placeholder(out_path, title="Test Report")

    with open(out_path) as f:
        content = f.read()
    assert "--no-charts" in content
    assert "Test Report" in content


def test_dashboard_omits_run_history_without_trend_params(tmp_path):
    """Backward compat: callers that don't pass current_summary_doc/
    reports_dir (every caller before this feature existed) get exactly
    today's page -- no "Run History" section at all."""
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    render_dashboard_html(csv_path, out_path, title="Test Report")

    with open(out_path) as f:
        content = f.read()
    assert "Run History" not in content


def test_dashboard_includes_run_history_with_prior_runs(tmp_path):
    reports_dir = os.path.join(tmp_path, "reports")
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)
    _write_run(reports_dir, "20260102T000000-b", STRATEGIES)
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    render_dashboard_html(
        csv_path,
        out_path,
        title="Test Report",
        current_summary_doc={"strategies": STRATEGIES},
        reports_dir=reports_dir,
    )

    with open(out_path) as f:
        content = f.read()
    assert "Run History" in content
    assert "final_money" in content
    # Single-run charts still render alongside the trend section.
    assert "Bankruptcy rate" in content


def test_dashboard_run_history_degrades_gracefully_with_no_prior_runs(tmp_path):
    """Zero published runs yet (first-ever batch): the current in-memory
    run alone is one point, not a trend -- the section explains why it's
    empty instead of a broken/empty chart, and the rest of the page still
    renders normally."""
    reports_dir = os.path.join(tmp_path, "reports")
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    render_dashboard_html(
        csv_path,
        out_path,
        title="Test Report",
        current_summary_doc={"strategies": STRATEGIES},
        reports_dir=reports_dir,
    )

    with open(out_path) as f:
        content = f.read()
    assert "Run History" in content
    assert "Fewer than 2 retained runs yet" in content
    assert "Bankruptcy rate" in content  # single-run charts unaffected


def test_dashboard_run_history_renders_with_exactly_one_prior_run(tmp_path):
    """One prior run + the current in-memory run = 2 total points, the
    real minimum for a line -- this should render an actual trend, not the
    "fewer than 2" placeholder."""
    reports_dir = os.path.join(tmp_path, "reports")
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)
    csv_path = _write_csv(tmp_path, _representative_results())
    out_path = os.path.join(tmp_path, "dashboard.html")

    render_dashboard_html(
        csv_path,
        out_path,
        title="Test Report",
        current_summary_doc={"strategies": STRATEGIES},
        reports_dir=reports_dir,
    )

    with open(out_path) as f:
        content = f.read()
    assert "Fewer than 2 retained runs yet" not in content
    assert "final_money" in content
