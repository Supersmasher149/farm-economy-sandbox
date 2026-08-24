"""Vectorized Monte Carlo sampler -- a separate, experimental tool.

Not a replacement for `simulation/`, not on its import path, and not
expected to ever be bit-compatible with it -- different RNG scheme
(splitmix64, see rng.py), and still missing storage/markets/contracts/
processing/upgrades even though `config_arrays.py` now reads the real
`config/crops.json` + `config/soil.json` for the mechanics that are ported.
See README.md in this directory before touching anything here.

Needs numpy + numba (`requirements-fast.txt`); nothing under `main.py`,
`simulation/`, `runner/`, or `metrics/` imports this package or is affected
by its absence.
"""
