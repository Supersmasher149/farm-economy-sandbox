"""Opt-in loader for the optional Cython-compiled modules.

The simulator runs pure Python by default. Setting FARM_COMPILED=1 makes it
load the artifacts built by `python3 tools/build_cython.py` instead, which
measured ~1.17x on a 1000-run batch. The `.py` files stay the reference
implementation either way.

Two decisions here are deliberate and worth not undoing:

**Out of tree, not in place.** Cython can emit `simulation/markets<EXT>` right
next to `simulation/markets.py`, and the import system would silently prefer
it -- EXTENSION_SUFFIXES is checked before SOURCE_SUFFIXES. That gives you no
way to tell which one you are running, and edits to the `.py` appear to do
nothing. So artifacts go under build/compiled/<tag>/ and are reached only
through the finder installed below.

**Opt in, not opt out.** The failure mode of this module must be "you get the
reference implementation". A missing manifest, a stale hash, an unreadable
artifact, a bug in this file, or a plain `git clone` all land on pure Python.
The alternative -- shadowing by default with a guard that hides it -- fails
the other way, and silently running code you did not ask for is exactly the
class of bug that bit-exact replay cannot absorb.

FARM_COMPILED values:
    unset / "" / "0"   pure Python, this module does nothing
    "1"                use artifacts if they verify; warn once and fall back
    "strict"           use artifacts, or raise -- for CI

See CLAUDE.md's Performance section and .claude/skills/replay-guard.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import sysconfig
from importlib.machinery import ExtensionFileLoader
from importlib.util import spec_from_file_location
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = REPO_ROOT / "build" / "compiled"

# Bumped when the manifest's shape changes, so an artifact directory written
# by an older builder is rejected rather than misread. Mirrors the
# PROFILE_LAYOUT guard in simulation/_fastplotmodule.c.
MANIFEST_VERSION = 1

# Packages whose submodules may be compiled. Their __init__.py files and this
# module are never compiled -- this one has to exist as source to run at all.
PACKAGES = ("simulation", "agents")
NEVER_COMPILE = {"__init__", "_compiled"}

_activated = False


def build_tag() -> str:
    """Identifies an ABI + platform. Artifacts for one tag never load under
    another, so switching interpreters cannot pick up a wrong-ABI build."""
    suffix = (sysconfig.get_config_var("EXT_SUFFIX") or ".so").lstrip(".")
    return suffix.removesuffix(".so").removesuffix(".pyd") or "default"


def artifact_dir() -> Path:
    return BUILD_ROOT / build_tag()


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display(path: Path) -> str:
    """Repo-relative when possible. Never raises -- this is only ever called
    while building an error message, so it must not become the error."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def compilable_sources() -> dict[str, Path]:
    """Module fullname -> source path, for every module a build may compile."""
    sources = {}
    for package in PACKAGES:
        for path in sorted((REPO_ROOT / package).glob("*.py")):
            if path.stem in NEVER_COMPILE:
                continue
            sources[f"{package}.{path.stem}"] = path
    return sources


def _verify(manifest: dict) -> tuple[dict[str, Path], list[str]]:
    """Return (loadable modules, reasons it is not fully usable)."""
    problems = []
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        return {}, [
            f"manifest_version {manifest.get('manifest_version')!r} != {MANIFEST_VERSION} "
            "(rebuild with tools/build_cython.py)"
        ]
    if manifest.get("build_tag") != build_tag():
        return {}, [f"built for {manifest.get('build_tag')!r}, running {build_tag()!r}"]

    directory = artifact_dir()
    modules = {}
    for fullname, entry in sorted(manifest.get("modules", {}).items()):
        artifact = directory / entry["artifact"]
        source = REPO_ROOT / entry["source"]
        if not artifact.exists():
            problems.append(f"{fullname}: artifact missing ({artifact.name})")
            continue
        if not source.exists():
            problems.append(f"{fullname}: source {entry['source']} no longer exists")
            continue
        if source_hash(source) != entry["source_sha256"]:
            # The realistic failure: someone edited the .py and did not
            # rebuild. Naming the module is the whole point -- debugging
            # stale compiled code is otherwise invisible.
            problems.append(f"{fullname}: {entry['source']} changed since it was compiled")
            continue
        modules[fullname] = artifact
    return modules, problems


class _CompiledFinder:
    """Maps specific module names to prebuilt artifacts. Anything not in the
    mapping returns None and falls through to the normal import machinery."""

    def __init__(self, modules: dict[str, Path]):
        self._modules = modules

    def find_spec(self, fullname, path=None, target=None):
        artifact = self._modules.get(fullname)
        if artifact is None:
            return None
        # The init symbol Cython generates is PyInit_<last component>, which
        # is exactly what ExtensionFileLoader looks for.
        loader = ExtensionFileLoader(fullname, str(artifact))
        return spec_from_file_location(fullname, str(artifact), loader=loader)

    def __repr__(self):
        return f"<_CompiledFinder {len(self._modules)} modules from {artifact_dir()}>"


def _fail(mode: str, message: str) -> None:
    if mode == "strict":
        raise RuntimeError(
            f"FARM_COMPILED=strict but the compiled build is unusable: {message}\n"
            "Run `python3 tools/build_cython.py` (or unset FARM_COMPILED)."
        )
    print(
        f"warning: FARM_COMPILED is set but falling back to pure Python: {message}",
        file=sys.stderr,
    )


def in_tree_artifacts() -> list[Path]:
    """Compiled modules sitting next to the .py they shadow.

    Building Cython output in place works, and is a trap: EXTENSION_SUFFIXES
    is checked before SOURCE_SUFFIXES, so the .so wins silently, edits to the
    .py appear to do nothing, and this module's fallback is defeated -- the
    finder can decline to serve a stale artifact only to have the import
    system find an even staler one on disk. `_fastplot` is excluded: it has no
    .py counterpart, so it shadows nothing.
    """
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    found = []
    for package in PACKAGES:
        directory = REPO_ROOT / package
        for artifact in sorted(directory.glob(f"*{suffix}")):
            if (directory / f"{artifact.name[: -len(suffix)]}.py").exists():
                found.append(artifact)
    return found


def _warn_about_in_tree_artifacts() -> None:
    shadowing = in_tree_artifacts()
    if not shadowing:
        return
    names = ", ".join(_display(path) for path in shadowing[:3])
    more = f" (+{len(shadowing) - 3} more)" if len(shadowing) > 3 else ""
    print(
        f"warning: compiled modules are shadowing their own source: {names}{more}\n"
        "         Edits to those .py files will have no effect and this build is "
        "not verified against the manifest.\n"
        "         Remove them with: rm -f simulation/*.so agents/*.so && "
        "python3 tools/build_fastplot.py",
        file=sys.stderr,
    )


def activate() -> bool:
    """Install the finder if FARM_COMPILED asks for it and the build verifies.

    Idempotent, and safe to call from more than one package __init__ -- which
    is how it happens, since whichever of simulation/agents is imported first
    has to install the finder before the other's submodules are resolved.
    """
    global _activated
    if _activated:
        return True
    # Checked before the mode, deliberately: an in-tree artifact is most
    # dangerous precisely when FARM_COMPILED is unset, because then the user
    # believes they are running the reference implementation and are not.
    _warn_about_in_tree_artifacts()
    mode = (os.environ.get("FARM_COMPILED") or "").strip().lower()
    if mode in ("", "0", "false", "no"):
        return False

    manifest_path = artifact_dir() / "manifest.json"
    if not manifest_path.exists():
        _fail(mode, f"no manifest at {_display(manifest_path)}")
        return False
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        _fail(mode, f"unreadable manifest: {error}")
        return False

    modules, problems = _verify(manifest)
    if problems:
        _fail(mode, "; ".join(problems))
        return False
    if not modules:
        _fail(mode, "manifest lists no modules")
        return False

    # Anything already imported cannot be swapped, and a half-compiled process
    # is not a configuration anyone asked for -- refuse rather than deliver it.
    already = sorted(name for name in modules if name in sys.modules)
    if already:
        _fail(mode, f"already imported as source: {', '.join(already)}")
        return False

    sys.meta_path.insert(0, _CompiledFinder(modules))
    _activated = True
    return True


def active() -> bool:
    return _activated
