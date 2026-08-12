"""Vectorized Monte Carlo sampler -- a separate, experimental tool.

Not a replacement for `simulation/`, not on its import path, and not
expected to ever be bit-compatible with it. See README.md in this directory
before touching anything here, especially before assuming any number in
`crops.py` should track `config/crops.json`.

Needs numpy + numba (`requirements-fast.txt`); nothing under `main.py`,
`simulation/`, `runner/`, or `metrics/` imports this package or is affected
by its absence.
"""
