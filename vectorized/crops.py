"""The 3 fixed strategy ids this sampler's kernel understands.

Everything that used to live here as hand-maintained constants (crop
economics, soil dynamics, weather seasons, starting values) now comes from
`config_arrays.load_vector_config()`, which reads the real `config/*.json`
files -- see that module's docstring for why the coupling this file's first
version deliberately avoided is now the right call.

What's left here is genuinely not config-driven: `config/*.json` has no
concept of "greedy/conservative/random" -- that's this sampler's own fixed
strategy roster (component C), distinct from the real game's 11 named
agents in `agents/*.py`. Porting the real agent roster is future work (see
vectorized/README.md's roadmap), not something `config/*.json` will ever
express regardless of how much of the rest of this module reads it.
"""

from __future__ import annotations

STRATEGY_GREEDY = 0
STRATEGY_CONSERVATIVE = 1
STRATEGY_RANDOM = 2
STRATEGY_NAMES = ("greedy", "conservative", "random")
