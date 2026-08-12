#!/usr/bin/env python3
"""Run main.py's CLI with REPORTS_DIR redirected to reports/_scratch/.

main.py has no `--output-dir` flag -- REPORTS_DIR is a module-level constant
resolved relative to main.py's own location (see main.py's comment on why:
config/report paths are checkout-relative on purpose). This wrapper does not
touch main.py; it imports the module and overrides that one attribute before
dispatching, the same monkeypatch tests/test_reporting_issues.py already uses
to keep tests off the real reports/ tree. Every function in main.py reads
REPORTS_DIR from the module namespace at call time, so the override applies
to whatever subcommand is dispatched.

Used by `make sim`, `make charts`, and `make balance` so ad hoc / scratch
runs can never disturb reports/latest, evict a retained run, or otherwise
touch a real batch's output -- see CLAUDE.md's "Simulation Output Safety".

Usage: same arguments as main.py itself, e.g.
    python3 tools/scratch_run.py batch --runs 100 --seed 42 --no-charts
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_DIR = os.path.join(BASE_DIR, "reports", "_scratch")

sys.path.insert(0, BASE_DIR)
import main as main_module  # noqa: E402


def run(argv):
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    main_module.REPORTS_DIR = SCRATCH_DIR
    parser = main_module.build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    run(sys.argv[1:])
