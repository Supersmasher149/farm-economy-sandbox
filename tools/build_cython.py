#!/usr/bin/env python3
"""Build the optional Cython accelerator.

    python3 tools/build_cython.py            # build every eligible module
    python3 tools/build_cython.py --clean    # remove the built artifacts
    python3 tools/build_cython.py --modules simulation.markets agents.profit_optimizer

Then run with it:

    FARM_COMPILED=1 python3 main.py batch --runs 1000

The simulator runs fine without this, and ignores it unless FARM_COMPILED is
set. Measured ~1.17x on a 1000-run batch with `simulation/` and `agents/`
compiled; the pure-Python modules remain the reference implementation.

Requires Cython (`pip install cython`), which is a *build-time* dependency
only -- nothing at runtime imports it, and the README's "no third-party
dependencies to run the simulator" promise is unaffected.

Artifacts go to build/compiled/<tag>/, never next to the sources: an
extension module sitting beside its own .py silently shadows it, which makes
"I edited the file and nothing changed" an invisible failure. The manifest
records a SHA-256 of every source so an edited-but-not-rebuilt module is
caught at import instead. See simulation/_compiled.py.

The Cython directives below are load-bearing for bit-exact replay, not
tuning knobs:

  annotation_typing=False  This codebase is heavily annotated (`amount: float`,
      `-> float`). Cython 3 defaults this to True and would read those as C
      doubles, coercing arguments and return values -- which can turn a
      returned `0` into `0.0`, changing its repr(), which feeds
      PlayerState.decision_random and therefore changes agent decisions.
  infer_types=False  The default ("safe") still infers C double for float-only
      locals. IEEE arithmetic makes that *probably* harmless given
      -ffp-contract=off, but probably is not the bar for replay.
  cdivision=False  True switches % and // to C truncation for negatives and
      drops ZeroDivisionError.
  c_api_binop_methods=False  Already the Cython 3 default; pinned so a future
      default flip cannot reintroduce 0.x swapped-operand semantics.
  binding=True  simulation/engine.py dispatches on *args arity and several
      functions rely on __defaults__.

Verified against these directives: sum() keeps CPython's Neumaier compensated
summation, two-arg min/max keep tie-goes-to-first-arg and +0.0/-0.0, and
round() stays half-to-even. Re-check with tests/test_compiled_shim.py and the
replay guard after changing any of them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import _ccompile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_compiled_helper():
    """Load simulation/_compiled.py without importing the `simulation`
    package, whose __init__ would activate the shim -- the builder must not
    end up running against the artifacts it is in the middle of replacing."""
    spec = importlib.util.spec_from_file_location(
        "_compiled_helper", REPO_ROOT / "simulation" / "_compiled.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_compiled = _load_compiled_helper()

DIRECTIVES = {
    "language_level": "3",
    "annotation_typing": "False",
    "infer_types": "False",
    "cdivision": "False",
    "c_api_binop_methods": "False",
    "binding": "True",
}


def _directive_argument() -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(DIRECTIVES.items()))


def clean() -> int:
    target = _compiled.BUILD_ROOT
    if target.exists():
        shutil.rmtree(target)
        print(f"Removed {target.relative_to(REPO_ROOT)}")
    else:
        print("Nothing to remove.")
    return 0


def build(selected: list[str] | None) -> int:
    if shutil.which("cython") is None:
        print("cython not found. Install it with: pip install cython", file=sys.stderr)
        print("The simulator runs fine without this accelerator.", file=sys.stderr)
        return 1

    sources = _compiled.compilable_sources()
    if selected:
        unknown = sorted(set(selected) - set(sources))
        if unknown:
            print(f"Unknown module(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(sources))}", file=sys.stderr)
            return 2
        sources = {name: sources[name] for name in selected}

    out_dir = _compiled.artifact_dir()
    work_dir = out_dir / "_c"
    # A partial rebuild must not leave a manifest describing modules that are
    # no longer there, so the whole tag directory is rebuilt from scratch.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    work_dir.mkdir(parents=True)

    suffix = _ccompile.ext_suffix()
    modules = {}
    for fullname, source in sources.items():
        package, _, stem = fullname.rpartition(".")
        c_file = work_dir / f"{package}_{stem}.c"
        artifact = out_dir / package / (stem + suffix)
        artifact.parent.mkdir(parents=True, exist_ok=True)

        cython_command = [
            "cython",
            "-3",
            "-X",
            _directive_argument(),
            "--module-name",
            fullname,
            str(source),
            "-o",
            str(c_file),
        ]
        if subprocess.run(cython_command, cwd=REPO_ROOT).returncode != 0:
            print(f"\ncython failed on {fullname}.", file=sys.stderr)
            return 1

        # No -Wall: this is machine-generated C and the warnings are not
        # actionable. The risk of printing thousands of them is that someone
        # "tidies" the flag list and takes -ffp-contract=off along with it.
        cc_command = _ccompile.compile_command(str(c_file), str(artifact))
        _ccompile.assert_required_flags(cc_command)
        if subprocess.run(cc_command, cwd=REPO_ROOT).returncode != 0:
            print(f"\nC compilation failed on {fullname}.", file=sys.stderr)
            return 1

        modules[fullname] = {
            "source": str(source.relative_to(REPO_ROOT)),
            "source_sha256": _compiled.source_hash(source),
            "artifact": str(artifact.relative_to(out_dir)),
        }
        print(f"  {fullname}")

    manifest = {
        "manifest_version": _compiled.MANIFEST_VERSION,
        "build_tag": _compiled.build_tag(),
        "ext_suffix": suffix,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "cython": _cython_version(),
        "directives": DIRECTIVES,
        "cflags": _ccompile.compile_command("SOURCE.c", "TARGET" + suffix),
        "modules": modules,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    shutil.rmtree(work_dir)

    print(f"\nBuilt {len(modules)} modules -> {out_dir.relative_to(REPO_ROOT)}")
    print("Nothing is used until you ask for it:")
    print("  FARM_COMPILED=1 python3 main.py batch --runs 1000")
    print("Verify before trusting it:")
    print("  FARM_COMPILED=strict python3 -m pytest")
    print(
        "  FARM_COMPILED=strict python3 .claude/skills/replay-guard/scripts/golden_replay.py check"
    )
    return 0


def _cython_version() -> str:
    try:
        import Cython

        return Cython.__version__
    except ImportError:  # pragma: no cover - build already checked for the CLI
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--clean", action="store_true", help="remove the built artifacts")
    parser.add_argument(
        "--modules",
        nargs="+",
        metavar="NAME",
        help="build only these modules (default: every module in simulation/ and agents/)",
    )
    args = parser.parse_args()
    if args.clean:
        return clean()
    return build(args.modules)


if __name__ == "__main__":
    raise SystemExit(main())
