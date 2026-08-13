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
RNG scheme, still-simplified economy: markets are single-channel, and
contracts skip the real production-forecast scheduler -- see kernel.py's
module docstring). It validates that the numba kernel is a faithful
parallelization of *this* module's sequential algorithm, which is itself a
config-driven port of simulation/crop_growth.py + simulation/weather.py's
per-plot mechanics, plus a Phase 2 storage/spoilage mirror of
simulation/inventory.py's age-and-spoil/capacity-trim/liability logic, a
Phase 3 single-channel mirror of simulation/markets.py's daily pricing, a
Phase 4 mirror of simulation/processing.py's recipe/job mechanics, a
Phase 5 simplified mirror of simulation/contracts.py's offer/accept/
deliver/resolve mechanics, and a Phase 7 mirror of `config/upgrades.json`'s
four effect types (see kernel.py's module docstring for the
active_plots/active_job_slots gating this needs) -- see kernel.py's
per-block comments for which real function each block mirrors.
"""

from __future__ import annotations

import numpy as np

from vectorized import kernel, rng
from vectorized.config_arrays import VectorConfig


def _f32(x: float) -> float:
    return float(np.float32(x))


def _insert_lot(
    lot_item_id,
    lot_quantity,
    lot_quality,
    lot_age_days,
    occupied,
    slot_pos,
    free,
    item,
    qty,
    quality,
    age,
) -> bool:
    """Insert a new lot via the free-list, O(1) instead of scanning for the
    first empty slot. Scalar mirror of kernel.py's `_insert_lot` -- see its
    docstring. `occupied`/`free` are plain Python lists here (their length
    *is* the count kernel.py tracks separately as `occupied_count`/
    `free_count`, since numpy arrays can't grow but Python lists can) --
    `free.pop()` removes the *last* free index, matching kernel.py's
    `free_count -= 1; s = free[free_count]` exactly, so both implementations
    hand out slots in the same order. Mutates the lot-field lists and
    `occupied`/`slot_pos`/`free` in place; returns whether it succeeded.
    """
    if not free:
        return False
    s = free.pop()
    lot_item_id[s] = item
    lot_quantity[s] = qty
    lot_quality[s] = quality
    lot_age_days[s] = age
    slot_pos[s] = len(occupied)
    occupied.append(s)
    return True


def _remove_lot(lot_item_id, occupied, slot_pos, free, s) -> None:
    """Remove slot `s` via swap-remove, O(1) instead of leaving a hole.
    Scalar mirror of kernel.py's `_remove_lot` -- see its docstring for the
    "don't advance the iteration index after calling this" discipline every
    call site below follows.
    """
    pos = slot_pos[s]
    last_slot = occupied[-1]
    occupied[pos] = last_slot
    slot_pos[last_slot] = pos
    occupied.pop()
    slot_pos[s] = -1
    free.append(s)
    lot_item_id[s] = -1


def _trim_to_capacity(
    lot_item_id, lot_quantity, lot_age_days, eff_life, occupied, slot_pos, free, capacity
) -> int:
    """FEFO capacity trim for one run's lot-slot lists, in place.

    Scalar mirror of kernel.py's `_trim_to_capacity` -- see its docstring.
    Walks `occupied`, not every slot. Mutates the lot-field lists and
    `occupied`/`slot_pos`/`free` in place (Python lists, passed by
    reference) and returns units spoiled by trimming.
    """
    total_qty = sum(lot_quantity[s] for s in occupied)
    overflow_units = total_qty - int(capacity)
    spoiled = 0
    while overflow_units > 0:
        chosen = -1
        chosen_remaining = 0.0
        for s in occupied:
            item = lot_item_id[s]
            remaining = float(eff_life[item]) - float(lot_age_days[s])
            if chosen < 0 or remaining < chosen_remaining:
                chosen = s
                chosen_remaining = remaining
        if chosen < 0:
            break  # no occupied slots left -- shouldn't happen if overflow_units > 0
        remove = min(overflow_units, lot_quantity[chosen])
        lot_quantity[chosen] -= remove
        overflow_units -= remove
        spoiled += remove
        if lot_quantity[chosen] == 0:
            _remove_lot(lot_item_id, occupied, slot_pos, free, chosen)
    return spoiled


def simulate_run_reference(
    config: VectorConfig,
    master_seed: int,
    run_index: int,
    strategy: int,
    num_plots: int,
    num_days: int,
) -> dict:
    """Sequentially simulate exactly one run. Returns final money/total_harvest/total_revenue.

    `num_plots` is the starting (pre-upgrade) plot count -- Phase 7's
    active_plots gating means the plot-level lists below are actually sized
    at `num_plots_max = num_plots + config.total_capacity_bonus`, the same
    max-width allocation kernel.py's caller (state.allocate) does; see this
    module's and kernel.py's docstrings.
    """
    num_plots_max = num_plots + config.total_capacity_bonus
    num_job_slots_max = config.base_capacity + config.total_processing_capacity_bonus

    run_state = rng.run_seed(master_seed, run_index)
    plot_state = [rng.plot_seed(run_state, p) for p in range(num_plots_max)]

    money = _f32(config.start_money)
    total_harvest = _f32(0.0)
    total_revenue = _f32(0.0)
    total_spoiled = _f32(0.0)
    total_storage_cost = _f32(0.0)
    total_processed = _f32(0.0)
    reputation = _f32(0.0)
    total_contracts_completed = _f32(0.0)
    total_contracts_failed = _f32(0.0)
    total_contract_penalties = _f32(0.0)

    moisture = [_f32(config.initial_moisture) for _ in range(num_plots_max)]
    nitrogen = [_f32(config.initial_nitrogen) for _ in range(num_plots_max)]
    phosphorus = [_f32(config.initial_phosphorus) for _ in range(num_plots_max)]
    potassium = [_f32(config.initial_potassium) for _ in range(num_plots_max)]
    ph = [_f32(config.initial_ph) for _ in range(num_plots_max)]
    soil_health = [_f32(config.initial_soil_health) for _ in range(num_plots_max)]
    pest_pressure = [_f32(config.initial_pest_pressure) for _ in range(num_plots_max)]
    disease_pressure = [_f32(config.initial_disease_pressure) for _ in range(num_plots_max)]

    crop_type = [-1] * num_plots_max
    growth_stage = [0] * num_plots_max
    days_to_harvest = [0] * num_plots_max
    previous_crop_family = [-1] * num_plots_max
    fertilized = [0] * num_plots_max

    water_stress = [0.0] * num_plots_max
    nutrient_stress = [0.0] * num_plots_max
    temperature_stress = [0.0] * num_plots_max
    pest_stress = [0.0] * num_plots_max
    disease_stress = [0.0] * num_plots_max

    neglect_days = [0] * num_plots_max
    last_watered_day = [0] * num_plots_max

    # Phase 7: how many of the (max-width) plot/job-slot columns this run has
    # unlocked so far -- see kernel.py's and state.py's docstrings.
    active_plots = num_plots
    active_job_slots = config.base_capacity
    upgrade_owned = [0] * config.num_upgrades

    # -- storage lots (Phase 2), fixed-size list mirroring kernel.py's (B, L) array --
    # -- Phase 4/7 reserve +num_job_slots_max slots for processed-product lots --
    num_lot_slots = num_plots_max * config.lots_per_plot + num_job_slots_max
    lot_item_id = [-1] * num_lot_slots
    lot_quantity = [0] * num_lot_slots
    lot_quality = [0] * num_lot_slots
    lot_age_days = [0] * num_lot_slots

    # Occupied/free-list bookkeeping for the lot-slot lists above (see
    # _insert_lot's docstring) -- every slot starts empty, so free starts
    # holding every index (in the same order kernel.py's array version
    # does) and occupied starts empty.
    occupied: list = []
    slot_pos = [-1] * num_lot_slots
    free = list(range(num_lot_slots))

    # -- markets (Phase 3): per-run scratch, reset fresh each run, not
    # persisted in the returned dict -- see kernel.py's mirror -- item-space
    # (crops + processed products, Phase 4) --
    market_supply = [0.0] * config.num_items
    today_price = [0.0] * config.num_items
    # Phase 7: this run's effective per-item shelf life, rebuilt each day --
    # see kernel.py's mirror.
    eff_life_by_item = [0.0] * config.num_items

    # -- processing job slots (Phase 4/7), fixed-size list mirroring kernel.py's
    # (B, J) array, J = num_job_slots_max --
    num_job_slots = num_job_slots_max
    job_output_item_id = [-1] * num_job_slots
    job_output_quantity = [0] * num_job_slots
    job_completion_day = [0] * num_job_slots

    # -- per-buyer contract slots (Phase 5), fixed-size lists mirroring
    # kernel.py's (B, K) arrays, K = config.num_buyers -- one slot per buyer --
    num_buyers = config.num_buyers
    contract_state = [0] * num_buyers
    contract_item_idx = [-1] * num_buyers
    contract_remaining = [0] * num_buyers
    contract_unit_price = [0.0] * num_buyers
    contract_min_quality_rank = [0] * num_buyers
    contract_deadline_day = [0] * num_buyers
    contract_expiry_day = [0] * num_buyers
    contract_penalty_rate = [0.0] * num_buyers
    buyer_relationship = [0.0] * num_buyers

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

        # -- markets: daily price roll + supply decay
        # (simulation/markets.py:update_daily_prices) -- single-channel scope,
        # item-space (crops + processed products, Phase 4) -- see kernel.py's
        # module docstring --
        for c in range(config.num_items):
            seasonal = float(config.seasonal_demand[c, season])
            supply = market_supply[c]
            saturation = max(float(config.market_minimum_supply_multiplier), 1.0 - supply * 0.01)
            run_state, u_price = rng.next_scalar(run_state)
            variation = float(config.price_variation[c])
            price_factor = (1.0 - variation) + u_price * (2.0 * variation)
            today_price[c] = max(
                0.01, float(config.base_price[c]) * seasonal * saturation * price_factor
            )
            market_supply[c] = supply * float(config.market_supply_decay)

        # -- upgrades: agent buys (component C's should_buy_upgrade, simplified
        # -- see kernel.py's module docstring) -- once/day, config-catalog
        # order, one shared `money` pool --
        for u in range(config.num_upgrades):
            run_state, u_buy = rng.next_scalar(run_state)
            if upgrade_owned[u]:
                continue
            cost = float(config.upgrade_cost[u])
            if strategy == kernel.STRATEGY_GREEDY:
                should_buy = money >= cost * kernel.UPGRADE_CASH_BUFFER_GREEDY
            elif strategy == kernel.STRATEGY_CONSERVATIVE:
                should_buy = money >= cost * kernel.UPGRADE_CASH_BUFFER_CONSERVATIVE
            else:
                should_buy = u_buy < kernel.COIN_FLIP and money >= cost
            if should_buy:
                money = _f32(money - cost)
                upgrade_owned[u] = 1
                active_plots += int(config.upgrade_capacity_amount[u])
                active_job_slots += int(config.upgrade_processing_capacity_amount[u])

        # -- upgrades: fold owned storage upgrades into this run's effective
        # capacity/shelf-life-multiplier -- see kernel.py's mirror --
        eff_capacity = int(config.storage_capacity)
        eff_shelf_mult = float(config.storage_shelf_life_multiplier)
        for u in range(config.num_upgrades):
            if upgrade_owned[u]:
                eff_capacity += int(config.upgrade_storage_capacity_bonus[u])
                eff_shelf_mult *= float(config.upgrade_storage_shelf_life_multiplier[u])
        for item in range(config.num_items):
            eff_life_by_item[item] = max(
                1.0, round(float(config.shelf_life_days_item[item]) * eff_shelf_mult)
            )

        # -- storage liability capture (simulation/inventory.py:capture_storage_liability)
        # -- once/day, before today's harvests are added -- see kernel.py's mirror --
        liability = float(config.storage_daily_cost) if occupied else 0.0

        for p in range(num_plots_max):
            if p >= active_plots:
                # Not unlocked yet -- see kernel.py's mirror.
                continue
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
                            # -- storage: lot (simulation/actions.py:harvest_mature) --
                            # no money credited here -- markets: sell all matured lots,
                            # after this day's aging/spoilage/trim, is what pays for it.
                            if quality_mult >= 0.9:
                                grade = 3  # premium
                            elif quality_mult >= 0.62:
                                grade = 2  # standard
                            else:
                                grade = 1  # processing (>= 0.3 guaranteed by the outer gate)
                            _insert_lot(
                                lot_item_id,
                                lot_quantity,
                                lot_quality,
                                lot_age_days,
                                occupied,
                                slot_pos,
                                free,
                                ct,
                                amount_units,
                                grade,
                                -1,  # becomes 0 on today's aging pass
                            )
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
                    # Phase 7: fold owned growth_time_reduction upgrades in at
                    # planting time only -- see kernel.py's mirror.
                    growth_mult = 1.0
                    for u in range(config.num_upgrades):
                        if upgrade_owned[u]:
                            growth_mult *= 1.0 - float(config.upgrade_growth_time_reduction[u])
                    days_to_harvest[p] = max(
                        1, int(round(config.growth_days[crop_idx] * growth_mult))
                    )
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
        # walks `occupied`, not every slot. A full-spoil removal swaps the last
        # occupied slot into index i (see _remove_lot's docstring), so i is
        # deliberately NOT advanced on that branch.
        i = 0
        while i < len(occupied):
            s = occupied[i]
            if lot_age_days[s] < 0:
                # produced today -- becomes 0 this pass, not aged further today
                lot_age_days[s] = 0
                i += 1
                continue
            lot_age_days[s] += 1
            item = lot_item_id[s]
            eff_life = eff_life_by_item[item]
            age_ratio = lot_age_days[s] / eff_life
            if age_ratio >= 1.0:
                total_spoiled = _f32(total_spoiled + lot_quantity[s])
                lot_quantity[s] = 0
                _remove_lot(lot_item_id, occupied, slot_pos, free, s)
                continue
            elif age_ratio >= 0.5 and lot_quality[s] == 3:
                lot_quality[s] = 2
            elif age_ratio >= 0.8 and lot_quality[s] == 2:
                lot_quality[s] = 1
            i += 1

        # -- storage: capacity trim, FEFO (simulation/inventory.py:_trim_to_capacity)
        # -- only sorts/mutates when something actually has to be trimmed --
        total_spoiled = _f32(
            total_spoiled
            + _trim_to_capacity(
                lot_item_id,
                lot_quantity,
                lot_age_days,
                eff_life_by_item,
                occupied,
                slot_pos,
                free,
                eff_capacity,
            )
        )

        # -- processing: jobs complete (simulation/processing.py:complete_jobs)
        # -- once/day, after today's aging/trim so a completing job's output is
        # unambiguously "produced today" -- see kernel.py's mirror --
        for j in range(num_job_slots):
            if job_output_item_id[j] < 0:
                continue
            if day < job_completion_day[j]:
                continue
            out_item = job_output_item_id[j]
            out_qty = job_output_quantity[j]
            total_processed = _f32(total_processed + out_qty)
            _insert_lot(
                lot_item_id,
                lot_quantity,
                lot_quality,
                lot_age_days,
                occupied,
                slot_pos,
                free,
                out_item,
                out_qty,
                2,  # standard -- matches complete_jobs' hardcoded grade
                -1,  # becomes 0 on tomorrow's aging pass
            )
            # if no empty slot: silently dropped, matching kernel.py's
            # overflow_events counter path (checked kernel-side only)
            job_output_item_id[j] = -1
            job_output_quantity[j] = 0

        # -- storage: same-day re-trim (simulation/inventory.py:enforce_storage_capacity)
        # -- see kernel.py's mirror for why a completing job's output needs its own
        # same-day trim pass, not just tomorrow's regular one --
        total_spoiled = _f32(
            total_spoiled
            + _trim_to_capacity(
                lot_item_id,
                lot_quantity,
                lot_age_days,
                eff_life_by_item,
                occupied,
                slot_pos,
                free,
                eff_capacity,
            )
        )

        # -- contracts: offer generation (simulation/contracts.py:generate_offers)
        # -- only on interval days, simplified scope (see kernel.py's module
        # docstring) -- one contract "slot" per buyer --
        if day != 0 and day % config.contract_offer_interval_days == 0:
            for b in range(num_buyers):
                if contract_state[b] != 0:
                    continue  # buyer already has an offer or active contract
                if reputation < float(config.buyer_min_reputation[b]):
                    continue
                n_elig = int(config.buyer_num_items[b])
                run_state, u_item = rng.next_scalar(run_state)
                pick = int(u_item * n_elig)
                if pick >= n_elig:
                    pick = n_elig - 1
                item_idx = int(config.buyer_item_idx[b, pick])
                run_state, u_qty = rng.next_scalar(run_state)
                qmin = int(config.buyer_quantity_min[b])
                qmax = int(config.buyer_quantity_max[b])
                quantity = qmin + int(u_qty * (qmax - qmin + 1))
                if quantity > qmax:
                    quantity = qmax
                relationship_mult = 1.0 + min(
                    float(config.contract_relationship_bonus_cap),
                    buyer_relationship[b] * float(config.buyer_relationship_bonus_rate[b]),
                )
                price = (
                    float(config.base_price[item_idx])
                    * float(config.buyer_price_multiplier[b])
                    * relationship_mult
                )
                contract_state[b] = 1
                contract_item_idx[b] = item_idx
                contract_remaining[b] = quantity
                contract_unit_price[b] = price
                contract_min_quality_rank[b] = int(config.buyer_min_quality_rank[b])
                contract_deadline_day[b] = day + int(config.buyer_deadline_days[b])
                contract_expiry_day[b] = day + config.contract_offer_expiry_days
                contract_penalty_rate[b] = float(config.buyer_penalty_rate[b])

        # -- contracts: accept + deliver + resolve (component C's
        # choose_contracts/choose_contract_deliveries, simplified
        # is_offer_feasible -- see kernel.py's module docstring) -- every day a
        # slot is offered or active --
        for b in range(num_buyers):
            cstate = contract_state[b]
            if cstate == 0:
                continue
            item = contract_item_idx[b]
            min_rank = contract_min_quality_rank[b]
            if cstate == 1:
                if day > contract_expiry_day[b]:
                    contract_state[b] = 0
                    continue
                # available/delivered scans walk occupied, not every slot --
                # see kernel.py's mirror.
                available = sum(
                    lot_quantity[s]
                    for s in occupied
                    if lot_item_id[s] == item and lot_quality[s] >= min_rank
                )
                if available <= 0:
                    continue  # still just offered, try again tomorrow (until expiry)
                contract_state[b] = 2
                cstate = 2
            # cstate == 2: active -- attempt delivery from today's inventory
            remaining = contract_remaining[b]
            delivered = 0
            i = 0
            while i < len(occupied) and delivered < remaining:
                s = occupied[i]
                if lot_item_id[s] == item and lot_quality[s] >= min_rank:
                    take = min(remaining - delivered, lot_quantity[s])
                    lot_quantity[s] -= take
                    delivered += take
                    if lot_quantity[s] == 0:
                        # Swap-remove moves the last occupied slot into i, so i
                        # is deliberately NOT advanced -- see kernel.py's mirror.
                        _remove_lot(lot_item_id, occupied, slot_pos, free, s)
                        continue
                i += 1
            if delivered > 0:
                revenue = delivered * contract_unit_price[b]
                money = _f32(money + revenue)
                total_revenue = _f32(total_revenue + revenue)
                total_harvest = _f32(total_harvest + delivered)
                remaining -= delivered
                contract_remaining[b] = remaining
            if remaining <= 0:
                contract_state[b] = 0
                reputation = _f32(min(100.0, reputation + 5.0))
                buyer_relationship[b] = min(
                    100.0, buyer_relationship[b] + float(config.contract_relationship_gain)
                )
                total_contracts_completed = _f32(total_contracts_completed + 1)
            elif day > contract_deadline_day[b]:
                shortfall_value = remaining * contract_unit_price[b]
                penalty = min(max(0.0, money), max(0.0, shortfall_value * contract_penalty_rate[b]))
                money = _f32(money - penalty)
                total_contract_penalties = _f32(total_contract_penalties + penalty)
                reputation = _f32(max(0.0, reputation - 4.0))
                buyer_relationship[b] = max(
                    0.0, buyer_relationship[b] - float(config.contract_relationship_loss)
                )
                contract_state[b] = 0
                total_contracts_failed = _f32(total_contracts_failed + 1)

        # -- processing: agent starts jobs (component C's choose_processing,
        # simplified -- see kernel.py's module docstring) -- fixed recipe-order
        # preference, same policy for all 3 strategies --
        for rec in range(config.num_recipes):
            free_slot = -1
            # Bounded by active_job_slots (Phase 7), not num_job_slots -- see
            # kernel.py's mirror.
            for j in range(active_job_slots):
                if job_output_item_id[j] < 0:
                    free_slot = j
                    break
            if free_slot < 0:
                break  # no free job slot -- nothing else can start today
            in_item = int(config.recipe_input_item_idx[rec])
            need = int(config.recipe_input_quantity[rec])
            min_rank = int(config.recipe_min_quality_rank[rec])
            cost = float(config.recipe_cost[rec])
            if money < cost:
                continue
            # available/consume scans walk occupied, not every slot -- see
            # kernel.py's mirror.
            available = sum(
                lot_quantity[s]
                for s in occupied
                if lot_item_id[s] == in_item and lot_quality[s] >= min_rank
            )
            if available < need:
                continue
            remaining = need
            i = 0
            while i < len(occupied) and remaining > 0:
                s = occupied[i]
                if lot_item_id[s] == in_item and lot_quality[s] >= min_rank:
                    take = min(remaining, lot_quantity[s])
                    lot_quantity[s] -= take
                    remaining -= take
                    if lot_quantity[s] == 0:
                        # Swap-remove moves the last occupied slot into i, so i
                        # is deliberately NOT advanced -- see kernel.py's mirror.
                        _remove_lot(lot_item_id, occupied, slot_pos, free, s)
                        continue
                i += 1
            money = _f32(money - cost)
            job_output_item_id[free_slot] = int(config.recipe_output_item_idx[rec])
            job_output_quantity[free_slot] = int(config.recipe_output_quantity[rec])
            job_completion_day[free_slot] = day + int(config.recipe_processing_days[rec])

        # -- markets: sell all matured lots (component C's choose_sales,
        # simplified -- see kernel.py's module docstring) -- every lot still
        # standing after today's aging/spoilage/trim is sold in full, at
        # today's price for its crop, scaled by its quality grade -- sells
        # every occupied slot, so this just walks `occupied` and clears it in
        # one pass rather than calling _remove_lot per slot -- see kernel.py's
        # mirror.
        for s in occupied:
            item = lot_item_id[s]
            qty = lot_quantity[s]
            grade = lot_quality[s]
            if grade == 3:
                quality_mult_sale = kernel.QUALITY_MULT_PREMIUM
            elif grade == 2:
                quality_mult_sale = kernel.QUALITY_MULT_STANDARD
            else:
                quality_mult_sale = kernel.QUALITY_MULT_PROCESSING
            revenue = today_price[item] * quality_mult_sale * qty
            money = _f32(money + revenue)
            total_revenue = _f32(total_revenue + revenue)
            total_harvest = _f32(total_harvest + qty)
            market_supply[item] += qty
            lot_item_id[s] = -1
            lot_quantity[s] = 0
            slot_pos[s] = -1
            free.append(s)
        occupied.clear()

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
        "total_processed": total_processed,
        "reputation": reputation,
        "total_contracts_completed": total_contracts_completed,
        "total_contracts_failed": total_contracts_failed,
        "total_contract_penalties": total_contract_penalties,
        "active_plots": float(active_plots),
        "active_job_slots": float(active_job_slots),
        "upgrades_owned_count": float(sum(upgrade_owned)),
    }
