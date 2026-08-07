"""Agent package.

Mirrors simulation/__init__.py: the compiled build has to be activated before
any `agents.*` submodule is resolved, and an agent module may be imported
before anything touches `simulation`. `activate()` is idempotent, so whichever
package is imported first does the work.
"""

from simulation import _compiled as _compiled

_compiled.activate()
