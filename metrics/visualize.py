"""Chart the strategy comparisons in a batch report.

Reads `reports/run_results.csv` (written by `python3 main.py batch`) --
one row per simulated run -- and renders a set of PNGs under
`reports/charts/` that make cross-strategy trends visible at a glance:
profit distribution, bankruptcy risk, cost structure, crop mix, contract
performance, and the watering-vs-crop-loss relationship.

Usage:
    python3 main.py batch --runs 1000              # produce reports/run_results.csv
    python3 -m metrics.visualize                    # render reports/charts/*.png
    python3 -m metrics.visualize --csv reports/run_results.csv --out reports/charts
    python3 -m metrics.visualize --show              # also open each chart in a window

Run with `-m metrics.visualize`, not `python3 metrics/visualize.py` directly:
executing it as a bare script puts metrics/ itself on sys.path, and
metrics/warnings.py then shadows the standard-library `warnings` module
that matplotlib imports internally.

Requires matplotlib (`pip install matplotlib`), unlike the rest of the
simulator, which has no third-party dependencies.
"""

import argparse
import csv
import json
import os
import statistics
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(BASE_DIR, "reports", "run_results.csv")
DEFAULT_OUT_DIR = os.path.join(BASE_DIR, "reports", "charts")

# Balance-warning thresholds this repo already treats as meaningful
# (metrics/warnings.py) -- reused here so the charts flag the same things
# the automated report does, rather than inventing new cutoffs.
HIGH_BANKRUPTCY_PCT = 20

# Fixed-order categorical palette (CVD-safe adjacent pairs, validated for
# up to 8 series -- see the dataviz skill's references/palette.md). Never
# cycle or reassign these by rank; a series beyond slot 8 folds into "Other".
CATEGORICAL = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
]
SEQUENTIAL_BLUE = "#2a78d6"
DIVERGING_POS = "#2a78d6"
DIVERGING_NEG = "#e34948"
STATUS_CRITICAL = "#d03b3b"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"

JSON_FIELDS = {
    "expenses_by_category",
    "revenue_by_channel",
    "quality_harvested",
    "crop_decision_observations",
}


def _parse_cell(key: str, value: str):
    if value == "":
        return None
    if key in JSON_FIELDS:
        return json.loads(value)
    if key == "bankrupt":
        return value == "True"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_runs(csv_path: str) -> tuple[list[dict], list[str]]:
    """Return (rows, crop_ids). Each row has parsed (not string) values."""
    if not os.path.exists(csv_path):
        raise SystemExit(
            f"No such file: {csv_path}\nGenerate it first with: python3 main.py batch --runs 1000"
        )
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        crop_ids = [name[len("pct_") :] for name in reader.fieldnames if name.startswith("pct_")]
        rows = [{k: _parse_cell(k, v) for k, v in row.items()} for row in reader]
    if not rows:
        raise SystemExit(f"{csv_path} has no data rows.")
    return rows, crop_ids


def group_by_strategy(rows: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["strategy"]].append(row)
    return dict(grouped)


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else 0.0


def _stdev(values):
    values = [v for v in values if v is not None]
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _style_axes(ax, *, horizontal_grid=True):
    """Recessive gridlines, muted axis, no top/right spines -- applied to
    every chart so the data ink, not the chrome, carries the reader's eye.
    """
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.tick_params(colors=INK_SECONDARY, labelsize=9)
    ax.xaxis.label.set_color(INK_SECONDARY)
    ax.yaxis.label.set_color(INK_SECONDARY)
    if horizontal_grid:
        ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    else:
        ax.grid(axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def _save(fig, out_dir: str, name: str, dpi: int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=SURFACE)
    return path


def chart_final_money_distribution(plt, grouped, out_dir, dpi):
    """Box plot: spread of final money per strategy. One axis (money),
    strategy identity carried by the x-tick labels rather than color, so a
    single hue is enough -- this is a magnitude/spread question, not an
    identity one.
    """
    order = sorted(
        grouped, key=lambda s: statistics.median(r["final_money"] for r in grouped[s]), reverse=True
    )
    data = [[r["final_money"] for r in grouped[s]] for s in order]

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.boxplot(
        data,
        orientation="vertical",
        patch_artist=True,
        widths=0.55,
        medianprops=dict(color=INK_PRIMARY, linewidth=2),
        whiskerprops=dict(color=INK_MUTED, linewidth=1.2),
        capprops=dict(color=INK_MUTED, linewidth=1.2),
        boxprops=dict(
            facecolor=SEQUENTIAL_BLUE, alpha=0.35, edgecolor=SEQUENTIAL_BLUE, linewidth=1.5
        ),
        flierprops=dict(
            marker="o", markersize=3, markerfacecolor=INK_MUTED, markeredgecolor="none", alpha=0.5
        ),
    )
    ax.set_xticks(range(1, len(order) + 1))
    ax.set_xticklabels(order, rotation=35, ha="right")
    ax.set_ylabel("Final money")
    ax.set_title(
        "Final money distribution by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12
    )
    ax.axhline(0, color=BASELINE, linewidth=1)
    _style_axes(ax, horizontal_grid=False)
    fig.tight_layout()
    return _save(fig, out_dir, "final_money_distribution.png", dpi)


def chart_bankruptcy_rate(plt, grouped, out_dir, dpi):
    """Horizontal bar: % of runs that went bankrupt, per strategy. Bars
    crossing the repo's own high-bankruptcy threshold (metrics/warnings.py)
    are marked with the reserved critical status color plus a direct label
    and a labeled threshold line -- status color never carries meaning alone.
    """
    rates = {s: 100 * sum(r["bankrupt"] for r in rows) / len(rows) for s, rows in grouped.items()}
    order = sorted(rates, key=rates.get)
    values = [rates[s] for s in order]
    colors = [STATUS_CRITICAL if v > HIGH_BANKRUPTCY_PCT else SEQUENTIAL_BLUE for v in values]

    fig, ax = plt.subplots(figsize=(9, max(3, 0.45 * len(order) + 1)))
    y = range(len(order))
    ax.barh(y, values, color=colors, height=0.6, zorder=3)
    ax.axvline(HIGH_BANKRUPTCY_PCT, color=STATUS_CRITICAL, linewidth=1.2, linestyle="--", zorder=2)
    ax.text(
        HIGH_BANKRUPTCY_PCT,
        len(order) - 0.3,
        f"  {HIGH_BANKRUPTCY_PCT}% high-bankruptcy threshold",
        color=STATUS_CRITICAL,
        fontsize=8,
        va="top",
    )
    for yi, v in zip(y, values, strict=True):
        ax.text(
            v + max(values) * 0.01 + 0.3,
            yi,
            f"{v:.1f}%",
            va="center",
            fontsize=9,
            color=INK_SECONDARY,
        )
    ax.set_yticks(list(y))
    ax.set_yticklabels(order)
    ax.set_xlabel("% of runs bankrupt")
    ax.set_title("Bankruptcy rate by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12)
    ax.set_xlim(0, max(values + [HIGH_BANKRUPTCY_PCT]) * 1.2 + 1)
    _style_axes(ax)
    fig.tight_layout()
    return _save(fig, out_dir, "bankruptcy_rate.png", dpi)


def chart_avg_profit_per_day(plt, grouped, out_dir, dpi):
    """Horizontal bar with std-dev error bars: mean profit/day per strategy.
    Profit above/below zero is a polarity question, so bars use the
    diverging blue/red pair around a zero baseline rather than one hue.
    """
    means = {s: _mean(r["avg_profit_per_day"] for r in rows) for s, rows in grouped.items()}
    stdevs = {s: _stdev(r["avg_profit_per_day"] for r in rows) for s, rows in grouped.items()}
    order = sorted(means, key=means.get, reverse=True)
    values = [means[s] for s in order]
    errs = [stdevs[s] for s in order]
    colors = [DIVERGING_POS if v >= 0 else DIVERGING_NEG for v in values]

    fig, ax = plt.subplots(figsize=(9, max(3, 0.45 * len(order) + 1)))
    y = range(len(order))
    ax.barh(
        y,
        values,
        xerr=errs,
        color=colors,
        height=0.6,
        zorder=3,
        error_kw=dict(ecolor=INK_MUTED, elinewidth=1, capsize=3),
    )
    ax.axvline(0, color=BASELINE, linewidth=1.2)
    span = max(abs(v) + e for v, e in zip(values, errs, strict=True)) or 1
    for yi, v, e in zip(y, values, errs, strict=True):
        sign = 1 if v >= 0 else -1
        label_x = v + sign * (e + span * 0.03)
        ax.text(
            label_x,
            yi,
            f"{v:.2f}",
            va="center",
            ha="left" if v >= 0 else "right",
            fontsize=9,
            color=INK_SECONDARY,
        )
    ax.margins(x=0.15)
    ax.set_yticks(list(y))
    ax.set_yticklabels(order)
    ax.set_xlabel("Avg. profit per day (mean ± std. dev. across runs)")
    ax.set_title(
        "Average daily profit by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12
    )
    _style_axes(ax)
    fig.tight_layout()
    return _save(fig, out_dir, "avg_profit_per_day.png", dpi)


def chart_expense_breakdown(plt, grouped, out_dir, dpi):
    """Stacked horizontal bar: mean spend per expense category, per
    strategy -- shows where each strategy's money actually goes.
    """
    categories = sorted(
        {cat for rows in grouped.values() for r in rows for cat in r["expenses_by_category"]}
    )
    if len(categories) > len(CATEGORICAL):
        kept, overflow = categories[: len(CATEGORICAL) - 1], categories[len(CATEGORICAL) - 1 :]
    else:
        kept, overflow = categories, []
    order = sorted(
        grouped,
        key=lambda s: _mean(sum(r["expenses_by_category"].values()) for r in grouped[s]),
        reverse=True,
    )

    fig, ax = plt.subplots(figsize=(10, max(3, 0.5 * len(order) + 1)))
    y = range(len(order))
    left = [0.0] * len(order)
    for i, cat in enumerate(kept):
        vals = [_mean(r["expenses_by_category"].get(cat, 0.0) for r in grouped[s]) for s in order]
        ax.barh(
            y,
            vals,
            left=left,
            color=CATEGORICAL[i],
            height=0.6,
            label=cat,
            edgecolor=SURFACE,
            linewidth=1,
            zorder=3,
        )
        left = [acc + v for acc, v in zip(left, vals, strict=True)]
    if overflow:
        vals = [
            _mean(sum(r["expenses_by_category"].get(c, 0.0) for c in overflow) for r in grouped[s])
            for s in order
        ]
        ax.barh(
            y,
            vals,
            left=left,
            color=CATEGORICAL[-1],
            height=0.6,
            label="other",
            edgecolor=SURFACE,
            linewidth=1,
            zorder=3,
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels(order)
    ax.set_xlabel("Mean expense per run, by category")
    ax.set_title(
        "Expense breakdown by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12
    )
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    _style_axes(ax)
    fig.tight_layout()
    return _save(fig, out_dir, "expense_breakdown.png", dpi)


def chart_crop_mix(plt, grouped, crop_ids, out_dir, dpi):
    """Stacked horizontal bar: mean % of plantings per crop, per strategy --
    the crop-choice trend the roster's docstrings care about (dominant
    crop / dead crop / diversification).
    """
    crop_ids = list(crop_ids)
    if len(crop_ids) > len(CATEGORICAL):
        crop_ids = crop_ids[: len(CATEGORICAL)]
    order = sorted(grouped)

    fig, ax = plt.subplots(figsize=(10, max(3, 0.5 * len(order) + 1)))
    y = range(len(order))
    left = [0.0] * len(order)
    for i, cid in enumerate(crop_ids):
        vals = [_mean(r.get(f"pct_{cid}", 0.0) for r in grouped[s]) for s in order]
        ax.barh(
            y,
            vals,
            left=left,
            color=CATEGORICAL[i],
            height=0.6,
            label=cid,
            edgecolor=SURFACE,
            linewidth=1,
            zorder=3,
        )
        left = [acc + v for acc, v in zip(left, vals, strict=True)]

    ax.set_yticks(list(y))
    ax.set_yticklabels(order)
    ax.set_xlabel("Mean % of plantings")
    ax.set_xlim(0, 100)
    ax.set_title("Crop mix by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12)
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK_SECONDARY)
    _style_axes(ax)
    fig.tight_layout()
    return _save(fig, out_dir, "crop_mix.png", dpi)


def chart_contract_completion_rate(plt, grouped, out_dir, dpi):
    """Bar chart: contract completion rate per strategy, restricted to
    strategies that actually engaged with contracts (most agents leave
    `choose_contracts` at its default no-op, so including them would just
    plot a row of undefined 0/0s).
    """
    rates = {}
    for s, rows in grouped.items():
        total_completed = sum(r["contracts_completed"] for r in rows)
        total_failed = sum(r["contracts_failed"] for r in rows)
        if total_completed + total_failed > 0:
            rates[s] = 100 * total_completed / (total_completed + total_failed)
    if not rates:
        return None
    order = sorted(rates, key=rates.get)
    values = [rates[s] for s in order]

    fig, ax = plt.subplots(figsize=(9, max(2.5, 0.45 * len(order) + 1)))
    y = range(len(order))
    ax.barh(y, values, color=SEQUENTIAL_BLUE, height=0.6, zorder=3)
    for yi, v in zip(y, values, strict=True):
        ax.text(v + 1, yi, f"{v:.1f}%", va="center", fontsize=9, color=INK_SECONDARY)
    ax.set_yticks(list(y))
    ax.set_yticklabels(order)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Contracts completed / (completed + failed)")
    ax.set_title(
        "Contract completion rate by strategy", color=INK_PRIMARY, fontsize=13, loc="left", pad=12
    )
    _style_axes(ax)
    fig.tight_layout()
    return _save(fig, out_dir, "contract_completion_rate.png", dpi)


def _watering_loss_points(rows):
    """(x, y) pairs for runs where both rates are defined.

    Both are legitimately None: `crop_loss_rate` when nothing matured (no
    denominator), `occupied_watering_rate` when no slot was ever occupied.
    Neither is 0% -- a run that never harvested did not lose 0% of its crop,
    it has no loss rate at all -- so those runs are dropped rather than
    plotted at the origin, where they would read as flawless play. Short runs
    and strategies that bankrupt on day one produce them in quantity.
    """
    points = [(r["occupied_watering_rate"], r["crop_loss_rate"]) for r in rows]
    return [(x, y) for x, y in points if x is not None and y is not None]


def chart_watering_vs_crop_loss(plt, grouped, out_dir, dpi):
    """Small multiples: watering coverage vs. crop loss rate, one scatter
    panel per strategy, each run as a point. Eleven strategies is too many
    distinct hues to tell apart reliably in one overlaid scatter (the
    validated categorical palette caps identity at 8, fewer still once
    points overlap across all pairs), so this facets by strategy on a
    single shared hue instead of color-coding by strategy.

    The x axis is `occupied_watering_rate`, not `watering_rate`: watering an
    empty plot is not a choice the agent gets to make, so coverage of *plot-
    days that had something growing* is the only version of this number that
    relates to crop loss at all. The two fields differ by however much of the
    farm sat idle.
    """
    order = sorted(grouped)
    n = len(order)
    cols = min(4, n)
    rows_n = -(-n // cols)

    points_by_strategy = {s: _watering_loss_points(grouped[s]) for s in order}
    all_points = [p for points in points_by_strategy.values() for p in points]
    # Every run may be undefined (a batch where nothing ever matured), so the
    # limits need a fallback -- max() of an empty sequence raises.
    xlim = (0, max((x for x, _ in all_points), default=0) * 1.05 + 1)
    ylim = (0, max((y for _, y in all_points), default=0) * 1.05 + 1)

    fig, axes = plt.subplots(rows_n, cols, figsize=(3.1 * cols, 2.6 * rows_n), squeeze=False)
    for i, s in enumerate(order):
        ax = axes[i // cols][i % cols]
        points = points_by_strategy[s]
        ax.scatter(
            [x for x, _ in points],
            [y for _, y in points],
            s=18,
            color=SEQUENTIAL_BLUE,
            alpha=0.5,
            edgecolors="none",
            zorder=3,
        )
        ax.set_title(s, fontsize=9, color=INK_PRIMARY, loc="left")
        ax.set_xlim(*xlim)
        ax.set_ylim(*ylim)
        _style_axes(ax, horizontal_grid=False)
        ax.tick_params(labelsize=7)
        if not points:
            # An empty panel is indistinguishable from a strategy that scored
            # zero on both axes. Say which it is.
            ax.text(
                0.5,
                0.5,
                "no run harvested",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=8,
                color=INK_MUTED,
            )
    for i in range(n, rows_n * cols):
        axes[i // cols][i % cols].set_visible(False)

    fig.supxlabel("Watering coverage of occupied plot-days (%)", color=INK_SECONDARY, fontsize=10)
    fig.supylabel("Crop loss rate at harvest (%)", color=INK_SECONDARY, fontsize=10)
    fig.suptitle(
        "Watering coverage vs. crop loss, per strategy (each point = one run)",
        color=INK_PRIMARY,
        fontsize=13,
        x=0.01,
        ha="left",
    )
    fig.tight_layout(rect=(0.02, 0.02, 1, 0.96))
    return _save(fig, out_dir, "watering_vs_crop_loss.png", dpi)


# ---------------------------------------------------------------------------
# Convergence and distribution charts
#
# These read canonical documents (`convergence.json`, `distributions.json`)
# rather than recomputing a statistic from the CSV. That is a hard rule, not a
# convenience: a chart that re-derived its own mean or its own interval could
# disagree with the report beside it, and the reader would have no way to tell
# which one to believe.
# ---------------------------------------------------------------------------


def _load_document(path):
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        document = json.load(f)
    return None if document.get("skipped") else document


def _checkpoint_series(convergence: dict, estimand_id: str, key: str):
    """{strategy: ([runs], [values])} for one estimand and one recorded key."""
    series = {}
    for checkpoint in convergence.get("checkpoints", []):
        runs = checkpoint.get("runs_per_strategy")
        for strategy, entry in checkpoint.get("strategies", {}).items():
            estimate = entry.get("estimates", {}).get(estimand_id)
            if not estimate or estimate.get(key) is None:
                continue
            xs, ys = series.setdefault(strategy, ([], []))
            xs.append(runs)
            ys.append(estimate[key])
    return series


def _line_chart(plt, series, title, subtitle, ylabel, out_dir, dpi, name, bands=None):
    if not series:
        return None
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=SURFACE)
    _style_axes(ax, horizontal_grid=False)
    for index, (strategy, (xs, ys)) in enumerate(sorted(series.items())):
        color = CATEGORICAL[index % len(CATEGORICAL)]
        ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.6, color=color, label=strategy)
        if bands and strategy in bands:
            lower, upper = bands[strategy]
            ax.fill_between(xs, lower, upper, color=color, alpha=0.12, linewidth=0)
    ax.set_xlabel("runs per strategy")
    ax.set_ylabel(ylabel)
    ax.set_title(title, color=INK_PRIMARY, fontsize=13, loc="left", pad=18)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=INK_SECONDARY, fontsize=9, ha="left")
    if len(series) <= 12:
        ax.legend(frameon=False, fontsize=8, ncol=2, labelcolor=INK_SECONDARY)
    fig.tight_layout()
    return _save(fig, out_dir, name, dpi)


def chart_convergence_estimate(plt, convergence, out_dir, dpi):
    return _line_chart(
        plt,
        _checkpoint_series(convergence, "expected_final_money", "estimate"),
        "Expected final money vs runs",
        "Each point is a declared checkpoint; a flat tail means the estimate has settled.",
        "expected final money",
        out_dir,
        dpi,
        "convergence_estimate.png",
    )


def chart_convergence_half_width(plt, convergence, out_dir, dpi):
    return _line_chart(
        plt,
        _checkpoint_series(convergence, "expected_final_money", "half_width"),
        "Interval half-width vs runs",
        "Precision of the estimate (not the spread of outcomes); shrinks with sqrt(n).",
        "half-width",
        out_dir,
        dpi,
        "convergence_half_width.png",
    )


def chart_convergence_bankruptcy(plt, convergence, out_dir, dpi):
    series = _checkpoint_series(convergence, "bankruptcy_probability", "estimate")
    lowers = _checkpoint_series(convergence, "bankruptcy_probability", "lower")
    uppers = _checkpoint_series(convergence, "bankruptcy_probability", "upper")
    bands = {
        strategy: (lowers[strategy][1], uppers[strategy][1])
        for strategy in series
        if strategy in lowers and strategy in uppers
    }
    return _line_chart(
        plt,
        series,
        "Bankruptcy probability vs runs",
        "Shaded band is the Wilson score interval at each checkpoint's own alpha slice.",
        "P(bankrupt)",
        out_dir,
        dpi,
        "convergence_bankruptcy.png",
        bands=bands,
    )


def chart_convergence_quantile(plt, convergence, out_dir, dpi):
    series = {}
    for checkpoint in convergence.get("checkpoints", []):
        runs = checkpoint.get("runs_per_strategy")
        for strategy, entry in checkpoint.get("strategies", {}).items():
            quantile = entry.get("quantile")
            if not quantile or quantile.get("value") is None:
                continue
            xs, ys = series.setdefault(strategy, ([], []))
            xs.append(runs)
            ys.append(quantile["value"])
    return _line_chart(
        plt,
        series,
        "Exact median final money vs runs",
        "Recorded only with --track-quantiles; exact order statistic, not a reservoir estimate.",
        "median final money",
        out_dir,
        dpi,
        "convergence_quantile.png",
    )


def chart_checkpoint_change(plt, convergence, out_dir, dpi):
    return _line_chart(
        plt,
        _checkpoint_series(convergence, "expected_final_money", "change_from_previous"),
        "Checkpoint-to-checkpoint change",
        "Movement of the estimate between successive looks; converging to zero is the signal.",
        "change in estimate",
        out_dir,
        dpi,
        "convergence_change.png",
    )


def chart_final_money_ecdf(plt, distributions_doc, out_dir, dpi):
    """ECDF of final money per strategy, over ALL runs.

    The cohort is named in the subtitle because it matters: this curve
    includes bankrupt runs, and a survivor-only ECDF of the same batch looks
    materially better for exactly the strategies that fail most.
    """
    series = {}
    for strategy, entry in distributions_doc.get("strategies", {}).items():
        curve = entry.get("ecdf", {}).get("final_money_all_runs", {})
        if curve.get("values"):
            series[strategy] = (curve["values"], curve["probabilities"])
    return _line_chart(
        plt,
        series,
        "Final money ECDF",
        "Cohort: all runs, bankrupt and surviving. Exact observations, not a reservoir sample.",
        "F(final money)",
        out_dir,
        dpi,
        "final_money_ecdf.png",
    )


def chart_time_to_bankruptcy(plt, distributions_doc, out_dir, dpi):
    """Kaplan-Meier survival curve, censoring runs that finished solvent."""
    series = {}
    for strategy, entry in distributions_doc.get("strategies", {}).items():
        curve = entry.get("time_to_bankruptcy", {})
        if curve.get("times"):
            series[strategy] = (curve["times"], curve["survival"])
    chart = _line_chart(
        plt,
        series,
        "Time to bankruptcy (Kaplan-Meier)",
        "Runs still solvent at the horizon are censored, not counted as failures.",
        "P(still solvent)",
        out_dir,
        dpi,
        "time_to_bankruptcy.png",
    )
    return chart


def render_all(
    csv_path: str,
    out_dir: str,
    dpi: int,
    show: bool,
    convergence_path: str = None,
    distributions_path: str = None,
) -> list[str]:
    import matplotlib

    if not show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["font.family"] = ["DejaVu Sans", "Arial", "sans-serif"]

    rows, crop_ids = load_runs(csv_path)
    grouped = group_by_strategy(rows)

    paths = [
        chart_final_money_distribution(plt, grouped, out_dir, dpi),
        chart_bankruptcy_rate(plt, grouped, out_dir, dpi),
        chart_avg_profit_per_day(plt, grouped, out_dir, dpi),
        chart_expense_breakdown(plt, grouped, out_dir, dpi),
        chart_crop_mix(plt, grouped, crop_ids, out_dir, dpi),
        chart_contract_completion_rate(plt, grouped, out_dir, dpi),
        chart_watering_vs_crop_loss(plt, grouped, out_dir, dpi),
    ]

    # Optional, and absent by design when the batch did not produce them --
    # an older published run has no convergence.json, and the dashboard must
    # still render the charts it does have rather than failing.
    convergence = _load_document(convergence_path)
    if convergence:
        paths.extend(
            [
                chart_convergence_estimate(plt, convergence, out_dir, dpi),
                chart_convergence_half_width(plt, convergence, out_dir, dpi),
                chart_convergence_bankruptcy(plt, convergence, out_dir, dpi),
                chart_convergence_quantile(plt, convergence, out_dir, dpi),
                chart_checkpoint_change(plt, convergence, out_dir, dpi),
            ]
        )
    distributions_doc = _load_document(distributions_path)
    if distributions_doc:
        paths.extend(
            [
                chart_final_money_ecdf(plt, distributions_doc, out_dir, dpi),
                chart_time_to_bankruptcy(plt, distributions_doc, out_dir, dpi),
            ]
        )
    paths = [p for p in paths if p]

    if show:
        plt.show()
    else:
        plt.close("all")
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help="Path to run_results.csv (default: reports/run_results.csv)",
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT_DIR, help="Output directory for PNGs (default: reports/charts)"
    )
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument(
        "--convergence",
        default=None,
        help="Path to convergence.json, to also render the checkpoint charts.",
    )
    parser.add_argument(
        "--distributions",
        default=None,
        help="Path to distributions.json, to also render the ECDF and survival charts.",
    )
    parser.add_argument("--show", action="store_true", help="Also display each chart in a window")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    written = render_all(
        args.csv, args.out, args.dpi, args.show, args.convergence, args.distributions
    )
    print(f"Wrote {len(written)} chart(s) to {args.out}:")
    for p in written:
        print(f"  {p}")
