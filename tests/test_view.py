"""Tests for metrics/view.py -- the stdlib CLI table/diff viewer over
published reports/runs/<id>/summary.json artifacts.

These build run directories directly (a summary.json plus a `latest`
symlink) rather than running a real batch, so run resolution, table
rendering, and diffing can be checked in isolation from the simulator.
"""

import json
import os

import pytest

from metrics.view import (
    ViewError,
    list_runs,
    load_run,
    render_diff,
    render_table,
    render_warnings,
    resolve_run_dir,
    scalar_fields,
)


def _write_run(reports_dir, run_id, strategies, warnings=None):
    run_dir = os.path.join(reports_dir, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    doc = {
        "base_seed": 1,
        "num_runs": 10,
        "days": 30,
        "start_money": 100,
        "warnings": warnings or [],
        "strategies": strategies,
    }
    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(doc, f)
    return run_dir


def _symlink_latest(reports_dir, run_id):
    link_path = os.path.join(reports_dir, "latest")
    if os.path.islink(link_path):
        os.unlink(link_path)
    os.symlink(os.path.join("runs", run_id), link_path)


STRATEGIES = {
    "profit_optimizer": {
        "avg_final_money": 300.0,
        "bankruptcy_rate": 0.0,
        "avg_profit_per_day": 9.0,
        "avg_watering_rate": 80.0,
        "crop_usage_pct": {"quickweed": 100.0},
    },
    "reckless_spender": {
        "avg_final_money": -50.0,
        "bankruptcy_rate": 40.0,
        "avg_profit_per_day": -3.0,
        "avg_watering_rate": 20.0,
        "crop_usage_pct": {"quickweed": 50.0, "greenleaf": 50.0},
    },
    "random_agent": {
        "avg_final_money": 100.0,
        "bankruptcy_rate": 10.0,
        "avg_profit_per_day": None,  # never observed a defined ratio
        "avg_watering_rate": 50.0,
        "crop_usage_pct": {},
    },
}


# --- resolve_run_dir / list_runs -------------------------------------------


def test_resolve_latest_follows_symlink(tmp_path):
    reports_dir = str(tmp_path)
    run_dir = _write_run(reports_dir, "20260101T000000-aaa", STRATEGIES)
    _symlink_latest(reports_dir, "20260101T000000-aaa")

    assert os.path.realpath(resolve_run_dir(reports_dir, None)) == os.path.realpath(run_dir)
    assert os.path.realpath(resolve_run_dir(reports_dir, "latest")) == os.path.realpath(run_dir)


def test_resolve_latest_with_no_published_runs_raises(tmp_path):
    with pytest.raises(ViewError):
        resolve_run_dir(str(tmp_path), "latest")


def test_resolve_latest_n_counts_back_chronologically(tmp_path):
    reports_dir = str(tmp_path)
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)
    _write_run(reports_dir, "20260102T000000-b", STRATEGIES)
    _write_run(reports_dir, "20260103T000000-c", STRATEGIES)
    _symlink_latest(reports_dir, "20260103T000000-c")

    assert resolve_run_dir(reports_dir, "latest-0").endswith("20260103T000000-c")
    assert resolve_run_dir(reports_dir, "latest-1").endswith("20260102T000000-b")
    assert resolve_run_dir(reports_dir, "latest-2").endswith("20260101T000000-a")


def test_resolve_latest_n_out_of_range_raises(tmp_path):
    reports_dir = str(tmp_path)
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)

    with pytest.raises(ViewError):
        resolve_run_dir(reports_dir, "latest-5")


def test_resolve_by_run_id(tmp_path):
    reports_dir = str(tmp_path)
    run_dir = _write_run(reports_dir, "20260101T000000-a", STRATEGIES)

    assert resolve_run_dir(reports_dir, "20260101T000000-a") == run_dir


def test_resolve_unknown_ref_raises_with_hint(tmp_path):
    with pytest.raises(ViewError, match="--list"):
        resolve_run_dir(str(tmp_path), "not-a-real-run")


def test_list_runs_oldest_first(tmp_path):
    reports_dir = str(tmp_path)
    _write_run(reports_dir, "20260102T000000-b", STRATEGIES)
    _write_run(reports_dir, "20260101T000000-a", STRATEGIES)

    assert list_runs(reports_dir) == ["20260101T000000-a", "20260102T000000-b"]


def test_list_runs_empty_when_nothing_published(tmp_path):
    assert list_runs(str(tmp_path)) == []


# --- load_run ----------------------------------------------------------


def test_load_run_missing_summary_json_raises_clear_error(tmp_path):
    run_dir = os.path.join(tmp_path, "runs", "old-run")
    os.makedirs(run_dir)

    with pytest.raises(ViewError, match="summary.json"):
        load_run(run_dir)


def test_load_run_reads_the_written_doc(tmp_path):
    run_dir = _write_run(str(tmp_path), "20260101T000000-a", STRATEGIES, warnings=["hello"])

    doc = load_run(run_dir)

    assert doc["warnings"] == ["hello"]
    assert doc["strategies"] == STRATEGIES


# --- scalar_fields -------------------------------------------------------


def test_scalar_fields_excludes_dict_valued_fields():
    fields = scalar_fields(STRATEGIES)

    assert "avg_final_money" in fields
    assert "crop_usage_pct" not in fields


# --- render_table ----------------------------------------------------------


def test_render_table_default_fields_and_sort_descending():
    table = render_table(STRATEGIES)

    lines = table.splitlines()
    # Default sort is by the first field (avg_final_money), descending.
    order = [line.split()[0] for line in lines[2:]]
    assert order == ["profit_optimizer", "random_agent", "reckless_spender"]
    assert "final_money" in lines[0]
    assert "bankrupt%" in lines[0]
    assert "profit/day" in lines[0]


def test_render_table_ascending_sort():
    table = render_table(STRATEGIES, sort_by="avg_final_money", ascending=True)

    order = [line.split()[0] for line in table.splitlines()[2:]]
    assert order == ["reckless_spender", "random_agent", "profit_optimizer"]


def test_render_table_none_values_sort_last_either_direction():
    # random_agent's avg_profit_per_day is None -- it must land last whether
    # sorting descending or ascending, never at either extreme as if it were
    # the smallest/largest real value.
    desc = render_table(STRATEGIES, fields=["avg_profit_per_day"], sort_by="avg_profit_per_day")
    asc = render_table(
        STRATEGIES, fields=["avg_profit_per_day"], sort_by="avg_profit_per_day", ascending=True
    )

    assert desc.splitlines()[-1].split()[0] == "random_agent"
    assert asc.splitlines()[-1].split()[0] == "random_agent"


def test_render_table_top_n():
    table = render_table(STRATEGIES, top=1)

    data_rows = table.splitlines()[2:]
    assert len(data_rows) == 1
    assert data_rows[0].startswith("profit_optimizer")


def test_render_table_strategy_filter():
    table = render_table(STRATEGIES, strategy_filter=["reckless_spender"])

    data_rows = table.splitlines()[2:]
    assert len(data_rows) == 1
    assert data_rows[0].startswith("reckless_spender")


def test_render_table_unknown_field_raises():
    with pytest.raises(ViewError, match="not_a_real_field"):
        render_table(STRATEGIES, fields=["not_a_real_field"])


def test_render_table_unknown_strategy_raises():
    with pytest.raises(ViewError, match="nope"):
        render_table(STRATEGIES, strategy_filter=["nope"])


# --- render_diff -------------------------------------------------------


def test_render_diff_reports_delta_and_sorts_by_magnitude():
    a = {"s1": {"avg_final_money": 100.0}, "s2": {"avg_final_money": 100.0}}
    b = {"s1": {"avg_final_money": 105.0}, "s2": {"avg_final_money": 400.0}}

    diff = render_diff("before", a, "before", b, fields=["avg_final_money"])

    lines = diff.splitlines()
    data_rows = [line for line in lines if line.startswith("s1") or line.startswith("s2")]
    assert data_rows[0].startswith("s2")  # bigger |delta| (300) sorts first
    assert data_rows[1].startswith("s1")


def test_render_diff_notes_strategies_only_on_one_side():
    a = {"only_a": {"avg_final_money": 1.0}}
    b = {"only_b": {"avg_final_money": 1.0}}

    diff = render_diff("A", a, "B", b, fields=["avg_final_money"])

    assert "only in A: only_a" in diff
    assert "only in B: only_b" in diff


def test_render_diff_only_changed_hides_unchanged_rows():
    a = {"same": {"avg_final_money": 1.0}, "moved": {"avg_final_money": 1.0}}
    b = {"same": {"avg_final_money": 1.0}, "moved": {"avg_final_money": 2.0}}

    diff = render_diff("before", a, "after", b, fields=["avg_final_money"], only_changed=True)

    assert "moved" in diff
    assert "same" not in diff


# --- render_warnings -----------------------------------------------------


def test_render_warnings_none():
    assert render_warnings([]) == "No balance warnings."


def test_render_warnings_lists_each():
    text = render_warnings(["a bad thing", "another bad thing"])
    assert "a bad thing" in text
    assert "another bad thing" in text
