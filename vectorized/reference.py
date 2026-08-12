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
RNG scheme, still-simplified economy: no markets/contracts/processing/
upgrades, and storage is shadow accounting -- see kernel.py's module
docstring). It validates that the numba kernel is a faithful parallelization
of *this* module's sequential algorithm, which is itself a config-driven
port of simulation/crop_growth.py + simulation/weather.py's per-plot
mechanics, plus a Phase 2 storage/spoilage mirror of
simulation/inventory.py's age-and-spoil/capacity-trim/liability logic --
see kernel.py's per-block comments for which real function each block
mirrors.
"""

from __future__ import annotations

import numpy as np

from vectorized import kernel, rng
from vectorized.config_arrays import VectorConfig


def _f32(x: float) -> float:
    return float(np.float32(x))


def simulate_run_reference(
    config: VectorConfig,
    master_seed: int,
    run_index: int,
    strategy: int,
    num_plots: int,
    num_days: int,
) -> dict:
    """Sequentially simulate exactly one run. Returns final money/total_harvest/total_revenue."""
    run_state = rng.run_seed(master_seed, run_index)
    plot_state = [rng.plot_seed(run_state, p) for p in range(num_plots)]

    money = _f32(config.start_money)
    total_harvest = _f32(0.0)
    total_revenue = _f32(0.0)
    total_spoiled = _f32(0.0)
    total_storage_cost = _f32(0.0)

    moisture = [_f32(config.initial_moisture) for _ in range(num_plots)]
    nitrogen = [_f32(config.initial_nitrogen) for _ in range(num_plots)]
    phosphorus = [_f32(config.initial_phosphorus) for _ in range(num_plots)]
    potassium = [_f32(config.initial_potassium) for _ in range(num_plots)]
    ph = [_f32(config.initial_ph) for _ in range(num_plots)]
    soil_health = [_f32(config.initial_soil_health) for _ in range(num_plots)]
    pest_pressure = [_f32(config.initial_pest_pressure) for _ in range(num_plots)]
    disease_pressure = [_f32(config.initial_disease_pressure) for _ in range(num_plots)]

    crop_type = [-1] * num_plots
    growth_stage = [0] * num_plots
    days_to_harvest = [0] * num_plots
    previous_crop_family = [-1] * num_plots
    fertilized = [0] * num_plots

    water_stress = [0.0] * num_plots
    nutrient_stress = [0.0] * num_plots
    temperature_stress = [0.0] * num_plots
    pest_stress = [0.0] * num_plots
    disease_stress = [0.0] * num_plots

    neglect_days = [0] * num_plots
    last_watered_day = [0] * num_plots

    # -- storage lots (Phase 2), fixed-size list mirroring kernel.py's (B, L) array --
    num_lot_slots = num_plots * config.lots_per_plot
    lot_item_id = [-1] * num_lot_slots
    lot_quantity = [0] * num_lot_slots
    lot_quality = [0] * num_lot_slots
    lot_age_days = [0] * num_lot_slots

    num_seasons = len(config.season_rain_chance)

    for day in range(num_days):
        season = (day // config.season_length_days) % num_seasons
        run_state, u_temp = rng.next_scalar(run_state)
        temperature = config.season_temp_low[season] + u_temp * (
            config.season_temp_high[season] - config.season_temp_low[season]
        )
        run_state, u_rain_chance = rng.next_scalar(run_state)
        if u_rain_chance < config.season_rain_chance[season]:
            run_state, u_rain_amt = rng.next_scalar(run_state)
            rainfall = config.season_rain_low[season] + u_rain_amt * (
                config.season_rain_high[season] - config.season_rain_low[season]
            )
        else:
            rainfall = 0.0
        evaporation = config.season_evaporation[season] + max(0.0, temperature - 25.0) * 0.005

        # -- storage liability capture (simulation/inventory.py:capture_storage_liability)
        # -- once/day, before today's harvests are added -- see kernel.py's mirror --
        has_inventory = any(q > 0 for q in lot_quantity)
        liability = float(config.storage_daily_cost) if has_inventory else 0.0

        for p in range(num_plots):
            ps = plot_state[p]
            ct = crop_type[p]

            moist = moisture[p] + float(config.regen_moisture) + rainfall
            if config.regen_nitrogen:
                nitrogen[p] = _f32(min(1.0, nitrogen[p] + float(config.regen_nitrogen)))
            if config.regen_phosphorus:
                phosphorus[p] = _f32(min(1.0, phosphorus[p] + float(config.regen_phosphorus)))
            if config.regen_potassium:
                potassium[p] = _f32(min(1.0, potassium[p] + float(config.regen_potassium)))
            if config.regen_soil_health:
                soil_health[p] = _f32(min(1.0, soil_health[p] + float(config.regen_soil_health)))
            if config.regen_pest_pressure:
                pest_pressure[p] = _f32(
                    max(0.0, pest_pressure[p] - float(config.regen_pest_pressure))
                )
            if config.regen_disease_pressure:
                disease_pressure[p] = _f32(
                    max(0.0, disease_pressure[p] - float(config.regen_disease_pressure))
                )

            if ct < 0:
                moisture[p] = _f32(max(0.0, min(1.0, moist - evaporation)))
                pest_pressure[p] = _f32(
                    max(0.0, pest_pressure[p] * float(config.fallow_pest_decay))
                )
                disease_pressure[p] = _f32(
                    max(0.0, disease_pressure[p] * float(config.fallow_disease_decay))
                )
                soil_health[p] = _f32(
                    min(1.0, soil_health[p] + float(config.fallow_soil_health_regen))
                )
            else:
                water_stress[p] = _f32(
                    water_stress[p] + max(0.0, float(config.min_moisture[ct]) - moist)
                )
                n_short = max(0.0, float(config.nitrogen_demand[ct]) - nitrogen[p])
                p_short = max(0.0, float(config.phosphorus_demand[ct]) - phosphorus[p])
                k_short = max(0.0, float(config.potassium_demand[ct]) - potassium[p])
                ph_stress = 0.0
                if ph[p] < config.ph_low[ct]:
                    ph_stress = (float(config.ph_low[ct]) - ph[p]) * 0.1
                elif ph[p] > config.ph_high[ct]:
                    ph_stress = (ph[p] - float(config.ph_high[ct])) * 0.1
                nutrient_stress[p] = _f32(
                    nutrient_stress[p] + n_short + p_short + k_short + ph_stress
                )
                if temperature < config.temperature_low[ct]:
                    temperature_stress[p] = _f32(
                        temperature_stress[p]
                        + (float(config.temperature_low[ct]) - temperature) / 20.0
                    )
                elif temperature > config.temperature_high[ct]:
                    temperature_stress[p] = _f32(
                        temperature_stress[p]
                        + (temperature - float(config.temperature_high[ct])) / 20.0
                    )
                pest_stress[p] = _f32(
                    pest_stress[p] + pest_pressure[p] * float(config.pest_susceptibility[ct])
                )
                disease_stress[p] = _f32(
                    disease_stress[p]
                    + disease_pressure[p] * float(config.disease_susceptibility[ct])
                )

                moisture[p] = _f32(max(0.0, min(1.0, moist - evaporation)))
                nitrogen[p] = _f32(
                    max(0.0, min(1.0, nitrogen[p] - float(config.nitrogen_demand[ct])))
                )
                phosphorus[p] = _f32(
                    max(0.0, min(1.0, phosphorus[p] - float(config.phosphorus_demand[ct])))
                )
                potassium[p] = _f32(
                    max(0.0, min(1.0, potassium[p] - float(config.potassium_demand[ct])))
                )

                overdue = day - last_watered_day[p] - int(config.water_interval_days[ct])
                neglect_days[p] = max(0, overdue)

                disease_pressure[p] = _f32(
                    min(
                        float(config.max_disease_pressure),
                        disease_pressure[p] + rainfall * float(config.disease_growth_per_rainfall),
                    )
                )
                pest_pressure[p] = _f32(
                    min(
                        float(config.max_pest_pressure),
                        pest_pressure[p] + float(config.pest_growth_per_day),
                    )
                )

                growth_stage[p] += 1
                days_to_harvest[p] -= 1

                if days_to_harvest[p] <= 0:
                    env_stress = (
                        water_stress[p] * 0.16
                        + nutrient_stress[p] * 0.18
                        + temperature_stress[p] * 0.12
                        + pest_stress[p] * 0.10
                        + disease_stress[p] * 0.12
                    )
                    quality_stress = env_stress + neglect_days[p] * 0.08
                    yield_mult = min(1.35, max(0.15, 1.0 - env_stress))
                    quality_mult = min(1.2, max(0.0, 1.0 - quality_stress * 1.25))
                    if fertilized[p]:
                        quality_mult += float(config.fertilizer_quality_bonus)

                    fam = int(config.family_id[ct])
                    if previous_crop_family[p] == fam:
                        yield_mult *= float(config.same_family_yield_penalty)
                        quality_mult *= float(config.same_family_quality_penalty)
                    yield_mult *= float(config.soil_health_yield_floor) + soil_health[p] * float(
                        config.soil_health_yield_span
                    )
                    yield_mult = min(1.5, max(0.1, yield_mult))
                    quality_mult = min(1.25, max(0.0, quality_mult))

                    loss_bonus = min(
                        neglect_days[p] * float(config.neglect_loss_chance_penalty_per_day),
                        float(config.max_neglect_loss_chance_bonus),
                    )
                    lc = float(config.loss_chance[ct]) + loss_bonus
                    if fertilized[p]:
                        lc -= float(config.fertilizer_loss_chance_reduction)
                    lc = min(0.95, max(0.0, lc))

                    ps, u_loss = rng.next_scalar(ps)
                    ps, u_yield = rng.next_scalar(ps)
                    ps, u_price = rng.next_scalar(ps)

                    if u_loss >= lc:
                        min_y, max_y = int(config.min_yield[ct]), int(config.max_yield[ct])
                        base_y = min_y + int(u_yield * (max_y - min_y + 1))
                        base_y = min(base_y, max_y)
                        if fertilized[p]:
                            yield_mult = min(
                                1.5, max(0.1, yield_mult + float(config.fertilizer_yield_bonus_pct))
                            )
                        neglect_penalty = min(
                            neglect_days[p] * float(config.neglect_yield_penalty_per_day),
                            float(config.max_neglect_yield_penalty),
                        )
                        amount = base_y * yield_mult * (1.0 - neglect_penalty)
                        amount_units = int(amount + 0.5) if amount > 0.0 else 0
                        if amount_units > 0 and quality_mult >= 0.3:
                            price_factor = 1.0 + float(config.price_variation[ct]) * (
                                2.0 * u_price - 1.0
                            )
                            price = max(0.01, float(config.base_price[ct]) * price_factor)
                            revenue = amount_units * price
                            money = _f32(money + revenue)
                            total_revenue = _f32(total_revenue + revenue)
                            total_harvest = _f32(total_harvest + amount_units)

                            # -- storage: shadow lot (simulation/actions.py:harvest_mature)
                            # -- shadow accounting, see kernel.py's module docstring --
                            if quality_mult >= 0.9:
                                grade = 3  # premium
                            elif quality_mult >= 0.62:
                                grade = 2  # standard
                            else:
                                grade = 1  # processing (>= 0.3 guaranteed by the outer gate)
                            for s in range(num_lot_slots):
                                if lot_item_id[s] < 0:
                                    lot_item_id[s] = ct
                                    lot_quantity[s] = amount_units
                                    lot_quality[s] = grade
                                    lot_age_days[s] = -1  # becomes 0 on today's aging pass
                                    break
                            # if no empty slot: silently dropped, matching kernel.py's
                            # overflow_events counter path (checked kernel-side only)

                    previous_crop_family[p] = fam
                    soil_health[p] = _f32(
                        max(
                            float(config.min_soil_health),
                            soil_health[p] - float(config.harvest_soil_health_cost),
                        )
                    )
                    crop_type[p] = -1
                    growth_stage[p] = 0
                    days_to_harvest[p] = 0
                    water_stress[p] = 0.0
                    nutrient_stress[p] = 0.0
                    temperature_stress[p] = 0.0
                    pest_stress[p] = 0.0
                    disease_stress[p] = 0.0
                    neglect_days[p] = 0
                    fertilized[p] = 0
                    ct = -1

            if ct < 0:
                ps, u_pick = rng.next_scalar(ps)
                crop_idx = -1
                if strategy == kernel.STRATEGY_GREEDY:
                    for c in config.greedy_rank:
                        c = int(c)
                        if (
                            total_revenue >= config.unlock_total_revenue[c]
                            and money >= config.seed_cost[c]
                        ):
                            crop_idx = c
                            break
                elif strategy == kernel.STRATEGY_CONSERVATIVE:
                    for c in config.conservative_rank:
                        c = int(c)
                        if (
                            total_revenue >= config.unlock_total_revenue[c]
                            and money >= config.seed_cost[c]
                        ):
                            crop_idx = c
                            break
                else:
                    candidates = [
                        c
                        for c in range(config.num_crops)
                        if total_revenue >= config.unlock_total_revenue[c]
                        and money >= config.seed_cost[c]
                    ]
                    if candidates:
                        pick = min(int(u_pick * len(candidates)), len(candidates) - 1)
                        crop_idx = candidates[pick]
                if crop_idx >= 0:
                    money = _f32(money - float(config.seed_cost[crop_idx]))
                    crop_type[p] = crop_idx
                    growth_stage[p] = 0
                    days_to_harvest[p] = int(config.growth_days[crop_idx])
                    last_watered_day[p] = day
            else:
                ps, u_water = rng.next_scalar(ps)
                if strategy == kernel.STRATEGY_GREEDY:
                    should_water = moisture[p] < kernel.WATER_THRESHOLD_GREEDY
                elif strategy == kernel.STRATEGY_CONSERVATIVE:
                    should_water = moisture[p] < kernel.WATER_THRESHOLD_CONSERVATIVE
                else:
                    should_water = u_water < kernel.COIN_FLIP
                if should_water and money >= float(config.water_cost_per_plot):
                    money = _f32(money - float(config.water_cost_per_plot))
                    moisture[p] = _f32(min(1.0, moisture[p] + float(config.water_moisture_added)))
                    last_watered_day[p] = day

                ps, u_fert = rng.next_scalar(ps)
                if fertilized[p] == 0:
                    fert_cost = float(config.fertilizer_cost)
                    if strategy == kernel.STRATEGY_GREEDY:
                        should_fert = money >= fert_cost * kernel.FERTILIZE_CASH_BUFFER_GREEDY
                    elif strategy == kernel.STRATEGY_CONSERVATIVE:
                        should_fert = money >= fert_cost * kernel.FERTILIZE_CASH_BUFFER_CONSERVATIVE
                    else:
                        should_fert = u_fert < kernel.COIN_FLIP and money >= fert_cost
                    if should_fert and money >= fert_cost:
                        money = _f32(money - fert_cost)
                        fertilized[p] = 1
                        nitrogen[p] = _f32(
                            min(1.0, nitrogen[p] + float(config.fertilizer_nitrogen_added))
                        )
                        phosphorus[p] = _f32(
                            min(1.0, phosphorus[p] + float(config.fertilizer_phosphorus_added))
                        )
                        potassium[p] = _f32(
                            min(1.0, potassium[p] + float(config.fertilizer_potassium_added))
                        )

            plot_state[p] = ps

        # -- storage: aging, quality downgrade, full-spoil
        # (simulation/inventory.py:age_and_spoil) -- once/day, after today's harvests
        # are in so same-day lots can be correctly skipped -- see kernel.py's mirror --
        for s in range(num_lot_slots):
            if lot_item_id[s] < 0:
                continue
            if lot_age_days[s] < 0:
                # produced today -- becomes 0 this pass, not aged further today
                lot_age_days[s] = 0
                continue
            lot_age_days[s] += 1
            item = lot_item_id[s]
            eff_life = int(config.effective_shelf_life_days[item])
            age_ratio = lot_age_days[s] / eff_life
            if age_ratio >= 1.0:
                total_spoiled = _f32(total_spoiled + lot_quantity[s])
                lot_quantity[s] = 0
                lot_item_id[s] = -1
            elif age_ratio >= 0.5 and lot_quality[s] == 3:
                lot_quality[s] = 2
            elif age_ratio >= 0.8 and lot_quality[s] == 2:
                lot_quality[s] = 1

        # -- storage: capacity trim, FEFO (simulation/inventory.py:_trim_to_capacity)
        # -- only sorts/mutates when something actually has to be trimmed --
        total_qty = sum(lot_quantity[s] for s in range(num_lot_slots) if lot_item_id[s] >= 0)
        overflow_units = total_qty - int(config.storage_capacity)
        while overflow_units > 0:
            chosen = -1
            chosen_remaining = 0.0
            for s in range(num_lot_slots):
                if lot_item_id[s] < 0:
                    continue
                item = lot_item_id[s]
                remaining = float(config.effective_shelf_life_days[item]) - float(lot_age_days[s])
                if chosen < 0 or remaining < chosen_remaining:
                    chosen = s
                    chosen_remaining = remaining
            if chosen < 0:
                break  # no occupied slots left -- shouldn't happen if overflow_units > 0
            remove = min(overflow_units, lot_quantity[chosen])
            lot_quantity[chosen] -= remove
            overflow_units -= remove
            total_spoiled = _f32(total_spoiled + remove)
            if lot_quantity[chosen] == 0:
                lot_item_id[chosen] = -1

        # -- storage: liability collect (simulation/inventory.py:collect_storage_liability)
        # -- end of day, using the amount captured before today's harvests; shadow
        # accounting: capped by (and reported against) `money`, but never subtracted
        # from it -- see kernel.py's module docstring for why. --
        charged = min(max(0.0, money), max(0.0, liability))
        total_storage_cost = _f32(total_storage_cost + charged)

    return {
        "money": money,
        "total_harvest": total_harvest,
        "total_revenue": total_revenue,
        "total_spoiled": total_spoiled,
        "total_storage_cost": total_storage_cost,
    }
