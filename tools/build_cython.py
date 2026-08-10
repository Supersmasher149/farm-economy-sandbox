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

The build is transactional. Everything is compiled into a sibling staging
directory, the manifest is written last, the staged build is loaded in a
subprocess through the real FARM_COMPILED path, and only then is it swapped
into place. A failed or interrupted build therefore leaves the previous
working build untouched rather than deleting it and stopping halfway. The swap
is two renames rather than one, so there is a sub-millisecond window in which
the tag directory does not exist; a concurrent import lands on pure Python
with a warning, which is the correct direction to fail.

The Cython directives are defined in simulation/_compiled.py -- the module
that verifies them at load time -- and hashed into the manifest, so artifacts
built with different ones are rejected instead of trusted. They are
load-bearing for bit-exact replay, not tuning knobs:

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
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import _ccompile

REPO_ROOT = Path(__file__).resolve().parent.parent

_compiled = _ccompile.compiled_helper()

DIRECTIVES = _compiled.DIRECTIVES

STAGING_PREFIX = ".incoming-"
REPLACED_PREFIX = ".replaced-"


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


def _sweep_stale_staging(root: Path) -> None:
    """Remove staging/replaced directories left behind by a killed build.

    They are inert -- nothing reads them, because only the tag directory is
    ever loaded from -- but a crashed build should not leave the tree growing
    a copy of itself on every retry.
    """
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if path.is_dir() and path.name.startswith((STAGING_PREFIX, REPLACED_PREFIX)):
            shutil.rmtree(path, ignore_errors=True)


def _verify_staged_build(staging: Path, modules: dict) -> bool:
    """Import every staged artifact in a subprocess, through the real loader.

    Not `import x; assert it worked` in this process: the builder must not end
    up running the code it just produced, and an extension module cannot be
    unloaded once imported. FARM_COMPILED=strict makes the shim raise rather
    than fall back, so a manifest this build wrote but the loader rejects
    fails here instead of silently degrading for the next person to run it.
    """
    script = (
        "import importlib, os, sys\n"
        "from simulation import _compiled\n"
        "names = sys.argv[1:]\n"
        "for name in names:\n"
        "    importlib.import_module(name)\n"
        "if not _compiled.active():\n"
        "    raise SystemExit('the compiled finder did not install')\n"
        "staged = os.path.realpath(os.environ['FARM_COMPILED_DIR'])\n"
        "wrong = [n for n in names\n"
        "         if not os.path.realpath(sys.modules[n].__file__).startswith(staged)]\n"
        "if wrong:\n"
        "    raise SystemExit('loaded from outside the staged build: ' + ', '.join(wrong))\n"
    )
    environment = dict(os.environ, FARM_COMPILED="strict", FARM_COMPILED_DIR=str(staging))
    result = subprocess.run(
        [sys.executable, "-c", script, *sorted(modules)],
        cwd=REPO_ROOT,
        env=environment,
    )
    if result.returncode != 0:
        print(
            "\nThe staged build did not load; leaving the previous build in place.", file=sys.stderr
        )
        return False
    return True


def _publish(staging: Path, out_dir: Path) -> None:
    """Swap the staged build in, keeping the old one until the swap succeeds.

    `os.replace` cannot overwrite a non-empty directory, so the live build is
    renamed aside first. If the second rename fails the first is undone, so
    the outcome is always one whole build -- never a mix of two.
    """
    replaced = out_dir.parent / f"{REPLACED_PREFIX}{out_dir.name}-{os.getpid()}"
    moved_aside = out_dir.exists()
    if moved_aside:
        os.replace(out_dir, replaced)
    try:
        os.replace(staging, out_dir)
    except OSError:
        if moved_aside:
            os.replace(replaced, out_dir)
        raise
    if moved_aside:
        shutil.rmtree(replaced, ignore_errors=True)


def _compile_into(staging: Path, work_dir: Path, sources: dict) -> dict | None:
    """Cython + cc every source into `staging`. None on the first failure."""
    suffix = _ccompile.ext_suffix()
    modules = {}
    for fullname, source in sources.items():
        package, _, stem = fullname.rpartition(".")
        c_file = work_dir / f"{package}_{stem}.c"
        artifact = staging / package / (stem + suffix)
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
            return None

        # No -Wall: this is machine-generated C and the warnings are not
        # actionable. The risk of printing thousands of them is that someone
        # "tidies" the flag list and takes -ffp-contract=off along with it.
        cc_command = _ccompile.compile_command(str(c_file), str(artifact))
        _ccompile.assert_required_flags(cc_command)
        if subprocess.run(cc_command, cwd=REPO_ROOT).returncode != 0:
            print(f"\nC compilation failed on {fullname}.", file=sys.stderr)
            return None

        modules[fullname] = {
            "source": str(source.relative_to(REPO_ROOT)),
            "source_sha256": _compiled.source_hash(source),
            "artifact": str(artifact.relative_to(staging)),
        }
        print(f"  {fullname}")
    return modules


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
    _sweep_stale_staging(out_dir.parent)
    # A partial rebuild must not leave a manifest describing modules that are
    # no longer there, so the whole tag directory is replaced -- but only once
    # the replacement is complete and verified, never before.
    staging = out_dir.parent / f"{STAGING_PREFIX}{out_dir.name}-{os.getpid()}"
    work_dir = staging / "_c"
    work_dir.mkdir(parents=True)
    try:
        modules = _compile_into(staging, work_dir, sources)
        if modules is None:
            return 1

        # Written last: the manifest is what makes a directory loadable, so
        # until it exists there is nothing for the loader to half-trust.
        suffix = _ccompile.ext_suffix()
        recipe = _compiled.build_recipe()
        manifest = {
            "manifest_version": _compiled.MANIFEST_VERSION,
            "build_tag": _compiled.build_tag(),
            "build_recipe": recipe,
            "build_recipe_sha256": _compiled.recipe_hash(recipe),
            "provenance": {
                "python": platform.python_version(),
                "platform": platform.platform(terse=True),
                "cython": _cython_version(),
                "cflags": _ccompile.compile_command("SOURCE.c", "TARGET" + suffix),
            },
            "modules": modules,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        shutil.rmtree(work_dir)

        if not _verify_staged_build(staging, modules):
            return 1
        _publish(staging, out_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

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
