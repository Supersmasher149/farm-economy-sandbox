"""Pure-Python sequential reference implementation (component E).

Not a second, independent model -- a transcription of kernel.py's
`_simulate_chunk_core` with `r` and `p` fixed to a single run/plot, no numba,
no numpy arrays for the hot loop. It exists to be read side by side with the
kernel and to give scripts/vectorized_validate.py something to check
`simulate_chunk(..., B=1)` against.

Every state write is cast through `np.float32` immediately, the same way the
kernel's float32-dtype arrays round on every store. Skip that and this drifts
from the kernel by float64-vs-float32 rounding within a few days of
simulation and the validation script stops meaning anything.

This does *not* validate against `simulation/`'s real crop-growth model --
see vectorized/README.md for why that comparison isn't meaningful (different
RNG scheme, different simplified physics, not intended to be bit-exact with
the main engine). It validates that the numba kernel is a faithful
parallelization of *this* module's sequential algorithm.
"""

from __future__ import annotations

import numpy as np

from vectorized import crops, rng


def _f32(x: float) -> float:
    return float(np.float32(x))


def simulate_run_reference(
    master_seed: int,
    run_index: int,
    strategy: int,
    num_plots: int,
    num_days: int,
) -> dict:
    """Sequentially simulate exactly one run. Returns final money/total_harvest."""
    run_state = rng.run_seed(master_seed, run_index)
    plot_state = [rng.plot_seed(run_state, p) for p in range(num_plots)]

    money = _f32(crops.STARTING_MONEY)
    total_harvest = _f32(0.0)
    moisture = [_f32(crops.STARTING_MOISTURE) for _ in range(num_plots)]
    nitrogen = [_f32(crops.STARTING_NITROGEN) for _ in range(num_plots)]
    crop_type = [-1] * num_plots
    growth_stage = [0] * num_plots
    days_to_harvest = [0] * num_plots

    num_seasons = len(crops.SEASON_RAIN_CHANCE)

    for day in range(num_days):
        season = (day // crops.SEASON_LENGTH_DAYS) % num_seasons

        run_state, u_rain = rng.next_scalar(run_state)
        run_state, u_rain_amt = rng.next_scalar(run_state)
        rain_chance = crops.SEASON_RAIN_CHANCE[season]
        if u_rain < rain_chance:
            lo, hi = crops.SEASON_RAIN_MIN[season], crops.SEASON_RAIN_MAX[season]
            rainfall = lo + u_rain_amt * (hi - lo)
        else:
            rainfall = 0.0
        evap = crops.SEASON_EVAPORATION[season]

        for p in range(num_plots):
            moisture[p] = _f32(min(1.0, max(0.0, moisture[p] + rainfall - evap)))

        for p in range(num_plots):
            ps = plot_state[p]
            ct = crop_type[p]

            if ct < 0:
                nitrogen[p] = _f32(min(1.0, nitrogen[p] + float(crops.NITROGEN_FALLOW_REGEN)))
                ps, u_pick = rng.next_scalar(ps)
                if strategy == crops.STRATEGY_RANDOM:
                    crop_idx = min(int(u_pick * crops.NUM_CROPS), crops.NUM_CROPS - 1)
                elif strategy == crops.STRATEGY_GREEDY:
                    crop_idx = crops.GREEDY_CROP
                else:
                    crop_idx = crops.CONSERVATIVE_CROP
                cost = float(crops.SEED_COST[crop_idx])
                if money >= cost:
                    money = _f32(money - cost)
                    crop_type[p] = crop_idx
                    growth_stage[p] = 0
                    days_to_harvest[p] = int(crops.GROWTH_DAYS[crop_idx])
            else:
                ps, u_water = rng.next_scalar(ps)
                if strategy == crops.STRATEGY_GREEDY:
                    should_water = moisture[p] < 0.6
                elif strategy == crops.STRATEGY_CONSERVATIVE:
                    should_water = moisture[p] < 0.25
                else:
                    should_water = u_water < 0.5
                if should_water and money >= float(crops.WATER_COST):
                    money = _f32(money - float(crops.WATER_COST))
                    moisture[p] = _f32(min(1.0, moisture[p] + float(crops.WATER_AMOUNT)))

                nitrogen[p] = _f32(max(0.0, nitrogen[p] - float(crops.NITROGEN_USE[ct])))
                growth_stage[p] += 1
                days_to_harvest[p] -= 1

                if days_to_harvest[p] <= 0:
                    ps, u_loss = rng.next_scalar(ps)
                    ps, u_yield = rng.next_scalar(ps)

                    lc = float(crops.LOSS_CHANCE[ct])
                    if moisture[p] < float(crops.MIN_MOISTURE[ct]):
                        lc += 0.15
                    if nitrogen[p] < 0.2:
                        lc += 0.1
                    lc = min(0.95, lc)

                    if u_loss >= lc:
                        base_y = crops.MIN_YIELD[ct] + u_yield * (
                            crops.MAX_YIELD[ct] - crops.MIN_YIELD[ct]
                        )
                        moisture_factor = (
                            1.0 if moisture[p] >= float(crops.MIN_MOISTURE[ct]) else 0.6
                        )
                        nitrogen_factor = 0.5 + 0.5 * nitrogen[p]
                        y = base_y * moisture_factor * nitrogen_factor
                        money = _f32(money + y * float(crops.BASE_PRICE[ct]))
                        total_harvest = _f32(total_harvest + y)

                    crop_type[p] = -1
                    growth_stage[p] = 0
                    days_to_harvest[p] = 0

            plot_state[p] = ps

    return {"money": money, "total_harvest": total_harvest}
