"""Vectorized physics + agent kernel (components B and C).

Design deviation from the letter of the prompt, on purpose: rather than a
day-outer loop expressed as numpy masks/`np.where` over (B, P) arrays, this
parallelizes with `prange` over runs (the B axis) and keeps ordinary scalar
day/plot loops *inside* each run. Numba's automatic array-parallelization of
hand-written masked numpy expressions is unreliable on code with this much
branching (irrigation cost gating, harvest resets, strategy dispatch); `prange`
over independent runs is the idiom numba's own docs recommend for exactly this
"many independent simulations" shape, and it gets the actual goal -- millions
of runs under a wall-clock budget -- with a compiler that reliably parallelizes
what's asked of it. Every run is still fully independent (its own rng_run /
rng_plot streams, no cross-run data dependency), so this is still "vectorized
across runs" in the sense that matters: no Python-level per-run loop, no
per-run object.

Kept in lockstep with reference.py: same branch order, same draw order, same
float32 rounding on every state write (see reference.py's docstring). That
correspondence is what scripts/vectorized_validate.py checks.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from vectorized import crops
from vectorized.state import BatchState

MASK64 = np.uint64(0xFFFFFFFFFFFFFFFF)
GOLDEN_GAMMA = np.uint64(0x9E3779B97F4A7C15)
MIX_MULT_1 = np.uint64(0xBF58476D1CE4E5B9)
MIX_MULT_2 = np.uint64(0x94D049BB133111EB)


@njit(cache=True, inline="always")
def _next(state):
    """One splitmix64 draw. Must match vectorized.rng.next_scalar exactly."""
    state = state + GOLDEN_GAMMA
    z = state
    z = (z ^ (z >> np.uint64(30))) * MIX_MULT_1
    z = (z ^ (z >> np.uint64(27))) * MIX_MULT_2
    z = z ^ (z >> np.uint64(31))
    uniform = np.float64(z >> np.uint64(11)) * (1.0 / (1 << 53))
    return state, uniform


@njit(parallel=True, cache=True)
def _simulate_chunk_core(
    money,
    total_harvest,
    strategy_id,
    moisture,
    nitrogen,
    crop_type,
    growth_stage,
    days_to_harvest,
    rng_run_state,
    rng_plot_state,
    num_days,
    seed_cost,
    growth_days,
    min_yield_arr,
    max_yield_arr,
    base_price,
    loss_chance_arr,
    min_moisture_arr,
    nitrogen_use,
    greedy_crop,
    conservative_crop,
    num_crops,
    water_cost,
    water_amount,
    nitrogen_fallow_regen,
    season_length_days,
    season_rain_chance,
    season_rain_min,
    season_rain_max,
    season_evaporation,
):
    num_runs = money.shape[0]
    num_plots = moisture.shape[1]
    num_seasons = season_rain_chance.shape[0]

    for r in prange(num_runs):
        m = np.float64(money[r])
        th = np.float64(total_harvest[r])
        strat = strategy_id[r]
        run_state = rng_run_state[r]

        for day in range(num_days):
            season = (day // season_length_days) % num_seasons

            run_state, u_rain = _next(run_state)
            run_state, u_rain_amt = _next(run_state)
            rain_chance = season_rain_chance[season]
            if u_rain < rain_chance:
                lo = season_rain_min[season]
                hi = season_rain_max[season]
                rainfall = lo + u_rain_amt * (hi - lo)
            else:
                rainfall = 0.0
            evap = season_evaporation[season]

            for p in range(num_plots):
                moisture[r, p] = np.float32(
                    min(1.0, max(0.0, np.float64(moisture[r, p]) + rainfall - evap))
                )

            for p in range(num_plots):
                plot_state = rng_plot_state[r, p]
                ct = crop_type[r, p]

                if ct < 0:
                    nitrogen[r, p] = np.float32(
                        min(1.0, np.float64(nitrogen[r, p]) + nitrogen_fallow_regen)
                    )
                    plot_state, u_pick = _next(plot_state)
                    if strat == 2:
                        crop_idx = int(u_pick * num_crops)
                        if crop_idx >= num_crops:
                            crop_idx = num_crops - 1
                    elif strat == 0:
                        crop_idx = greedy_crop
                    else:
                        crop_idx = conservative_crop
                    cost = seed_cost[crop_idx]
                    if m >= cost:
                        m -= cost
                        crop_type[r, p] = crop_idx
                        growth_stage[r, p] = 0
                        days_to_harvest[r, p] = growth_days[crop_idx]
                else:
                    plot_state, u_water = _next(plot_state)
                    moisture_now = np.float64(moisture[r, p])
                    if strat == 0:
                        should_water = moisture_now < 0.6
                    elif strat == 1:
                        should_water = moisture_now < 0.25
                    else:
                        should_water = u_water < 0.5
                    if should_water and m >= water_cost:
                        m -= water_cost
                        moisture[r, p] = np.float32(min(1.0, moisture_now + water_amount))

                    nitrogen[r, p] = np.float32(
                        max(0.0, np.float64(nitrogen[r, p]) - nitrogen_use[ct])
                    )
                    growth_stage[r, p] = growth_stage[r, p] + 1
                    days_to_harvest[r, p] = days_to_harvest[r, p] - 1

                    if days_to_harvest[r, p] <= 0:
                        plot_state, u_loss = _next(plot_state)
                        plot_state, u_yield = _next(plot_state)

                        lc = np.float64(loss_chance_arr[ct])
                        if np.float64(moisture[r, p]) < min_moisture_arr[ct]:
                            lc += 0.15
                        if np.float64(nitrogen[r, p]) < 0.2:
                            lc += 0.1
                        lc = min(0.95, lc)

                        if u_loss >= lc:
                            base_y = min_yield_arr[ct] + u_yield * (
                                max_yield_arr[ct] - min_yield_arr[ct]
                            )
                            moisture_factor = (
                                1.0 if np.float64(moisture[r, p]) >= min_moisture_arr[ct] else 0.6
                            )
                            nitrogen_factor = 0.5 + 0.5 * np.float64(nitrogen[r, p])
                            y = base_y * moisture_factor * nitrogen_factor
                            m += y * base_price[ct]
                            th += y

                        crop_type[r, p] = -1
                        growth_stage[r, p] = 0
                        days_to_harvest[r, p] = 0

                rng_plot_state[r, p] = plot_state

        money[r] = np.float32(m)
        total_harvest[r] = np.float32(th)
        rng_run_state[r] = run_state


def simulate_chunk(state: BatchState, num_days: int, master_seed: int | None = None) -> None:
    """Advance `state` in place by `num_days`.

    Matches component B's requested signature `simulate_chunk(state, num_days,
    master_seed)`. `master_seed` is optional: pass it only for a self-seeding
    one-shot call; the normal orchestrator flow is `state.allocate` ->
    `state.init_runs(master_seed, ...)` -> `simulate_chunk(state, num_days)`,
    matching component D's "allocate state, initialize runs, run
    simulate_chunk" outline.
    """
    if master_seed is not None:
        from vectorized import state as state_mod

        state_mod.init_runs(
            state,
            master_seed,
            run_index_offset=0,
            strategy_of_run=state.strategy_id,
        )

    _simulate_chunk_core(
        state.money,
        state.total_harvest,
        state.strategy_id,
        state.moisture,
        state.nitrogen,
        state.crop_type,
        state.growth_stage,
        state.days_to_harvest,
        state.rng_run_state,
        state.rng_plot_state,
        num_days,
        crops.SEED_COST,
        crops.GROWTH_DAYS,
        crops.MIN_YIELD,
        crops.MAX_YIELD,
        crops.BASE_PRICE,
        crops.LOSS_CHANCE,
        crops.MIN_MOISTURE,
        crops.NITROGEN_USE,
        crops.GREEDY_CROP,
        crops.CONSERVATIVE_CROP,
        crops.NUM_CROPS,
        float(crops.WATER_COST),
        float(crops.WATER_AMOUNT),
        float(crops.NITROGEN_FALLOW_REGEN),
        crops.SEASON_LENGTH_DAYS,
        crops.SEASON_RAIN_CHANCE,
        crops.SEASON_RAIN_MIN,
        crops.SEASON_RAIN_MAX,
        crops.SEASON_EVAPORATION,
    )
