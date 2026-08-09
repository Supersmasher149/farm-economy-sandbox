"""Simulation package.

The import below is the hook for the optional compiled build: it runs before
any `simulation.*` submodule is resolved, which is the only moment a finder
can redirect them. It is a no-op unless FARM_COMPILED is set -- see
simulation/_compiled.py.
"""

from simulation import _compiled as _compiled

_compiled.activate()
