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

**The build recipe is verified, not just recorded.** Bit-exactness depends on
*how* the artifacts were compiled -- the Cython directives and the float flags
in particular -- so the recipe this source tree specifies is hashed into the
manifest at build time and recomputed here at load time. A build made with
different directives is rejected exactly like a build made from edited source.
That is also why DIRECTIVES and REQUIRED_CFLAGS live in this module rather than
in tools/: this is the file that has to verify them, it has no build-time
dependencies, and a second copy could drift.

FARM_COMPILED values:
    unset / "" / "0"   pure Python, this module does nothing
    "1"                use artifacts if they verify; warn once and fall back
    "strict"           use artifacts, or raise -- for CI

FARM_COMPILED_DIR overrides where artifacts are looked for. It exists so
tools/build_cython.py can verify a staged build through this exact loading
path before publishing it, and for debugging; production leaves it unset.

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
MANIFEST_VERSION = 2

# Bumped when the *meaning* of a recipe field changes in a way a hash cannot
# see -- e.g. if the builder started applying a directive it does not record.
# An artifact built before the bump then fails the recipe check instead of
# being trusted.
BUILD_RECIPE_VERSION = 1

# Cython directives. These are load-bearing for bit-exact replay, not tuning
# knobs; tools/build_cython.py's module docstring explains each one and why it
# has the value it does. Changing any of them changes the recipe hash, which
# invalidates every existing artifact directory -- which is the point.
DIRECTIVES = {
    "language_level": "3",
    "annotation_typing": "False",
    "infer_types": "False",
    "cdivision": "False",
    "c_api_binop_methods": "False",
    "binding": "True",
}

# C compiler flags that keep floating point bit-exact. tools/_ccompile.py reads
# these and refuses to build without them; the check here catches artifacts
# built back when the list was shorter. See that module's docstring.
REQUIRED_CFLAGS = ("-ffp-contract=off", "-fno-fast-math")

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
    """Where artifacts are read from. FARM_COMPILED_DIR overrides it so the
    builder can verify a staged directory before publishing it."""
    override = (os.environ.get("FARM_COMPILED_DIR") or "").strip()
    if override:
        return Path(override)
    return BUILD_ROOT / build_tag()


def compiler_identity() -> str:
    """The C compiler this tree would build with, normalized for comparison.

    Mirrors tools/_ccompile.compiler(). A mismatch is not proof of a bad
    build, but "these artifacts were not produced the way this checkout
    produces them" is exactly the class of thing this module refuses on.
    """
    return " ".join((os.environ.get("CC") or sysconfig.get_config_var("CC") or "cc").split())


def build_recipe() -> dict:
    """Everything about *how* a build is made that this module can verify.

    Deliberately excludes the Cython version and the full compiler command
    line. Cython is a build-time-only dependency, so a machine running
    prebuilt artifacts cannot be expected to have it installed and therefore
    cannot recompute a hash covering it; the full command line embeds an
    interpreter include path that legitimately differs between a build host
    and a run host. Both are recorded in the manifest under "provenance" for
    humans to read -- they are just not part of the verified hash.
    """
    return {
        "recipe_version": BUILD_RECIPE_VERSION,
        "build_tag": build_tag(),
        "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX") or ".so",
        "directives": dict(DIRECTIVES),
        "required_cflags": list(REQUIRED_CFLAGS),
        "compiler": compiler_identity(),
    }


def recipe_hash(recipe: dict) -> str:
    return hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


# Required manifest keys and their JSON types. A file that parses as JSON but
# is not a manifest must take the documented fallback path, so every field is
# checked before it is read -- a KeyError here would escape activate()'s
# handling and crash an import that is supposed to degrade to pure Python.
_MANIFEST_SCHEMA = {
    "manifest_version": int,
    "build_tag": str,
    "build_recipe": dict,
    "build_recipe_sha256": str,
    "modules": dict,
}
_ENTRY_SCHEMA = {"source": str, "source_sha256": str, "artifact": str}


def _typed(value, kind) -> bool:
    # bool is an int subclass, and `"manifest_version": true` should not pass
    # for an int field.
    if kind is int and isinstance(value, bool):
        return False
    return isinstance(value, kind)


def _schema_problems(manifest) -> list[str]:
    """Structural complaints about a parsed manifest, before any of it is used."""
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]

    problems = []
    for key, kind in _MANIFEST_SCHEMA.items():
        if key not in manifest:
            problems.append(f"manifest is missing {key!r}")
        elif not _typed(manifest[key], kind):
            problems.append(f"manifest field {key!r} is not {kind.__name__}")

    modules = manifest.get("modules")
    if isinstance(modules, dict):
        for fullname, entry in sorted(modules.items()):
            if not isinstance(entry, dict):
                problems.append(f"{fullname}: manifest entry is not a JSON object")
                continue
            for key, kind in _ENTRY_SCHEMA.items():
                if key not in entry:
                    problems.append(f"{fullname}: manifest entry is missing {key!r}")
                elif not _typed(entry[key], kind):
                    problems.append(f"{fullname}: manifest entry {key!r} is not {kind.__name__}")
    return problems


def _contained(root: Path, relative: str) -> Path | None:
    """`root / relative`, or None if it is absolute or escapes `root`.

    The manifest sits next to the artifacts it describes, so this is not a
    trust boundary so much as a way to keep a corrupt or hand-edited manifest
    from pointing the loader at an arbitrary file on disk.
    """
    if os.path.isabs(relative) or not relative:
        return None
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return candidate


def _recipe_problems(manifest: dict) -> list[str]:
    """Complaints about *how* the artifacts were built."""
    expected = build_recipe()
    if recipe_hash(manifest["build_recipe"]) != manifest["build_recipe_sha256"]:
        return ["build_recipe does not match its own recorded hash (manifest was edited)"]
    if recipe_hash(expected) == manifest["build_recipe_sha256"]:
        return []

    # Name the fields rather than printing two hashes: "rebuilt with different
    # directives" and "built by a different compiler" want different fixes.
    recorded = manifest["build_recipe"]
    differing = sorted(
        key for key in set(expected) | set(recorded) if expected.get(key) != recorded.get(key)
    )
    detail = ", ".join(
        f"{key}: built {recorded.get(key)!r}, expected {expected.get(key)!r}" for key in differing
    )
    return [f"built with a different recipe ({detail or 'fields match but hashes differ'})"]


def _verify(manifest: dict) -> tuple[dict[str, Path], list[str]]:
    """Return (loadable modules, reasons it is not fully usable)."""
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION:
        version = manifest.get("manifest_version") if isinstance(manifest, dict) else None
        return {}, [
            f"manifest_version {version!r} != {MANIFEST_VERSION} "
            "(rebuild with tools/build_cython.py)"
        ]
    if manifest.get("build_tag") != build_tag():
        return {}, [f"built for {manifest.get('build_tag')!r}, running {build_tag()!r}"]

    schema = _schema_problems(manifest)
    if schema:
        return {}, schema
    recipe = _recipe_problems(manifest)
    if recipe:
        return {}, recipe

    known_sources = compilable_sources()
    directory = artifact_dir()
    problems = []
    modules = {}
    for fullname, entry in sorted(manifest["modules"].items()):
        expected_source = known_sources.get(fullname)
        if expected_source is None:
            problems.append(f"{fullname}: not a module this tree compiles")
            continue
        artifact = _contained(directory, entry["artifact"])
        if artifact is None:
            problems.append(f"{fullname}: artifact path {entry['artifact']!r} escapes {directory}")
            continue
        source = _contained(REPO_ROOT, entry["source"])
        if source is None or source != expected_source:
            problems.append(
                f"{fullname}: manifest names source {entry['source']!r}, "
                f"but this tree has {_display(expected_source)}"
            )
            continue
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
