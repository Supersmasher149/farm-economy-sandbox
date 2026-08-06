#!/usr/bin/env python3
"""Extract/diff the ## Warnings section of a farm-economy-sandbox
summary_report.md, so the balance-check workflow doesn't need to re-read the
whole report by hand on every iteration.

Usage:
    python3 report_diff.py warnings <report.md>
    python3 report_diff.py diff <old_report.md> <new_report.md>

See ../SKILL.md for when/why to run this.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SECTION_HEADING = "## Warnings"


def _extract_warnings(report_text: str) -> list[str]:
    lines = report_text.splitlines()
    try:
        start = lines.index(SECTION_HEADING)
    except ValueError:
        raise ValueError(f"No {SECTION_HEADING!r} section found in report.") from None

    warnings = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- "):
            warnings.append(re.sub(r"^- (?:⚠️ )?", "", stripped))
    return [w for w in warnings if w and w != "No balance warnings triggered."]


def cmd_warnings(args: argparse.Namespace) -> int:
    text = Path(args.report).read_text()
    warnings = _extract_warnings(text)
    if not warnings:
        print("No balance warnings triggered.")
        return 0
    print(f"{len(warnings)} warning(s):\n")
    for w in warnings:
        print(f"  - {w}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    old_warnings = _extract_warnings(Path(args.old_report).read_text())
    new_warnings = _extract_warnings(Path(args.new_report).read_text())

    old_set, new_set = set(old_warnings), set(new_warnings)
    added = [w for w in new_warnings if w not in old_set]
    removed = [w for w in old_warnings if w not in new_set]

    if not added and not removed:
        print("No change in warnings between the two reports.")
        return 0

    if removed:
        print(f"Resolved ({len(removed)}):")
        for w in removed:
            print(f"  - {w}")
    if added:
        print(f"New ({len(added)}):")
        for w in added:
            print(f"  - {w}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_warnings = subparsers.add_parser(
        "warnings", help="Print the ## Warnings section of a report."
    )
    p_warnings.add_argument("report", help="Path to a summary_report.md")

    p_diff = subparsers.add_parser("diff", help="Diff warnings between two reports.")
    p_diff.add_argument("old_report", help="Path to the baseline summary_report.md")
    p_diff.add_argument("new_report", help="Path to the updated summary_report.md")

    args = parser.parse_args()
    if args.command == "warnings":
        return cmd_warnings(args)
    return cmd_diff(args)


if __name__ == "__main__":
    raise SystemExit(main())
