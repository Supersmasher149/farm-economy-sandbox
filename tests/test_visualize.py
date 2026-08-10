"""Smoke tests for metrics/visualize.py.

Nothing else in the test suite imports this module, which is how it came to
carry two bugs that a single render would have caught: it took `max()` over
crop-loss values that are legitimately `None`, and it plotted `watering_rate`
under an axis labelled as coverage of *occupied* plot-days. Charts are hard to
assert on in detail, so these tests aim at the two things that are checkable
without pinning pixels -- it renders at all for the data shapes a real batch
produces, and the series carry the numbers the labels claim.

matplotlib is not needed to run the simulator (see requirements-viz.txt), so
the whole module skips when it is absent.
"""

import os

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from metrics import visualize  # noqa: E402
from metrics.run_results import write_csv  # noqa: E402
from tests.test_aggregate_results import _make_run_result  # noqa: E402

CROP_IDS = ["quickweed", "greenleaf"]


def _write_csv(tmp_path, results):
    path = os.path.join(tmp_path, "run_results.csv")
    write_csv(results, path, CROP_IDS)
    return path


def _representative_results():
    """Two strategies, several runs each, every field defined.

    Contract counts are non-zero deliberately: chart_contract_completion_rate
    skips strategies that never took a contract, so a fixture of all-zeros
    would silently render six charts instead of seven.
    """
    results = []
    for i in range(5):
        results.append(
            _make_run_result(
                strategy="profit_optimizer",
                seed=i,
                final_money=100.0 + i,
                crop_loss_rate=float(i),
                watering_rate=40.0 + i,
                occupied_watering_rate=70.0 + i,
                crop_percentages={"quickweed": 60.0, "greenleaf": 40.0},
                contracts_completed=4,
                contracts_failed=1,
            )
        )
        results.append(
            _make_run_result(
                strategy="fast_seller",
                seed=100 + i,
                final_money=5.0 + i,
                bankrupt=True,
                bankruptcy_day=3,
                crop_loss_rate=20.0 + i,
                watering_rate=10.0 + i,
                occupied_watering_rate=30.0 + i,
                crop_percentages={"quickweed": 100.0},
                contracts_completed=1,
                contracts_failed=3,
            )
        )
    return results


@pytest.fixture
def captured_figure(monkeypatch):
    """Hold on to the last figure `_save` was handed, so a chart can be
    inspected as data instead of as a PNG."""
    captured = {}

    def fake_save(fig, out_dir, name, dpi):
        captured["fig"] = fig
        captured["name"] = name
        return os.path.join(out_dir, name)

    monkeypatch.setattr(visualize, "_save", fake_save)
    return captured


def _scatter_points(fig, panel=0):
    offsets = fig.axes[panel].collections[0].get_offsets()
    return [tuple(row) for row in offsets.tolist()]


def test_renders_every_chart_for_a_representative_batch(tmp_path):
    csv_path = _write_csv(tmp_path, _representative_results())
    out_dir = os.path.join(tmp_path, "charts")

    paths = visualize.render_all(csv_path, out_dir, dpi=60, show=False)

    assert len(paths) == 7
    for path in paths:
        assert os.path.getsize(path) > 0


def test_undefined_rates_are_dropped_rather_than_plotted_as_zero():
    """A run that never harvested has no loss rate. Plotting it at 0% would
    put it in the corner that means "watered nothing, lost nothing" -- the
    single most flattering point on the chart."""
    rows = [
        {"occupied_watering_rate": 80.0, "crop_loss_rate": 4.0},
        {"occupied_watering_rate": None, "crop_loss_rate": 4.0},
        {"occupied_watering_rate": 80.0, "crop_loss_rate": None},
        {"occupied_watering_rate": None, "crop_loss_rate": None},
    ]

    assert visualize._watering_loss_points(rows) == [(80.0, 4.0)]


def test_scatter_plots_occupied_coverage_not_raw_coverage(captured_figure):
    """The x axis is labelled "coverage of occupied plot-days"; it used to be
    fed `watering_rate`, which counts empty plots in the denominator."""
    grouped = {
        "test": [
            {"watering_rate": 10.0, "occupied_watering_rate": 90.0, "crop_loss_rate": 1.0},
            {"watering_rate": 20.0, "occupied_watering_rate": 80.0, "crop_loss_rate": 2.0},
        ]
    }

    visualize.chart_watering_vs_crop_loss(matplotlib.pyplot, grouped, "unused", 60)

    assert sorted(_scatter_points(captured_figure["fig"])) == [(80.0, 2.0), (90.0, 1.0)]


def test_batch_where_nothing_matured_renders_instead_of_crashing(captured_figure):
    """The regression: `max()` over a list containing None raises TypeError,
    and over an empty list raises ValueError. A 5-day diagnostic batch, or any
    strategy that bankrupts before its first harvest, produces exactly this.
    """
    grouped = {
        "test": [
            {"watering_rate": 10.0, "occupied_watering_rate": None, "crop_loss_rate": None},
            {"watering_rate": 20.0, "occupied_watering_rate": 50.0, "crop_loss_rate": None},
        ]
    }

    visualize.chart_watering_vs_crop_loss(matplotlib.pyplot, grouped, "unused", 60)

    fig = captured_figure["fig"]
    assert _scatter_points(fig) == []
    # Axis limits still have to be finite and ordered for matplotlib to draw.
    low, high = fig.axes[0].get_xlim()
    assert low < high


def test_empty_panel_says_why_it_is_empty(captured_figure):
    grouped = {
        "harvested": [{"occupied_watering_rate": 50.0, "crop_loss_rate": 5.0}],
        "starved": [{"occupied_watering_rate": None, "crop_loss_rate": None}],
    }

    visualize.chart_watering_vs_crop_loss(matplotlib.pyplot, grouped, "unused", 60)

    fig = captured_figure["fig"]
    # Panels are laid out in sorted strategy order: harvested, then starved.
    assert [text.get_text() for text in fig.axes[0].texts] == []
    assert [text.get_text() for text in fig.axes[1].texts] == ["no run harvested"]


def test_partial_batch_renders_end_to_end(tmp_path):
    """One strategy with real numbers, one that never harvested -- the shape a
    `--days 5` diagnostic batch actually writes."""
    results = _representative_results() + [
        _make_run_result(
            strategy="neglectful_grower",
            seed=200 + i,
            crops_harvested=0,
            crop_loss_rate=None,
            occupied_watering_rate=None,
            occupied_slot_days=0,
        )
        for i in range(3)
    ]
    csv_path = _write_csv(tmp_path, results)

    paths = visualize.render_all(csv_path, os.path.join(tmp_path, "charts"), dpi=60, show=False)

    assert len(paths) == 7


def test_missing_csv_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit, match="No such file"):
        visualize.load_runs(os.path.join(tmp_path, "absent.csv"))


def test_header_only_csv_is_a_clean_error(tmp_path):
    csv_path = _write_csv(tmp_path, [])
    with pytest.raises(SystemExit, match="no data rows"):
        visualize.load_runs(csv_path)
