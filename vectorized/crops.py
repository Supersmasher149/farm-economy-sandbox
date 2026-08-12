"""A small, self-contained crop table for the vectorized sampler.

Deliberately *not* a loader for config/crops.json. That file's schema (yield
curves, nutrient_demand dicts, seasonal_demand, unlock_requirement chains,
...) drives simulation/crop_growth.py's per-plant physics; reusing it here
would either (a) silently couple this experimental, non-bit-exact sampler to
the real economy's config, so an edit made for balance-testing purposes
quietly changes this module's numbers too, or (b) require reimplementing
enough of simulation/derived.py's config resolution that the two paths could
drift out of sync anyway. Three illustrative crops with plain scalar fields,
loosely modeled on quickweed/greenleaf/purplehaze's real magnitudes, keeps
the coupling at zero. See vectorized/README.md for what this module is and
isn't.

Every array here is indexed by crop_id (0..NUM_CROPS-1) and consumed
identically by kernel.py (numba) and reference.py (pure Python) -- keep the
two in lockstep the same way rng.py's three call shapes are kept in lockstep.
"""

from __future__ import annotations

import numpy as np

CROP_NAMES = ("quickweed", "greenleaf", "purplehaze")
NUM_CROPS = len(CROP_NAMES)

# fmt: off
SEED_COST     = np.array([8,    12,   64],  dtype=np.float32)
GROWTH_DAYS   = np.array([3,    6,    12],  dtype=np.int16)
MIN_YIELD     = np.array([2.0,  4.0,  5.0], dtype=np.float32)
MAX_YIELD     = np.array([3.0,  6.0,  10.0], dtype=np.float32)
BASE_PRICE    = np.array([6.0,  6.0,  16.0], dtype=np.float32)
LOSS_CHANCE   = np.array([0.19, 0.26, 0.39], dtype=np.float32)
MIN_MOISTURE  = np.array([0.30, 0.40, 0.48], dtype=np.float32)
NITROGEN_USE  = np.array([0.025, 0.035, 0.04], dtype=np.float32)
# fmt: on

# Fixed strategy choices (component C): Greedy always plants the crop with
# the best naive expected-revenue-per-day (base_price * mean_yield /
# growth_days, ignoring loss_chance -- that's the "greedy" bias); Conservative
# always plants the crop with the lowest loss_chance. Random draws uniformly
# each time a plot opens up (see kernel.py / reference.py).
_expected_value_per_day = BASE_PRICE * (MIN_YIELD + MAX_YIELD) / 2 / GROWTH_DAYS
GREEDY_CROP = int(np.argmax(_expected_value_per_day))
CONSERVATIVE_CROP = int(np.argmin(LOSS_CHANCE))

# Watering/irrigation economics, shared across crops (component C masks key
# off these plus strategy_id, not off anything crop-specific).
WATER_COST = np.float32(1.0)
WATER_AMOUNT = np.float32(0.35)

# Strategy ids (component C, "encode strategy as strategy_id").
STRATEGY_GREEDY = 0
STRATEGY_CONSERVATIVE = 1
STRATEGY_RANDOM = 2
STRATEGY_NAMES = ("greedy", "conservative", "random")

# Season table (4 seasons over a 365-day year, ~91 days each), loosely
# modeled on config/weather.json's spring/summer/autumn/winter magnitudes.
# fmt: off
SEASON_LENGTH_DAYS = 91
SEASON_RAIN_CHANCE   = np.array([0.42, 0.18, 0.35, 0.28], dtype=np.float32)
SEASON_RAIN_MIN      = np.array([0.12, 0.08, 0.10, 0.06], dtype=np.float32)
SEASON_RAIN_MAX      = np.array([0.32, 0.24, 0.30, 0.20], dtype=np.float32)
SEASON_EVAPORATION   = np.array([0.06, 0.12, 0.07, 0.04], dtype=np.float32)
# fmt: on

NITROGEN_FALLOW_REGEN = np.float32(0.02)
STARTING_MONEY = np.float32(300.0)
STARTING_MOISTURE = np.float32(0.65)
STARTING_NITROGEN = np.float32(0.75)
