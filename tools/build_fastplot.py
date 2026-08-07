#!/usr/bin/env python3
"""Build the optional C accelerator, `simulation._fastplot`.

    python3 tools/build_fastplot.py          # build in place
    python3 tools/build_fastplot.py --clean  # remove the built module

The simulator runs fine without this. `simulation/weather.py` falls back to
the pure-Python plot loop -- which remains the reference implementation --
whenever the module is missing, so the README's "no third-party dependencies
to run the simulator" promise still holds. Building it only replaces the
per-plot daily physics with a compiled equivalent.

The compiler invocation and the load-bearing float flags live in
`tools/_ccompile.py`, shared with `tools/build_cython.py` so the two optional
accelerators cannot drift apart on float semantics. Run the replay-guard skill
after building.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import _ccompile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO_ROOT, "simulation", "_fastplotmodule.c")
MODULE_NAME = "_fastplot"


def output_path() -> str:
    return os.path.join(REPO_ROOT, "simulation", MODULE_NAME + _ccompile.ext_suffix())


def clean() -> int:
    target = output_path()
    if os.path.exists(target):
        os.remove(target)
        print(f"Removed {os.path.relpath(target, REPO_ROOT)}")
    else:
        print("Nothing to remove.")
    return 0


def build() -> int:
    target = output_path()
    # -Wall stays for this one: it is hand-written C, so warnings are
    # actionable. tools/build_cython.py deliberately omits it.
    command = _ccompile.compile_command(SOURCE, target, extra_flags=["-Wall"])
    _ccompile.assert_required_flags(command)

    print(" ".join(command))
    result = subprocess.run(command, cwd=REPO_ROOT)
    if result.returncode != 0:
        print("\nBuild failed. The simulator still runs -- it will use the", file=sys.stderr)
        print("pure-Python plot loop instead.", file=sys.stderr)
        return result.returncode

    print(f"\nBuilt {os.path.relpath(target, REPO_ROOT)}")
    print("Verify before trusting it:")
    print("  python3 -m pytest tests/test_fastplot_equivalence.py")
    print("  python3 .claude/skills/replay-guard/scripts/golden_replay.py check")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clean", action="store_true", help="remove the built module")
    args = parser.parse_args()
    return clean() if args.clean else build()


if __name__ == "__main__":
    raise SystemExit(main())
