"""Stdlib-only CLI viewer over published batch reports.

`summary_report.md` is written for a human reading start-to-finish (one
section per strategy, ~500 lines for the full roster); this module is for
the "just tell me the 3 numbers, sorted" and "what changed between these two
batches" questions that a markdown wall doesn't answer quickly. It reads
`summary.json` (the aggregator's own dict, written verbatim by
`main.py batch` -- see `ARTIFACT_NAMES`), not the CSV or the markdown, so
there is exactly one source of truth for what a number *is* and this module
only decides how to lay it out.

No third-party dependencies, matching the rest of the simulator -- this is
meant for a fast glance in the same terminal a batch just ran in, not a
separate charting step (see metrics/dashboard.py for that).
"""

import json
import os

# Curated "at a glance" default -- the three numbers CLAUDE.md's own
# balance-testing workflow leads with: survivability, downside risk, pace.
DEFAULT_FIELDS = ("avg_final_money", "bankruptcy_rate", "avg_profit_per_day")

FIELD_LABELS = {
    "num_runs": "runs",
    "avg_final_money": "final_money",
    "median_final_money": "median_money",
    "min_final_money": "min_money",
    "max_final_money": "max_money",
    "surviving_runs": "survivors",
    "bankrupt_runs": "bankrupt_n",
    "bankruptcy_rate": "bankrupt%",
    "avg_bankruptcy_day": "bankrupt_day",
    "avg_profit_per_day": "profit/day",
    "avg_watering_rate": "water%",
    "avg_occupied_watering_rate": "occ_water%",
    "avg_crop_loss_rate": "crop_loss%",
    "avg_fertilizer_applications": "fert_apps",
    "avg_spoiled_units": "spoiled",
    "avg_processed_units": "processed",
    "avg_contracts_completed": "contracts_ok",
    "avg_contracts_failed": "contracts_fail",
    "avg_final_reputation": "reputation",
    "avg_total_costs": "total_costs",
    "avg_gross_profit": "gross_profit",
    "avg_operating_profit": "op_profit",
    "avg_net_cash_change": "net_cash",
    "first_upgrade_rate": "upgrade1%",
    "second_upgrade_rate": "upgrade2%",
    "avg_first_upgrade_day": "upgrade1_day",
    "avg_second_upgrade_day": "upgrade2_day",
}

RUNS_DIRNAME = "runs"
LATEST_LINK = "latest"


class ViewError(SystemExit):
    """Raised for CLI-facing problems (bad ref, missing artifact) -- a
    SystemExit subclass so `main.py` can let it propagate and print cleanly
    without a traceback, same as argparse's own errors.
    """


def list_runs(reports_dir: str) -> list[str]:
    """Published run ids, oldest first. Lexicographic == chronological:
    run ids are `<%Y%m%dT%H%M%S>-<suffix>` (main.py:_publish_report_artifacts),
    the same ordering `_sweep_old_runs` already relies on.
    """
    runs_dir = os.path.join(reports_dir, RUNS_DIRNAME)
    try:
        entries = os.listdir(runs_dir)
    except FileNotFoundError:
        return []
    return sorted(name for name in entries if os.path.isdir(os.path.join(runs_dir, name)))


def resolve_run_dir(reports_dir: str, ref: str | None) -> str:
    """Resolve a run reference to a published run directory.

    Accepts `None`/"latest" (the most recent batch), "latest-N" (N batches
    back), a bare run id, or a path to a run directory -- so `--diff
    latest-1 latest` reads naturally as "compare the previous batch to this
    one," the exact comparison CLAUDE.md's balance-testing workflow asks for.
    """
    runs_dir = os.path.join(reports_dir, RUNS_DIRNAME)

    if ref is None or ref == "latest":
        link_path = os.path.join(reports_dir, LATEST_LINK)
        if not os.path.exists(link_path):
            raise ViewError(
                f"No published runs found in {reports_dir}. Run `python3 main.py batch` first."
            )
        return os.path.realpath(link_path)

    if ref.startswith("latest-"):
        suffix = ref[len("latest-") :]
        if not suffix.isdigit():
            raise ViewError(
                f"Invalid run reference {ref!r}: expected 'latest', 'latest-N', or a run id "
                "(see --list)."
            )
        back = int(suffix)
        run_ids = list_runs(reports_dir)
        if back < 0 or back >= len(run_ids):
            raise ViewError(
                f"Only {len(run_ids)} published run(s) available; {ref!r} is out of range. "
                "Use --list to see them."
            )
        return os.path.join(runs_dir, run_ids[-1 - back])

    candidate = os.path.join(runs_dir, ref)
    if os.path.isdir(candidate):
        return candidate
    if os.path.isdir(ref):
        return ref
    raise ViewError(f"No such run: {ref!r}. Use --list to see available runs.")


def load_run(run_dir: str) -> dict:
    """Load `summary.json` from a published run directory.

    That file exists on every batch published after this feature landed;
    older retained runs (main.py keeps the last RUNS_RETAINED) may predate
    it, so this fails with a clear pointer rather than a raw KeyError deep
    in a table renderer.
    """
    path = os.path.join(run_dir, "summary.json")
    if not os.path.exists(path):
        raise ViewError(
            f"{path} not found -- this run was published before `summary.json` existed. "
            "Re-run `python3 main.py batch` to get a run this viewer can read."
        )
    with open(path) as f:
        return json.load(f)


def _field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


def _fmt_cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return ", ".join(f"{k}={v}" for k, v in sorted(value.items()))
    return str(value)


def scalar_fields(strategies: dict) -> list[str]:
    """Every field that has a plain (non-dict) value for at least one
    strategy, in the summary's own key order -- what `--fields all` expands
    to, and what a bad `--sort`/`--fields` name is checked against.
    """
    seen = []
    for stats in strategies.values():
        for key, value in stats.items():
            if not isinstance(value, dict) and key not in seen:
                seen.append(key)
    return seen


def _render_table_rows(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    lines.append("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        cells = [row[0].ljust(widths[0])] + [row[i].rjust(widths[i]) for i in range(1, len(row))]
        lines.append("  ".join(cells))
    return "\n".join(lines)


def render_table(
    strategies: dict,
    fields: list[str] = None,
    sort_by: str = None,
    ascending: bool = False,
    top: int = None,
    strategy_filter: list[str] = None,
) -> str:
    """Render a strategy-by-field comparison table.

    `sort_by` defaults to the first requested field. Strategies missing a
    value for the sort field sort last regardless of direction, rather than
    landing at either extreme as if they scored 0 -- an undefined ratio
    (e.g. avg_crop_loss_rate with no harvest events) is not a real 0.
    """
    fields = list(fields) if fields else list(DEFAULT_FIELDS)
    available = scalar_fields(strategies)
    unknown = [f for f in fields if f not in available]
    if unknown:
        raise ViewError(f"Unknown field(s): {unknown}. Available: {', '.join(sorted(available))}")

    names = list(strategies)
    if strategy_filter:
        missing = [s for s in strategy_filter if s not in strategies]
        if missing:
            raise ViewError(
                f"Unknown strategy/strategies: {missing}. Known: {', '.join(sorted(names))}"
            )
        names = [s for s in names if s in strategy_filter]

    sort_field = sort_by or fields[0]
    if sort_field not in available:
        raise ViewError(
            f"Unknown --sort field: {sort_field!r}. Available: {', '.join(sorted(available))}"
        )

    defined_names = [n for n in names if strategies[n].get(sort_field) is not None]
    missing_names = [n for n in names if strategies[n].get(sort_field) is None]
    defined_names.sort(key=lambda n: strategies[n][sort_field], reverse=not ascending)
    names = defined_names + missing_names

    if top is not None:
        names = names[:top]

    headers = ["strategy"] + [_field_label(f) for f in fields]
    rows = [[name] + [_fmt_cell(strategies[name].get(f)) for f in fields] for name in names]
    return _render_table_rows(headers, rows)


def render_diff(
    label_a: str,
    strategies_a: dict,
    label_b: str,
    strategies_b: dict,
    fields: list[str] = None,
    only_changed: bool = False,
) -> str:
    """Render a before/after table per field, one row per strategy, sorted
    by the size of the change -- built for CLAUDE.md's balance-testing
    workflow step 4 ("re-run with the same seed to isolate the effect").
    """
    fields = list(fields) if fields else list(DEFAULT_FIELDS)
    common = sorted(set(strategies_a) & set(strategies_b))
    only_a = sorted(set(strategies_a) - set(strategies_b))
    only_b = sorted(set(strategies_b) - set(strategies_a))

    sections = []
    if only_a or only_b:
        note = []
        if only_a:
            note.append(f"only in {label_a}: {', '.join(only_a)}")
        if only_b:
            note.append(f"only in {label_b}: {', '.join(only_b)}")
        sections.append("(" + "; ".join(note) + ")")

    for field in fields:
        rows = []
        for name in common:
            va = strategies_a[name].get(field)
            vb = strategies_b[name].get(field)
            numeric = isinstance(va, (int, float)) and isinstance(vb, (int, float))
            delta = vb - va if numeric else None
            if only_changed and (delta is None or delta == 0):
                continue
            rows.append((name, va, vb, delta))
        rows.sort(key=lambda r: abs(r[3]) if r[3] is not None else -1, reverse=True)
        headers = ["strategy", label_a, label_b, "delta"]
        table_rows = [
            [name, _fmt_cell(va), _fmt_cell(vb), _fmt_cell(delta)] for name, va, vb, delta in rows
        ]
        body = _render_table_rows(headers, table_rows) if table_rows else "  (no change)"
        sections.append(f"== {_field_label(field)} ==\n{body}")

    return "\n\n".join(sections)


def render_warnings(warnings: list[str]) -> str:
    if not warnings:
        return "No balance warnings."
    return "\n".join(f"⚠ {w}" for w in warnings)
