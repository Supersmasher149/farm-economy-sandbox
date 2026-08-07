#!/usr/bin/env python3
"""Build the optional C accelerator, `simulation._fastplot`.

    python3 tools/build_fastplot.py          # build in place
    python3 tools/build_fastplot.py --clean  # remove the built module

The simulator runs fine without this. `simulation/weather.py` falls back to
the pure-Python plot loop -- which remains the reference implementation --
whenever the module is missing, so the README's "no third-party dependencies
to run the simulator" promise still holds. Building it only replaces the
per-plot daily physics with a compiled equivalent.

This invokes the C compiler directly via `sysconfig` rather than going
through setuptools, because setuptools is not a dependency of this project
and adding one just to build an *optional* accelerator would defeat the
point. Only the C compiler and the CPython headers are required, both of
which ship with any normal CPython install.

The flags are load-bearing, not incidental: `-ffp-contract=off` stops the
compiler contracting `a * b + c` into an FMA and `-fno-fast-math` stops it
reassociating floating point. Either changes results in the last bits, which
breaks bit-exact seed replay. See the header of
simulation/_fastplotmodule.c and run the replay-guard skill after building.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO_ROOT, "simulation", "_fastplotmodule.c")
MODULE_NAME = "_fastplot"

REQUIRED_FLAGS = ["-ffp-contract=off", "-fno-fast-math"]


def output_path() -> str:
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    return os.path.join(REPO_ROOT, "simulation", MODULE_NAME + suffix)


def clean() -> int:
    target = output_path()
    if os.path.exists(target):
        os.remove(target)
        print(f"Removed {os.path.relpath(target, REPO_ROOT)}")
    else:
        print("Nothing to remove.")
    return 0


def build() -> int:
    include = sysconfig.get_paths()["include"]
    target = output_path()
    compiler = os.environ.get("CC") or sysconfig.get_config_var("CC") or "cc"

    command = [*compiler.split(), "-O2", *REQUIRED_FLAGS, "-Wall", "-shared"]
    if sys.platform == "darwin":
        # Extension modules resolve CPython symbols from the interpreter that
        # loads them rather than linking against libpython.
        command += ["-undefined", "dynamic_lookup"]
    command += ["-fPIC", f"-I{include}", SOURCE, "-o", target]

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
