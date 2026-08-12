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

Phase 1 ("crop/soil physics parity") ported `simulation/crop_growth.py` +
`simulation/weather.py`'s per-plot mechanics here nearly line-for-line (see
each block's comment for the source function it mirrors): multi-nutrient soil
(N/P/K), pH stress, temperature stress, pest/disease pressure and
susceptibility, family-rotation and soil-health yield/quality multipliers,
neglect tracking, fertilizer, and revenue-gated crop unlocks.

Phase 2 ("storage & spoilage") ported `simulation/inventory.py`'s
age-and-spoil/capacity-trim/liability mechanics as a *shadow accounting*
system: harvest still credits `money`/`total_revenue` instantly at the same
`roll_price`-style draw as Phase 1 (real markets/agent-selling is Phase 3,
not built yet), but every non-rejected harvest ALSO inserts a lot into a
fixed-size per-run lot-slot array (see `state.py`'s docstring for the
`(B, L)` shape). Those lots age, downgrade quality, fully spoil, and get
capacity-trimmed once per run per day, producing `total_spoiled`/
`total_storage_cost` -- informational stats that do not feed back into
`money` this phase. See `vectorized/README.md`'s roadmap for why: Phase 3
(markets) has to exist before spoilage can meaningfully block a sale.

Still NOT ported: markets (price is a single roll_price-style draw at
harvest, not a supply/demand system), contracts, processing, upgrades
(including upgrades' storage `capacity_bonus`/`shelf_life_multiplier`
effects -- Phase 2 uses only the base `config/storage.json` values). See
vectorized/README.md's roadmap.

Kept in lockstep with reference.py: same branch order, same draw order, same
float32 rounding on every state write (see reference.py's docstring). That
correspondence is what scripts/vectorized_validate.py checks.
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

from vectorized.config_arrays import VectorConfig
from vectorized.state import BatchState

MASK64 = np.uint64(0xFFFFFFFFFFFFFFFF)
GOLDEN_GAMMA = np.uint64(0x9E3779B97F4A7C15)
MIX_MULT_1 = np.uint64(0xBF58476D1CE4E5B9)
MIX_MULT_2 = np.uint64(0x94D049BB133111EB)

STRATEGY_GREEDY = 0
STRATEGY_CONSERVATIVE = 1
STRATEGY_RANDOM = 2

# Fertilizing thresholds (component C): not config-driven -- config/*.json
# has no notion of "how cautious is the greedy/conservative/random
# strategy," same as the watering thresholds below. Kept as named constants
# so they read the same way at both call sites (kernel.py, reference.py)
# rather than as unexplained literals.
FERTILIZE_CASH_BUFFER_GREEDY = 3.0  # multiples of fertilizer_cost kept in reserve
FERTILIZE_CASH_BUFFER_CONSERVATIVE = 10.0
WATER_THRESHOLD_GREEDY = 0.6
WATER_THRESHOLD_CONSERVATIVE = 0.25
COIN_FLIP = 0.5


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
    total_revenue,
    strategy_id,
    total_spoiled,
    total_storage_cost,
    moisture,
    nitrogen,
    phosphorus,
    potassium,
    ph,
    soil_health,
    pest_pressure,
    disease_pressure,
    crop_type,
    growth_stage,
    days_to_harvest,
    previous_crop_family,
    fertilized,
    water_stress,
    nutrient_stress,
    temperature_stress,
    pest_stress,
    disease_stress,
    neglect_days,
    last_watered_day,
    rng_run_state,
    rng_plot_state,
    # storage lots (Phase 2), shape (B, L), L = num_plots * config.lots_per_plot
    lot_item_id,
    lot_quantity,
    lot_quality,
    lot_age_days,
    overflow_events,
    num_days,
    # crop arrays (component: config_arrays.VectorConfig)
    num_crops,
    seed_cost,
    growth_days,
    min_yield_arr,
    max_yield_arr,
    base_price,
    price_variation,
    loss_chance_arr,
    water_interval_days,
    min_moisture_arr,
    ph_low_arr,
    ph_high_arr,
    temp_low_arr,
    temp_high_arr,
    pest_susceptibility,
    disease_susceptibility,
    nitrogen_demand,
    phosphorus_demand,
    potassium_demand,
    family_id,
    unlock_total_revenue,
    effective_shelf_life_days,
    greedy_rank,
    conservative_rank,
    # soil dynamics
    regen_moisture,
    regen_nitrogen,
    regen_phosphorus,
    regen_potassium,
    regen_soil_health,
    regen_pest_pressure,
    regen_disease_pressure,
    same_family_yield_penalty,
    same_family_quality_penalty,
    soil_health_yield_floor,
    soil_health_yield_span,
    harvest_soil_health_cost,
    min_soil_health,
    fallow_pest_decay,
    fallow_disease_decay,
    fallow_soil_health_regen,
    pest_growth_per_day,
    disease_growth_per_rainfall,
    max_pest_pressure,
    max_disease_pressure,
    # watering
    water_cost_per_plot,
    water_moisture_added,
    neglect_loss_chance_penalty_per_day,
    max_neglect_loss_chance_bonus,
    neglect_yield_penalty_per_day,
    max_neglect_yield_penalty,
    # fertilizer
    fertilizer_cost,
    fertilizer_yield_bonus_pct,
    fertilizer_loss_chance_reduction,
    fertilizer_quality_bonus,
    fertilizer_nitrogen_added,
    fertilizer_phosphorus_added,
    fertilizer_potassium_added,
    # weather
    season_length_days,
    season_temp_low,
    season_temp_high,
    season_rain_chance,
    season_rain_low,
    season_rain_high,
    season_evaporation,
    # storage
    storage_capacity,
    storage_daily_cost,
):
    num_runs = money.shape[0]
    num_plots = moisture.shape[1]
    num_seasons = season_rain_chance.shape[0]
    num_lot_slots = lot_item_id.shape[1]

    for r in prange(num_runs):
        m = np.float64(money[r])
        th = np.float64(total_harvest[r])
        tr = np.float64(total_revenue[r])
        ts = np.float64(total_spoiled[r])
        tc = np.float64(total_storage_cost[r])
        strat = strategy_id[r]
        run_state = rng_run_state[r]

        for day in range(num_days):
            # -- weather (simulation/weather.py:generate_weather) --
            season = (day // season_length_days) % num_seasons
            run_state, u_temp = _next(run_state)
            temperature = season_temp_low[season] + u_temp * (
                season_temp_high[season] - season_temp_low[season]
            )
            run_state, u_rain_chance = _next(run_state)
            if u_rain_chance < season_rain_chance[season]:
                run_state, u_rain_amt = _next(run_state)
                rainfall = season_rain_low[season] + u_rain_amt * (
                    season_rain_high[season] - season_rain_low[season]
                )
            else:
                rainfall = 0.0
            evaporation = season_evaporation[season] + max(0.0, temperature - 25.0) * 0.005

            # -- storage liability capture (simulation/inventory.py:capture_storage_liability)
            # -- run-level, once/day, before today's harvests are added --
            has_inventory = False
            for s in range(num_lot_slots):
                if lot_quantity[r, s] > 0:
                    has_inventory = True
                    break
            liability = storage_daily_cost if has_inventory else 0.0

            for p in range(num_plots):
                plot_state = rng_plot_state[r, p]
                ct = crop_type[r, p]

                # -- regen, all plots (simulation/weather.py:apply_weather) --
                moist = np.float64(moisture[r, p]) + regen_moisture + rainfall
                if regen_nitrogen != 0.0:
                    nitrogen[r, p] = np.float32(
                        min(1.0, np.float64(nitrogen[r, p]) + regen_nitrogen)
                    )
                if regen_phosphorus != 0.0:
                    phosphorus[r, p] = np.float32(
                        min(1.0, np.float64(phosphorus[r, p]) + regen_phosphorus)
                    )
                if regen_potassium != 0.0:
                    potassium[r, p] = np.float32(
                        min(1.0, np.float64(potassium[r, p]) + regen_potassium)
                    )
                if regen_soil_health != 0.0:
                    soil_health[r, p] = np.float32(
                        min(1.0, np.float64(soil_health[r, p]) + regen_soil_health)
                    )
                if regen_pest_pressure != 0.0:
                    pest_pressure[r, p] = np.float32(
                        max(0.0, np.float64(pest_pressure[r, p]) - regen_pest_pressure)
                    )
                if regen_disease_pressure != 0.0:
                    disease_pressure[r, p] = np.float32(
                        max(0.0, np.float64(disease_pressure[r, p]) - regen_disease_pressure)
                    )

                if ct < 0:
                    # -- fallow (simulation/weather.py:apply_weather, planted is None) --
                    moisture[r, p] = np.float32(max(0.0, min(1.0, moist - evaporation)))
                    pest_pressure[r, p] = np.float32(
                        max(0.0, np.float64(pest_pressure[r, p]) * fallow_pest_decay)
                    )
                    disease_pressure[r, p] = np.float32(
                        max(0.0, np.float64(disease_pressure[r, p]) * fallow_disease_decay)
                    )
                    soil_health[r, p] = np.float32(
                        min(1.0, np.float64(soil_health[r, p]) + fallow_soil_health_regen)
                    )
                else:
                    # -- growing: stress accumulation (crop_growth.py:update_crop_stress) --
                    water_stress[r, p] = np.float32(
                        np.float64(water_stress[r, p]) + max(0.0, min_moisture_arr[ct] - moist)
                    )
                    n_short = max(0.0, nitrogen_demand[ct] - np.float64(nitrogen[r, p]))
                    p_short = max(0.0, phosphorus_demand[ct] - np.float64(phosphorus[r, p]))
                    k_short = max(0.0, potassium_demand[ct] - np.float64(potassium[r, p]))
                    ph_now = np.float64(ph[r, p])
                    ph_stress = 0.0
                    if ph_now < ph_low_arr[ct]:
                        ph_stress = (ph_low_arr[ct] - ph_now) * 0.1
                    elif ph_now > ph_high_arr[ct]:
                        ph_stress = (ph_now - ph_high_arr[ct]) * 0.1
                    nutrient_stress[r, p] = np.float32(
                        np.float64(nutrient_stress[r, p]) + n_short + p_short + k_short + ph_stress
                    )
                    if temperature < temp_low_arr[ct]:
                        temperature_stress[r, p] = np.float32(
                            np.float64(temperature_stress[r, p])
                            + (temp_low_arr[ct] - temperature) / 20.0
                        )
                    elif temperature > temp_high_arr[ct]:
                        temperature_stress[r, p] = np.float32(
                            np.float64(temperature_stress[r, p])
                            + (temperature - temp_high_arr[ct]) / 20.0
                        )
                    pest_stress[r, p] = np.float32(
                        np.float64(pest_stress[r, p])
                        + np.float64(pest_pressure[r, p]) * pest_susceptibility[ct]
                    )
                    disease_stress[r, p] = np.float32(
                        np.float64(disease_stress[r, p])
                        + np.float64(disease_pressure[r, p]) * disease_susceptibility[ct]
                    )

                    moisture[r, p] = np.float32(max(0.0, min(1.0, moist - evaporation)))
                    nitrogen[r, p] = np.float32(
                        max(0.0, min(1.0, np.float64(nitrogen[r, p]) - nitrogen_demand[ct]))
                    )
                    phosphorus[r, p] = np.float32(
                        max(0.0, min(1.0, np.float64(phosphorus[r, p]) - phosphorus_demand[ct]))
                    )
                    potassium[r, p] = np.float32(
                        max(0.0, min(1.0, np.float64(potassium[r, p]) - potassium_demand[ct]))
                    )

                    overdue = day - last_watered_day[r, p] - water_interval_days[ct]
                    neglect_days[r, p] = max(0, overdue)

                    disease_pressure[r, p] = np.float32(
                        min(
                            max_disease_pressure,
                            np.float64(disease_pressure[r, p])
                            + rainfall * disease_growth_per_rainfall,
                        )
                    )
                    pest_pressure[r, p] = np.float32(
                        min(
                            max_pest_pressure, np.float64(pest_pressure[r, p]) + pest_growth_per_day
                        )
                    )

                    growth_stage[r, p] = growth_stage[r, p] + 1
                    days_to_harvest[r, p] = days_to_harvest[r, p] - 1

                    if days_to_harvest[r, p] <= 0:
                        # -- harvest (crop_growth.py:harvest_multipliers + compute_harvest_outcome) --
                        env_stress = (
                            np.float64(water_stress[r, p]) * 0.16
                            + np.float64(nutrient_stress[r, p]) * 0.18
                            + np.float64(temperature_stress[r, p]) * 0.12
                            + np.float64(pest_stress[r, p]) * 0.10
                            + np.float64(disease_stress[r, p]) * 0.12
                        )
                        quality_stress = env_stress + neglect_days[r, p] * 0.08
                        yield_mult = min(1.35, max(0.15, 1.0 - env_stress))
                        quality_mult = min(1.2, max(0.0, 1.0 - quality_stress * 1.25))
                        if fertilized[r, p]:
                            quality_mult += fertilizer_quality_bonus

                        fam = family_id[ct]
                        if previous_crop_family[r, p] == fam:
                            yield_mult *= same_family_yield_penalty
                            quality_mult *= same_family_quality_penalty
                        yield_mult *= (
                            soil_health_yield_floor
                            + np.float64(soil_health[r, p]) * soil_health_yield_span
                        )
                        yield_mult = min(1.5, max(0.1, yield_mult))
                        quality_mult = min(1.25, max(0.0, quality_mult))

                        loss_bonus = min(
                            neglect_days[r, p] * neglect_loss_chance_penalty_per_day,
                            max_neglect_loss_chance_bonus,
                        )
                        lc = loss_chance_arr[ct] + loss_bonus
                        if fertilized[r, p]:
                            lc -= fertilizer_loss_chance_reduction
                        lc = min(0.95, max(0.0, lc))

                        plot_state, u_loss = _next(plot_state)
                        plot_state, u_yield = _next(plot_state)
                        plot_state, u_price = _next(plot_state)

                        if u_loss >= lc:
                            yield_range = max_yield_arr[ct] - min_yield_arr[ct] + 1
                            base_y = min_yield_arr[ct] + int(u_yield * yield_range)
                            if base_y > max_yield_arr[ct]:
                                base_y = max_yield_arr[ct]
                            if fertilized[r, p]:
                                yield_mult = min(
                                    1.5, max(0.1, yield_mult + fertilizer_yield_bonus_pct)
                                )
                            neglect_penalty = min(
                                neglect_days[r, p] * neglect_yield_penalty_per_day,
                                max_neglect_yield_penalty,
                            )
                            amount = base_y * yield_mult * (1.0 - neglect_penalty)
                            amount_units = int(amount + 0.5) if amount > 0.0 else 0
                            # quality_grade(quality_mult): only "rejected" (<0.3) blocks
                            # the sale -- processing/standard/premium all sell here.
                            if amount_units > 0 and quality_mult >= 0.3:
                                price_factor = 1.0 + price_variation[ct] * (2.0 * u_price - 1.0)
                                price = max(0.01, base_price[ct] * price_factor)
                                revenue = amount_units * price
                                m += revenue
                                tr += revenue
                                th += amount_units

                                # -- storage: shadow lot (simulation/actions.py:harvest_mature)
                                # -- shadow accounting: money is already credited above (Phase
                                # 3/markets doesn't exist yet); this lot exists only to age/
                                # spoil/capacity-trim for total_spoiled/total_storage_cost. --
                                if quality_mult >= 0.9:
                                    grade = 3  # premium
                                elif quality_mult >= 0.62:
                                    grade = 2  # standard
                                else:
                                    grade = 1  # processing (>= 0.3 guaranteed by the outer gate)
                                inserted = False
                                for s in range(num_lot_slots):
                                    if lot_item_id[r, s] < 0:
                                        lot_item_id[r, s] = ct
                                        lot_quantity[r, s] = amount_units
                                        lot_quality[r, s] = grade
                                        lot_age_days[r, s] = -1  # becomes 0 on today's aging pass
                                        inserted = True
                                        break
                                if not inserted:
                                    overflow_events[r] += 1

                        previous_crop_family[r, p] = fam
                        soil_health[r, p] = np.float32(
                            max(
                                min_soil_health,
                                np.float64(soil_health[r, p]) - harvest_soil_health_cost,
                            )
                        )
                        crop_type[r, p] = -1
                        growth_stage[r, p] = 0
                        days_to_harvest[r, p] = 0
                        water_stress[r, p] = 0.0
                        nutrient_stress[r, p] = 0.0
                        temperature_stress[r, p] = 0.0
                        pest_stress[r, p] = 0.0
                        disease_stress[r, p] = 0.0
                        neglect_days[r, p] = 0
                        fertilized[r, p] = 0
                        ct = -1

                # -- agent decisions (component C) --
                if ct < 0:
                    # planting: strategy-ranked, gated by unlock (total_revenue) + afford
                    plot_state, u_pick = _next(plot_state)
                    crop_idx = -1
                    if strat == STRATEGY_GREEDY:
                        for i in range(num_crops):
                            c = greedy_rank[i]
                            if tr >= unlock_total_revenue[c] and m >= seed_cost[c]:
                                crop_idx = c
                                break
                    elif strat == STRATEGY_CONSERVATIVE:
                        for i in range(num_crops):
                            c = conservative_rank[i]
                            if tr >= unlock_total_revenue[c] and m >= seed_cost[c]:
                                crop_idx = c
                                break
                    else:
                        count = 0
                        for c in range(num_crops):
                            if tr >= unlock_total_revenue[c] and m >= seed_cost[c]:
                                count += 1
                        if count > 0:
                            pick = int(u_pick * count)
                            if pick >= count:
                                pick = count - 1
                            seen = 0
                            for c in range(num_crops):
                                if tr >= unlock_total_revenue[c] and m >= seed_cost[c]:
                                    if seen == pick:
                                        crop_idx = c
                                        break
                                    seen += 1
                    if crop_idx >= 0:
                        m -= seed_cost[crop_idx]
                        crop_type[r, p] = crop_idx
                        growth_stage[r, p] = 0
                        days_to_harvest[r, p] = growth_days[crop_idx]
                        last_watered_day[r, p] = day
                else:
                    # watering
                    plot_state, u_water = _next(plot_state)
                    moisture_now = np.float64(moisture[r, p])
                    if strat == STRATEGY_GREEDY:
                        should_water = moisture_now < WATER_THRESHOLD_GREEDY
                    elif strat == STRATEGY_CONSERVATIVE:
                        should_water = moisture_now < WATER_THRESHOLD_CONSERVATIVE
                    else:
                        should_water = u_water < COIN_FLIP
                    if should_water and m >= water_cost_per_plot:
                        m -= water_cost_per_plot
                        moisture[r, p] = np.float32(min(1.0, moisture_now + water_moisture_added))
                        last_watered_day[r, p] = day

                    # fertilizing (once per planting)
                    plot_state, u_fert = _next(plot_state)
                    if fertilized[r, p] == 0:
                        if strat == STRATEGY_GREEDY:
                            should_fert = m >= fertilizer_cost * FERTILIZE_CASH_BUFFER_GREEDY
                        elif strat == STRATEGY_CONSERVATIVE:
                            should_fert = m >= fertilizer_cost * FERTILIZE_CASH_BUFFER_CONSERVATIVE
                        else:
                            should_fert = u_fert < COIN_FLIP and m >= fertilizer_cost
                        if should_fert and m >= fertilizer_cost:
                            m -= fertilizer_cost
                            fertilized[r, p] = 1
                            nitrogen[r, p] = np.float32(
                                min(1.0, np.float64(nitrogen[r, p]) + fertilizer_nitrogen_added)
                            )
                            phosphorus[r, p] = np.float32(
                                min(1.0, np.float64(phosphorus[r, p]) + fertilizer_phosphorus_added)
                            )
                            potassium[r, p] = np.float32(
                                min(1.0, np.float64(potassium[r, p]) + fertilizer_potassium_added)
                            )

                rng_plot_state[r, p] = plot_state

            # -- storage: aging, quality downgrade, full-spoil
            # (simulation/inventory.py:age_and_spoil) -- run-level, once/day, after
            # today's harvests are in so same-day lots can be correctly skipped --
            for s in range(num_lot_slots):
                if lot_item_id[r, s] < 0:
                    continue
                if lot_age_days[r, s] < 0:
                    # produced today -- becomes 0 this pass, not aged further today
                    lot_age_days[r, s] = 0
                    continue
                lot_age_days[r, s] += 1
                item = lot_item_id[r, s]
                eff_life = effective_shelf_life_days[item]
                age_ratio = np.float64(lot_age_days[r, s]) / np.float64(eff_life)
                if age_ratio >= 1.0:
                    ts += lot_quantity[r, s]
                    lot_quantity[r, s] = 0
                    lot_item_id[r, s] = -1
                elif age_ratio >= 0.5 and lot_quality[r, s] == 3:
                    lot_quality[r, s] = 2
                elif age_ratio >= 0.8 and lot_quality[r, s] == 2:
                    lot_quality[r, s] = 1

            # -- storage: capacity trim, FEFO (simulation/inventory.py:_trim_to_capacity)
            # -- only sorts/mutates when something actually has to be trimmed, same
            # optimization the real engine documents: storage sits under capacity on
            # the overwhelming majority of days --
            total_qty = 0
            for s in range(num_lot_slots):
                if lot_item_id[r, s] >= 0:
                    total_qty += lot_quantity[r, s]
            overflow_units = total_qty - storage_capacity
            while overflow_units > 0:
                chosen = -1
                chosen_remaining = 0.0
                for s in range(num_lot_slots):
                    if lot_item_id[r, s] < 0:
                        continue
                    item = lot_item_id[r, s]
                    remaining = np.float64(effective_shelf_life_days[item]) - np.float64(
                        lot_age_days[r, s]
                    )
                    if chosen < 0 or remaining < chosen_remaining:
                        chosen = s
                        chosen_remaining = remaining
                if chosen < 0:
                    break  # no occupied slots left -- shouldn't happen if overflow_units > 0
                remove = min(overflow_units, lot_quantity[r, chosen])
                lot_quantity[r, chosen] -= remove
                overflow_units -= remove
                ts += remove
                if lot_quantity[r, chosen] == 0:
                    lot_item_id[r, chosen] = -1

            # -- storage: liability collect (simulation/inventory.py:collect_storage_liability)
            # -- end of day, using the amount captured before today's harvests; shadow
            # accounting: capped by (and reported against) `m`, but never subtracted
            # from it -- see kernel.py's module docstring for why. --
            charged = min(max(0.0, m), max(0.0, liability))
            tc += charged

        money[r] = np.float32(m)
        total_harvest[r] = np.float32(th)
        total_revenue[r] = np.float32(tr)
        total_spoiled[r] = np.float32(ts)
        total_storage_cost[r] = np.float32(tc)
        rng_run_state[r] = run_state


def simulate_chunk(
    state: BatchState, num_days: int, config: VectorConfig, master_seed: int | None = None
) -> None:
    """Advance `state` in place by `num_days`.

    Matches component B's requested signature `simulate_chunk(state, num_days,
    master_seed)`, with `config` added: the normal orchestrator flow is
    `state.allocate` -> `state.init_runs(config, master_seed, ...)` ->
    `simulate_chunk(state, num_days, config)`. `master_seed` is optional: pass
    it only for a self-seeding one-shot call that also re-runs `init_runs`.
    """
    if master_seed is not None:
        from vectorized import state as state_mod

        state_mod.init_runs(
            state,
            config,
            master_seed,
            run_index_offset=0,
            strategy_of_run=state.strategy_id,
        )

    overflow_events = np.zeros(state.num_runs, dtype=np.int32)

    _simulate_chunk_core(
        state.money,
        state.total_harvest,
        state.total_revenue,
        state.strategy_id,
        state.total_spoiled,
        state.total_storage_cost,
        state.moisture,
        state.nitrogen,
        state.phosphorus,
        state.potassium,
        state.ph,
        state.soil_health,
        state.pest_pressure,
        state.disease_pressure,
        state.crop_type,
        state.growth_stage,
        state.days_to_harvest,
        state.previous_crop_family,
        state.fertilized,
        state.water_stress,
        state.nutrient_stress,
        state.temperature_stress,
        state.pest_stress,
        state.disease_stress,
        state.neglect_days,
        state.last_watered_day,
        state.rng_run_state,
        state.rng_plot_state,
        state.lot_item_id,
        state.lot_quantity,
        state.lot_quality,
        state.lot_age_days,
        overflow_events,
        num_days,
        config.num_crops,
        config.seed_cost,
        config.growth_days,
        config.min_yield,
        config.max_yield,
        config.base_price,
        config.price_variation,
        config.loss_chance,
        config.water_interval_days,
        config.min_moisture,
        config.ph_low,
        config.ph_high,
        config.temperature_low,
        config.temperature_high,
        config.pest_susceptibility,
        config.disease_susceptibility,
        config.nitrogen_demand,
        config.phosphorus_demand,
        config.potassium_demand,
        config.family_id,
        config.unlock_total_revenue,
        config.effective_shelf_life_days,
        config.greedy_rank,
        config.conservative_rank,
        float(config.regen_moisture),
        float(config.regen_nitrogen),
        float(config.regen_phosphorus),
        float(config.regen_potassium),
        float(config.regen_soil_health),
        float(config.regen_pest_pressure),
        float(config.regen_disease_pressure),
        float(config.same_family_yield_penalty),
        float(config.same_family_quality_penalty),
        float(config.soil_health_yield_floor),
        float(config.soil_health_yield_span),
        float(config.harvest_soil_health_cost),
        float(config.min_soil_health),
        float(config.fallow_pest_decay),
        float(config.fallow_disease_decay),
        float(config.fallow_soil_health_regen),
        float(config.pest_growth_per_day),
        float(config.disease_growth_per_rainfall),
        float(config.max_pest_pressure),
        float(config.max_disease_pressure),
        float(config.water_cost_per_plot),
        float(config.water_moisture_added),
        float(config.neglect_loss_chance_penalty_per_day),
        float(config.max_neglect_loss_chance_bonus),
        float(config.neglect_yield_penalty_per_day),
        float(config.max_neglect_yield_penalty),
        float(config.fertilizer_cost),
        float(config.fertilizer_yield_bonus_pct),
        float(config.fertilizer_loss_chance_reduction),
        float(config.fertilizer_quality_bonus),
        float(config.fertilizer_nitrogen_added),
        float(config.fertilizer_phosphorus_added),
        float(config.fertilizer_potassium_added),
        config.season_length_days,
        config.season_temp_low,
        config.season_temp_high,
        config.season_rain_chance,
        config.season_rain_low,
        config.season_rain_high,
        config.season_evaporation,
        int(config.storage_capacity),
        float(config.storage_daily_cost),
    )

    if np.any(overflow_events):
        raise RuntimeError(
            "vectorized/kernel.py: lot-slot overflow -- a plot needed more concurrent "
            "storage lots than config_arrays.py's lots_per_plot bound provides for. "
            f"overflow_events={overflow_events[overflow_events > 0]!r} at run indices "
            f"{np.nonzero(overflow_events)[0]!r}. This means config/crops.json's "
            "shelf_life_days/growth_days ratio drifted past the assumption "
            "lots_per_plot was sized for -- see config_arrays.py's VectorConfig."
        )
