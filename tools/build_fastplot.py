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

The build is transactional: it compiles to a sibling temp path, loads it and
checks its PROFILE_LAYOUT against `simulation.derived`, and only then renames
it over the live module. Compiling straight to the final path -- which this
used to do -- means a failed compile can leave a truncated .so that
`simulation/weather.py` then tries to import, and a mid-build run can load a
half-written file. A rename within one directory is atomic, so a reader sees
either the old module or the new one.
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
STAGING_SUFFIX = ".incoming"


def output_path() -> str:
    return os.path.join(REPO_ROOT, "simulation", MODULE_NAME + _ccompile.ext_suffix())


def _sweep_stale_staging() -> None:
    """Drop leftovers from a killed build. They cannot be imported -- the
    suffix is not an extension suffix -- but they should not accumulate."""
    directory = os.path.join(REPO_ROOT, "simulation")
    for name in sorted(os.listdir(directory)):
        if name.startswith(MODULE_NAME) and STAGING_SUFFIX in name:
            os.remove(os.path.join(directory, name))


def _verify(staged: str) -> bool:
    """Load the staged module in a subprocess and check it is the right shape.

    In a subprocess because an extension module cannot be unloaded once
    imported, and because `simulation.weather` may already be holding the
    module this build is about to replace. PROFILE_LAYOUT is the same guard
    weather.py applies at import -- checking it here turns "the accelerator
    silently stopped being used" into a build failure.
    """
    script = (
        "import sys\n"
        "from importlib.machinery import ExtensionFileLoader\n"
        "from importlib.util import module_from_spec, spec_from_file_location\n"
        "from simulation import derived\n"
        # The spec name has to be the real module name: CPython derives the
        # init symbol it looks for (PyInit__fastplot) from it.
        "spec = spec_from_file_location(\n"
        "    '_fastplot', sys.argv[1],\n"
        "    loader=ExtensionFileLoader('_fastplot', sys.argv[1]))\n"
        "module = module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "if getattr(module, 'PROFILE_LAYOUT', None) != derived.PROFILE_LAYOUT:\n"
        "    raise SystemExit(\n"
        "        'PROFILE_LAYOUT is %r but simulation.derived expects %r'\n"
        "        % (getattr(module, 'PROFILE_LAYOUT', None), derived.PROFILE_LAYOUT))\n"
        "if not callable(getattr(module, 'apply_day', None)):\n"
        "    raise SystemExit('the built module has no apply_day()')\n"
    )
    return subprocess.run([sys.executable, "-c", script, staged], cwd=REPO_ROOT).returncode == 0


def clean() -> int:
    _sweep_stale_staging()
    target = output_path()
    if os.path.exists(target):
        os.remove(target)
        print(f"Removed {os.path.relpath(target, REPO_ROOT)}")
    else:
        print("Nothing to remove.")
    return 0


def build() -> int:
    _sweep_stale_staging()
    target = output_path()
    staged = f"{target}{STAGING_SUFFIX}-{os.getpid()}"
    # -Wall stays for this one: it is hand-written C, so warnings are
    # actionable. tools/build_cython.py deliberately omits it.
    command = _ccompile.compile_command(SOURCE, staged, extra_flags=["-Wall"])
    _ccompile.assert_required_flags(command)

    print(" ".join(command))
    try:
        result = subprocess.run(command, cwd=REPO_ROOT)
        if result.returncode != 0:
            print("\nBuild failed. The simulator still runs -- it will use the", file=sys.stderr)
            print("pure-Python plot loop instead.", file=sys.stderr)
            return result.returncode

        if not _verify(staged):
            print("\nThe built module did not verify; leaving the previous one in", file=sys.stderr)
            print("place. Rebuild after checking simulation/_fastplotmodule.c.", file=sys.stderr)
            return 1

        os.replace(staged, target)
    finally:
        if os.path.exists(staged):
            os.remove(staged)

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
