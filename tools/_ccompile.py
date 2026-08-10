"""Shared C-compiler invocation for the two optional accelerators.

`tools/build_fastplot.py` (hand-written kernel) and `tools/build_cython.py`
(generated modules) have different contracts, but they must not have different
float semantics, so the flags live here and both import them.

The flags are load-bearing, not incidental: `-ffp-contract=off` stops the
compiler contracting `a * b + c` into an FMA and `-fno-fast-math` stops it
reassociating floating point. Either changes results in the last bits, which
breaks bit-exact seed replay. See the header of simulation/_fastplotmodule.c.

Neither builder uses setuptools -- it is not a dependency of this project, and
adding one just to build *optional* accelerators would defeat the point. Only
a C compiler and the CPython headers are required, both of which ship with any
normal CPython install.

The flag list itself is defined in simulation/_compiled.py, not here: that
module verifies at *load* time that artifacts were built with these flags, so
it needs the list anyway, and a second copy here could drift from the one
being enforced.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_helper = None


def compiled_helper():
    """simulation/_compiled.py, loaded without importing the `simulation`
    package -- whose __init__ activates the shim, which would leave a builder
    running against the very artifacts it is in the middle of replacing."""
    global _helper
    if _helper is None:
        spec = importlib.util.spec_from_file_location(
            "_compiled_helper", REPO_ROOT / "simulation" / "_compiled.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _helper = module
    return _helper


REQUIRED_FLAGS = list(compiled_helper().REQUIRED_CFLAGS)

# Cython emits code that assumes the same aliasing rules CPython itself is
# built with. Harmless for the hand-written kernel, required for the generated
# modules, so it is applied to both rather than tracked separately.
BASE_FLAGS = ["-O2", *REQUIRED_FLAGS, "-fno-strict-aliasing"]


def compiler() -> list[str]:
    return (os.environ.get("CC") or sysconfig.get_config_var("CC") or "cc").split()


def ext_suffix() -> str:
    return sysconfig.get_config_var("EXT_SUFFIX") or ".so"


def compile_command(source: str, target: str, extra_flags: list[str] | None = None) -> list[str]:
    """Full argv to build one extension module from one C source file."""
    command = [*compiler(), *BASE_FLAGS, *(extra_flags or []), "-shared"]
    if sys.platform == "darwin":
        # Extension modules resolve CPython symbols from the interpreter that
        # loads them rather than linking against libpython.
        command += ["-undefined", "dynamic_lookup"]
    command += ["-fPIC", f"-I{sysconfig.get_paths()['include']}", source, "-o", target]
    return command


def assert_required_flags(command: list[str]) -> None:
    """Fail loudly if a caller has dropped a determinism-critical flag."""
    missing = [flag for flag in REQUIRED_FLAGS if flag not in command]
    if missing:
        raise RuntimeError(
            f"refusing to build without {' '.join(missing)}: these flags keep "
            "floating point bit-exact, and dropping them silently breaks seed replay"
        )
