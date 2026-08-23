"""Chart per-strategy metrics across published run history.

`metrics/visualize.py`'s charts all compare strategies within *one* batch
(one run's CSV rows, grouped by strategy -- the categorical axis is
strategy identity). This module answers a different question: is a given
strategy's `bankruptcy_rate`/`avg_final_money`/etc. trending up or down
across the last several published batches? The axis here is run history,
not strategy identity, so it needs a different input shape entirely: a
sequence of `(run_id, summary_doc)` pairs rather than CSV rows.

It reads `summary.json` via `metrics/view.py`'s `list_runs`/`load_run` --
the same read path `main.py view` uses -- rather than re-deriving anything
from the CSV, so a number here always means what it means there. This
module does not know about `main.py`'s `RUNS_RETAINED` or the publish/sweep
mechanism; it simply charts whatever `view.list_runs` finds on disk, which
is already bounded by that sweep.

`metrics/dashboard.py` is the only caller: it loads history, appends the
current (not-yet-published) batch's in-memory summary doc as the newest
point, and bundles the resulting PNGs into the same page as the per-batch
charts, under a "Run History" section.

Dict-valued summary fields (e.g. `crop_usage_pct`, where "dominant crop
share" actually lives) are out of scope for this first cut -- the default
field set below sticks to `metrics/view.py`'s own curated scalar fields so
this module's numbers and the CLI's stay in lockstep. Charting a crop's
share over time is a natural follow-up once this scaffolding exists.
"""

from datetime import datetime

from metrics import view
from metrics.visualize import (
    CATEGORICAL,
    INK_PRIMARY,
    INK_SECONDARY,
    _save,
    _style_axes,
)


def load_history(reports_dir: str) -> list[tuple[str, dict]]:
    """Published runs, oldest first, as `(run_id, summary_doc)` pairs.

    A retained run whose `summary.json` is missing or unreadable (older
    runs may predate the artifact -- see `view.load_run`'s own docstring)
    is skipped rather than raising, so one stale retained run doesn't blank
    the whole trend section.
    """
    history = []
    for run_id in view.list_runs(reports_dir):
        run_dir = f"{reports_dir}/{view.RUNS_DIRNAME}/{run_id}"
        try:
            history.append((run_id, view.load_run(run_dir)))
        except view.ViewError:
            continue
    return history


def append_current(
    history: list[tuple[str, dict]], current_summary_doc: dict, run_label: str = "current"
) -> list[tuple[str, dict]]:
    """Append the in-memory current batch as the newest point.

    It has no run id yet -- `render_dashboard_html` is called before
    `main.py`'s `_publish_report_artifacts` mints one and writes this run
    under `reports/runs/` -- so it's labeled `run_label` instead.
    """
    return [*history, (run_label, current_summary_doc)]


def _run_label(run_id: str) -> str:
    """Short x-axis tick from a `<%Y%m%dT%H%M%S>-<suffix>` run id, e.g.
    `'20260815T165711-jddr0juk'` -> `'08-15 16:57'`. Falls back to the raw
    id unchanged for anything that doesn't parse that way (e.g. `"current"`).
    """
    timestamp = run_id.split("-", 1)[0]
    try:
        return datetime.strptime(timestamp, "%Y%m%dT%H%M%S").strftime("%m-%d %H:%M")
    except ValueError:
        return run_id


def chart_field_trend(plt, history: list[tuple[str, dict]], field: str, out_dir: str, dpi: int):
    """One line per strategy, `field`'s value across run history on the x
    axis. Points with a `None` value (a strategy with no defined value for
    `field` in a given run -- e.g. `avg_final_money_survivors` with zero
    survivors) are skipped rather than plotted as 0, matching the
    None-skipping convention used throughout `visualize.py`/`view.py`. A
    strategy absent from an older run's `strategies` dict (the roster
    changed) needs no special case: `dict.get(name, {}).get(field)` is
    `None` either way, so its line simply starts later.

    Returns `None` (chart omitted) if fewer than 2 points total are
    plotted across every strategy -- a single point isn't a trend, the
    same "omitted, not broken" precedent as
    `visualize.chart_contract_completion_rate` returning `None` when no
    strategy has contract data.
    """
    labels = [_run_label(run_id) for run_id, _ in history]

    names = []
    for _, doc in history:
        for name in doc.get("strategies", {}):
            if name not in names:
                names.append(name)

    series = {}
    for name in names:
        points = []
        for x, (_, doc) in enumerate(history):
            value = doc.get("strategies", {}).get(name, {}).get(field)
            if value is not None:
                points.append((x, value))
        if points:
            series[name] = points

    if sum(len(points) for points in series.values()) < 2:
        return None

    fig, ax = plt.subplots(figsize=(9, 5))
    for i, name in enumerate(series):
        points = series[name]
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        color = CATEGORICAL[i % len(CATEGORICAL)]
        ax.plot(xs, ys, color=color, linewidth=1.8, marker="o", markersize=4, label=name, zorder=3)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(view.FIELD_LABELS.get(field, field))
    ax.set_title(
        f"{view.FIELD_LABELS.get(field, field)} across run history",
        color=INK_PRIMARY,
        fontsize=13,
        loc="left",
        pad=12,
    )
    ax.legend(loc="best", frameon=False, fontsize=8, labelcolor=INK_SECONDARY)
    _style_axes(ax, horizontal_grid=False)
    fig.tight_layout()
    return _save(fig, out_dir, f"trend_{field}.png", dpi)


def render_all(
    history: list[tuple[str, dict]],
    out_dir: str,
    dpi: int,
    fields: tuple[str, ...] = view.DEFAULT_FIELDS,
    show: bool = False,
) -> list[str]:
    """Mirrors `visualize.render_all`'s shape: sets up matplotlib, renders
    one chart per field, filters omitted (`None`) charts, returns paths.

    Short-circuits to `[]` when there's less than 2 runs of history total,
    rather than calling every `chart_field_trend` only to have each
    individually return `None` -- gives `metrics/dashboard.py` one cheap,
    obvious signal for "not enough history yet" instead of re-deriving it
    from an empty list of paths.
    """
    if len(history) < 2:
        return []

    import matplotlib

    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["font.family"] = ["DejaVu Sans", "Arial", "sans-serif"]

    paths = [chart_field_trend(plt, history, field, out_dir, dpi) for field in fields]
    paths = [p for p in paths if p]

    if show:
        plt.show()
    else:
        plt.close("all")
    return paths
