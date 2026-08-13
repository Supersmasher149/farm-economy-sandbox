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

Phase 3 ("markets", single-channel scope) ported `simulation/markets.py`'s
daily price formula and replaces Phase 2's shadow accounting: harvest no
longer credits money at all -- it only creates a lot (same as Phase 2's
lot-insertion code, unchanged) -- and once per run per day, after that
day's aging/spoilage/capacity-trim has run, every crop gets a fresh price
roll (`base_price * seasonal_demand[season] * saturation(market_supply) *
uniform(1-variation, 1+variation)`, with `market_supply` decaying daily,
mirroring `update_daily_prices`) and every occupied lot slot is sold in
full at that price (scaled by a quality multiplier -- premium/standard/
processing, matching `markets.QUALITY_MULTIPLIERS`), crediting `money`/
`total_revenue`/`total_harvest` for real. `market_supply` and today's
price array are per-run scratch (reset each run, not part of `BatchState`)
since nothing needs them after the run completes.

This is a genuine simplification of the real engine's 5-channel system
(`spot`/`wholesale`/`farm_stand`/`processor`/`specialty`, each with its own
price multiplier, quality gate, daily capacity, fee structure, and one
reputation-gated) down to one effective channel with none of that: no
capacity limits, no fees, no reputation (which doesn't exist anywhere in
`vectorized/` state). See `config_arrays.py`'s docstring for exactly what's
read vs. not.

The "agent decides how much to sell" step (`Agent.choose_sales`, 11 real
strategies with real per-strategy logic) is also simplified: every lot
still standing after today's aging/spoilage/trim is sold in full, every
day, for all 3 fixed strategies -- the simplest real strategy's behavior
(`fast_seller.choose_sales` dumps all inventory the same way). Two
consequences worth knowing before reading the numbers: (1) Phase 2's
storage/spoilage mechanics become much less likely to bite in the default
config, since inventory rarely survives past the same day it was
created -- they're still real and still load-bearing (a heavy same-day
harvest across many plots can still exceed `storage_capacity` before the
sell step runs, since trim happens *before* selling, matching the real
day-order), just rarer; `check_storage_capacity_trim`'s forced tiny-capacity
scenario is what keeps that path exercised. (2) A plot's watering/
fertilizing/planting decision this same day still sees *yesterday's* cash
position, not today's just-credited sale revenue -- selling happens once,
after the whole per-plot loop, not interleaved per-plot the way harvest and
replanting already are. This is a known, deliberate divergence from the
real engine's day order (harvest → age/spoil → price → sell → buy upgrades
→ water/fertilize → plant), kept for this reason: Phase 1/2 already
established that agent decisions happen inline, per-plot, in one pass; a
strict day-order match would mean two full passes over plots (harvest-only,
then water/fertilize/plant-only) instead of one, a larger restructuring
than this phase's scope asked for.

Phase 4 ("processing") ported `simulation/processing.py`'s recipe/job
mechanics: `config/processing.json`'s recipes each consume a fixed quantity
of one crop (at a minimum quality) and cash, occupy one of `base_capacity`
global (not per-plot) job slots for `processing_days`, then complete into a
lot of a processed *product* -- a new item-space entry alongside crops
(index >= `num_crops`; see `config_arrays.py`'s docstring for the unified
crop+product item space this required), sold the same way and at the same
daily-rolled price as any crop lot. Job slots are a fixed-size `(B, J)`
array, `J = config.base_capacity`, the same bounded-array pattern Phase 2
used for lots -- but bounded by the real engine's own capacity constant
(currently 1: at most one job in flight at a time), not a derived formula,
since processing capacity is a global resource, not a per-plot one.

Same "simplify the agent, not just the mechanic" approach as Phase 3's
sell-everything: `Agent.choose_processing` (real per-strategy recipe/batch
choices) becomes a fixed policy shared by all 3 fixed strategies -- try
each recipe in `config/processing.json`'s order, start it if a job slot is
free and there's enough input inventory and cash, once per day. This runs
after that day's aging/spoilage/trim and before the sell-all-lots step
(matching the real engine's day order: harvest → age/spoil → jobs complete
→ re-trim → price → **jobs start** → **sell** → ...), so processing
competes with same-day selling for the same freshly-harvested inventory --
whichever recipe is tried first each day gets first claim on its input
crop, same-day, before any of it could otherwise be swept into that day's
sale.

A completing job's output is new inventory added *after* that day's
regular aging/trim pass already ran, so it needs its own same-day re-trim
(`simulation/inventory.py:enforce_storage_capacity`) before pricing/
selling -- both trim passes now share one `_trim_to_capacity` helper,
matching the real engine's own `_trim_to_capacity`/`enforce_storage_capacity`
split. `lots_per_plot`'s per-plot lot-slot bound didn't need to change for
this: `num_lot_slots` gained a flat `+ config.base_capacity` reserve
instead (at most `base_capacity` product lots can be pending at once,
bounded by job-slot count, independent of plot count -- see `state.py`'s
docstring).

Phase 5 ("contracts", simplified scope) ported `simulation/contracts.py`'s
offer/accept/deliver/resolve mechanics, but not its `is_offer_feasible`:
a multi-day production-forecast engine (greedy batch scheduling against
processing-slot free-days, sorted future-harvest arrival lists, cash
reservation across competing recipes) that is genuinely list/branch-shaped,
not array-shaped -- the roadmap's own "least naturally mask-shaped
subsystem" flag. Dropping it is also low-cost: only 2 of the real 11
strategies (`profit_optimizer`, `progression_player`) even override
`choose_contracts` to accept anything; the base `Agent` (and every
strategy closer to this module's 3 fixed ones) never does.

Structural simplification: the real engine lets a buyer have any number of
simultaneous offers/active contracts over a run (bounded only by
`unresolved_ids` deduplication on `buyer_id-item_id-day`). This phase gives
each buyer exactly **one** contract "slot" at a time instead -- empty,
offered, or active -- so `state.py`'s per-buyer arrays are `K = num_buyers`
exactly, not a derived bound; a buyer with an outstanding offer or active
contract simply isn't re-offered until that slot frees up. This trades away
some of the real engine's throughput (a popular buyer could otherwise stack
several contracts) for a much simpler, still fully vectorized state
machine.

Per-buyer daily block, run-level, positioned after that day's aging/trim/
jobs-complete/re-trim and before jobs-start (processing) -- matching the
real day order (harvest → age/spoil → jobs complete → re-trim → price →
contracts offer/accept/deliver → jobs start → sell → ...), so contracts get
first claim on today's inventory, ahead of processing and selling:

1. **Offer generation**, only on interval days (`day % offer_interval_days
   == 0`, `day != 0`): for each buyer with an empty slot and `reputation >=
   min_reputation`, roll one eligible item and a quantity (matching
   `generate_offers`'s `rng.choice`/`rng.roll_yield`), price it at
   `base_price * buyer_price_multiplier * relationship_multiplier`
   (`_relationship_price_multiplier`, unchanged), and offer it.
2. **Accept**, every day a slot is offered and not yet expired
   (`offer_expiry_days`, default 3): accept as soon as *any* current stock
   (>0) of the item at the required quality exists. This stands in for
   `is_offer_feasible`'s forecast -- current inventory only, no promise of
   harvests that haven't happened yet -- so contracts get accepted (and
   sometimes fail their deadline) less predictably than the real engine's
   forecast-gated accept, which is the intended trade for staying array-
   shaped. An expired, never-accepted offer just clears the slot -- no
   penalty (only *accepted* contracts can fail).
3. **Deliver**, every day a slot is active: consume up to `remaining` units
   from matching lot slots (same item + quality gate as processing's input
   check), crediting `money`/`total_revenue`/`total_harvest` immediately
   (contract revenue counts as a sale, same as `total_sold` does in the
   real engine). Fully delivered -> resolved successfully: `reputation`
   +5 (capped 100), `buyer_relationship` up by `relationship_gain_per_delivery`
   (capped 100), `total_contracts_completed` +1.
4. **Deadline resolve**, only if a slot is still active *after* today's
   delivery attempt and `day > deadline_day`: penalty
   `min(money, remaining * unit_price * penalty_rate)`, deducted for real
   (not shadow, unlike storage liability) -- `reputation` -4 (floored 0),
   `buyer_relationship` down by `relationship_loss_per_failure` (floored
   0), `total_contracts_failed` +1, `total_contract_penalties` tracks the
   cumulative cost.

`reputation` and `buyer_relationship` are real `BatchState` fields (not
scratch): they persist for the whole run and gate/scale future offers, so
they can't be reset between days the way `market_supply` can.

Still NOT ported: the multi-channel market system, upgrades (including
upgrades' storage `capacity_bonus`/`shelf_life_multiplier` effects and
processing-capacity bonuses -- Phase 2-4 use only the base
`config/storage.json`/`config/processing.json` values). See
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

# Quality-grade sale multipliers (component: markets), matching
# simulation/markets.py:QUALITY_MULTIPLIERS exactly. Not config-driven --
# the real module hardcodes these too. QUALITY_MULT_REJECTED is never used:
# a rejected-grade harvest never becomes a lot (see the harvest block).
QUALITY_MULT_PROCESSING = 0.65  # grade 1
QUALITY_MULT_STANDARD = 1.0  # grade 2
QUALITY_MULT_PREMIUM = 1.35  # grade 3


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


@njit(cache=True)
def _trim_to_capacity(lot_item_id_row, lot_quantity_row, lot_age_days_row, eff_life, capacity):
    """FEFO capacity trim for one run's lot-slot row, in place.

    Matches `simulation/inventory.py:_trim_to_capacity` -- shared by both
    call sites the same way the real function is (`age_and_spoil`'s nightly
    trim, and `enforce_storage_capacity`'s same-day re-trim right after
    processing jobs complete -- see Phase 4 in this module's docstring for
    why a completing job's output needs a second same-day trim pass).
    Returns units spoiled by trimming; does not touch `total_spoiled`
    itself, matching the real function's own division of labor (each call
    site folds the return value into its own running tally).
    """
    num_lot_slots = lot_item_id_row.shape[0]
    total_qty = 0
    for s in range(num_lot_slots):
        if lot_item_id_row[s] >= 0:
            total_qty += lot_quantity_row[s]
    overflow_units = total_qty - capacity
    spoiled = 0
    while overflow_units > 0:
        chosen = -1
        chosen_remaining = 0.0
        for s in range(num_lot_slots):
            if lot_item_id_row[s] < 0:
                continue
            item = lot_item_id_row[s]
            remaining = np.float64(eff_life[item]) - np.float64(lot_age_days_row[s])
            if chosen < 0 or remaining < chosen_remaining:
                chosen = s
                chosen_remaining = remaining
        if chosen < 0:
            break  # no occupied slots left -- shouldn't happen if overflow_units > 0
        remove = min(overflow_units, lot_quantity_row[chosen])
        lot_quantity_row[chosen] -= remove
        overflow_units -= remove
        spoiled += remove
        if lot_quantity_row[chosen] == 0:
            lot_item_id_row[chosen] = -1
    return spoiled


@njit(parallel=True, cache=True)
def _simulate_chunk_core(
    money,
    total_harvest,
    total_revenue,
    strategy_id,
    total_spoiled,
    total_storage_cost,
    total_processed,
    reputation,
    total_contracts_completed,
    total_contracts_failed,
    total_contract_penalties,
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
    # + config.base_capacity (Phase 4 reserves the +base_capacity for processed-
    # product lots -- see state.py's docstring)
    lot_item_id,
    lot_quantity,
    lot_quality,
    lot_age_days,
    overflow_events,
    # processing job slots (Phase 4), shape (B, J), J = config.base_capacity
    job_output_item_id,
    job_output_quantity,
    job_completion_day,
    # per-buyer contract slots (Phase 5), shape (B, K), K = config.num_buyers
    contract_state,
    contract_item_idx,
    contract_remaining,
    contract_unit_price,
    contract_min_quality_rank,
    contract_deadline_day,
    contract_expiry_day,
    contract_penalty_rate,
    buyer_relationship,
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
    seasonal_demand,
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
    # markets
    market_minimum_supply_multiplier,
    market_supply_decay,
    # processing (component: config_arrays.VectorConfig), one entry per recipe
    recipe_input_item_idx,
    recipe_input_quantity,
    recipe_min_quality_rank,
    recipe_output_item_idx,
    recipe_output_quantity,
    recipe_processing_days,
    recipe_cost,
    # buyers (component: config_arrays.VectorConfig), one entry per buyer
    buyer_item_idx,
    buyer_num_items,
    buyer_quantity_min,
    buyer_quantity_max,
    buyer_min_quality_rank,
    buyer_price_multiplier,
    buyer_deadline_days,
    buyer_penalty_rate,
    buyer_min_reputation,
    buyer_relationship_bonus_rate,
    # contracts (component: config_arrays.VectorConfig)
    contract_offer_interval_days,
    contract_offer_expiry_days,
    contract_relationship_gain,
    contract_relationship_loss,
    contract_relationship_bonus_cap,
):
    num_runs = money.shape[0]
    num_plots = moisture.shape[1]
    num_seasons = season_rain_chance.shape[0]
    num_lot_slots = lot_item_id.shape[1]
    num_job_slots = job_output_item_id.shape[1]
    num_items = base_price.shape[0]  # num_crops + num_products (Phase 4 item space)
    num_recipes = recipe_input_item_idx.shape[0]
    num_buyers = contract_state.shape[1]

    for r in prange(num_runs):
        m = np.float64(money[r])
        th = np.float64(total_harvest[r])
        tr = np.float64(total_revenue[r])
        ts = np.float64(total_spoiled[r])
        tc = np.float64(total_storage_cost[r])
        tp = np.float64(total_processed[r])
        rep = np.float64(reputation[r])
        tcc = np.float64(total_contracts_completed[r])
        tcf = np.float64(total_contracts_failed[r])
        tcp = np.float64(total_contract_penalties[r])
        strat = strategy_id[r]
        run_state = rng_run_state[r]

        # Per-run scratch, not part of BatchState -- both reset fresh each
        # run (matching player.market_supply starting empty each game) and
        # unneeded after the run completes, so there's no reason to persist
        # them across chunk/run boundaries the way lot state has to be.
        market_supply = np.zeros(num_items, dtype=np.float64)
        today_price = np.zeros(num_items, dtype=np.float64)

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

            # -- markets: daily price roll + supply decay
            # (simulation/markets.py:update_daily_prices) -- run-level, once/day,
            # single-channel scope, item-space (crops + processed products,
            # Phase 4 -- see this module's docstring) --
            for c in range(num_items):
                seasonal = seasonal_demand[c, season]
                supply = market_supply[c]
                saturation = max(market_minimum_supply_multiplier, 1.0 - supply * 0.01)
                run_state, u_price = _next(run_state)
                price_factor = (1.0 - price_variation[c]) + u_price * (2.0 * price_variation[c])
                today_price[c] = max(0.01, base_price[c] * seasonal * saturation * price_factor)
                market_supply[c] = supply * market_supply_decay

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
                            # a lot from being created -- processing/standard/premium all do.
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
            ts += _trim_to_capacity(
                lot_item_id[r],
                lot_quantity[r],
                lot_age_days[r],
                effective_shelf_life_days,
                storage_capacity,
            )

            # -- processing: jobs complete (simulation/processing.py:complete_jobs)
            # -- run-level, once/day, after today's aging/trim so a completing job's
            # output is unambiguously "produced today" (skipped by tomorrow's aging
            # pass the same way a fresh harvest lot is) --
            for j in range(num_job_slots):
                if job_output_item_id[r, j] < 0:
                    continue
                if day < job_completion_day[r, j]:
                    continue
                out_item = job_output_item_id[r, j]
                out_qty = job_output_quantity[r, j]
                tp += out_qty
                inserted = False
                for s in range(num_lot_slots):
                    if lot_item_id[r, s] < 0:
                        lot_item_id[r, s] = out_item
                        lot_quantity[r, s] = out_qty
                        lot_quality[r, s] = 2  # standard -- matches complete_jobs' hardcoded grade
                        lot_age_days[r, s] = -1  # becomes 0 on tomorrow's aging pass
                        inserted = True
                        break
                if not inserted:
                    overflow_events[r] += 1
                job_output_item_id[r, j] = -1
                job_output_quantity[r, j] = 0

            # -- storage: same-day re-trim (simulation/inventory.py:enforce_storage_capacity)
            # -- a completing job's output is new inventory added after this day's
            # aging/trim pass already ran, so without a second pass here, overflow it
            # causes wouldn't spoil until tomorrow, letting today's sale use inventory
            # that should already be gone (matches the real engine's own #19 fix) --
            ts += _trim_to_capacity(
                lot_item_id[r],
                lot_quantity[r],
                lot_age_days[r],
                effective_shelf_life_days,
                storage_capacity,
            )

            # -- contracts: offer generation (simulation/contracts.py:generate_offers)
            # -- run-level, only on interval days, simplified scope (see this
            # module's docstring) -- one contract "slot" per buyer --
            if day != 0 and day % contract_offer_interval_days == 0:
                for b in range(num_buyers):
                    if contract_state[r, b] != 0:
                        continue  # buyer already has an offer or active contract
                    if rep < buyer_min_reputation[b]:
                        continue
                    n_elig = buyer_num_items[b]
                    run_state, u_item = _next(run_state)
                    pick = int(u_item * n_elig)
                    if pick >= n_elig:
                        pick = n_elig - 1
                    item_idx = buyer_item_idx[b, pick]
                    run_state, u_qty = _next(run_state)
                    qmin = buyer_quantity_min[b]
                    qmax = buyer_quantity_max[b]
                    quantity = qmin + int(u_qty * (qmax - qmin + 1))
                    if quantity > qmax:
                        quantity = qmax
                    relationship_mult = 1.0 + min(
                        contract_relationship_bonus_cap,
                        buyer_relationship[r, b] * buyer_relationship_bonus_rate[b],
                    )
                    price = base_price[item_idx] * buyer_price_multiplier[b] * relationship_mult
                    contract_state[r, b] = 1
                    contract_item_idx[r, b] = item_idx
                    contract_remaining[r, b] = quantity
                    contract_unit_price[r, b] = price
                    contract_min_quality_rank[r, b] = buyer_min_quality_rank[b]
                    contract_deadline_day[r, b] = day + buyer_deadline_days[b]
                    contract_expiry_day[r, b] = day + contract_offer_expiry_days
                    contract_penalty_rate[r, b] = buyer_penalty_rate[b]

            # -- contracts: accept + deliver + resolve (component C's
            # choose_contracts/choose_contract_deliveries, simplified is_offer_feasible
            # -- see this module's docstring) -- run-level, every day a slot is
            # offered or active --
            for b in range(num_buyers):
                cstate = contract_state[r, b]
                if cstate == 0:
                    continue
                item = contract_item_idx[r, b]
                min_rank = contract_min_quality_rank[r, b]
                if cstate == 1:
                    if day > contract_expiry_day[r, b]:
                        contract_state[r, b] = 0
                        continue
                    available = 0
                    for s in range(num_lot_slots):
                        if lot_item_id[r, s] == item and lot_quality[r, s] >= min_rank:
                            available += lot_quantity[r, s]
                    if available <= 0:
                        continue  # still just offered, try again tomorrow (until expiry)
                    contract_state[r, b] = 2
                    cstate = 2
                # cstate == 2: active -- attempt delivery from today's inventory
                remaining = contract_remaining[r, b]
                delivered = 0
                for s in range(num_lot_slots):
                    if delivered >= remaining:
                        break
                    if lot_item_id[r, s] == item and lot_quality[r, s] >= min_rank:
                        take = min(remaining - delivered, lot_quantity[r, s])
                        lot_quantity[r, s] -= take
                        if lot_quantity[r, s] == 0:
                            lot_item_id[r, s] = -1
                        delivered += take
                if delivered > 0:
                    revenue = delivered * contract_unit_price[r, b]
                    m += revenue
                    tr += revenue
                    th += delivered
                    remaining -= delivered
                    contract_remaining[r, b] = remaining
                if remaining <= 0:
                    contract_state[r, b] = 0
                    rep = min(100.0, rep + 5.0)
                    buyer_relationship[r, b] = min(
                        100.0, buyer_relationship[r, b] + contract_relationship_gain
                    )
                    tcc += 1
                elif day > contract_deadline_day[r, b]:
                    shortfall_value = remaining * contract_unit_price[r, b]
                    penalty = min(
                        max(0.0, m), max(0.0, shortfall_value * contract_penalty_rate[r, b])
                    )
                    m -= penalty
                    tcp += penalty
                    rep = max(0.0, rep - 4.0)
                    buyer_relationship[r, b] = max(
                        0.0, buyer_relationship[r, b] - contract_relationship_loss
                    )
                    contract_state[r, b] = 0
                    tcf += 1

            # -- processing: agent starts jobs (component C's choose_processing,
            # simplified -- see this module's docstring) -- fixed recipe-order
            # preference, same policy for all 3 strategies, matching "sell
            # everything"'s no-per-strategy-branching precedent: try each recipe in
            # config order, start it if a job slot is free and there's enough input
            # inventory (at the recipe's min quality) and cash --
            for rec in range(num_recipes):
                free_slot = -1
                for j in range(num_job_slots):
                    if job_output_item_id[r, j] < 0:
                        free_slot = j
                        break
                if free_slot < 0:
                    break  # no free job slot -- nothing else can start today
                in_item = recipe_input_item_idx[rec]
                need = recipe_input_quantity[rec]
                min_rank = recipe_min_quality_rank[rec]
                cost = recipe_cost[rec]
                if m < cost:
                    continue
                available = 0
                for s in range(num_lot_slots):
                    if lot_item_id[r, s] == in_item and lot_quality[r, s] >= min_rank:
                        available += lot_quantity[r, s]
                if available < need:
                    continue
                remaining = need
                for s in range(num_lot_slots):
                    if remaining <= 0:
                        break
                    if lot_item_id[r, s] == in_item and lot_quality[r, s] >= min_rank:
                        take = min(remaining, lot_quantity[r, s])
                        lot_quantity[r, s] -= take
                        remaining -= take
                        if lot_quantity[r, s] == 0:
                            lot_item_id[r, s] = -1
                m -= cost
                job_output_item_id[r, free_slot] = recipe_output_item_idx[rec]
                job_output_quantity[r, free_slot] = recipe_output_quantity[rec]
                job_completion_day[r, free_slot] = day + recipe_processing_days[rec]

            # -- markets: sell all matured lots (component C's choose_sales,
            # simplified -- see this module's docstring) -- every lot still
            # standing after today's aging/spoilage/trim is sold in full, at
            # today's price for its crop, scaled by its quality grade --
            for s in range(num_lot_slots):
                if lot_item_id[r, s] < 0:
                    continue
                item = lot_item_id[r, s]
                qty = lot_quantity[r, s]
                grade = lot_quality[r, s]
                if grade == 3:
                    quality_mult_sale = QUALITY_MULT_PREMIUM
                elif grade == 2:
                    quality_mult_sale = QUALITY_MULT_STANDARD
                else:
                    quality_mult_sale = QUALITY_MULT_PROCESSING
                revenue = today_price[item] * quality_mult_sale * qty
                m += revenue
                tr += revenue
                th += qty
                market_supply[item] += qty
                lot_item_id[r, s] = -1
                lot_quantity[r, s] = 0

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
        total_processed[r] = np.float32(tp)
        reputation[r] = np.float32(rep)
        total_contracts_completed[r] = np.float32(tcc)
        total_contracts_failed[r] = np.float32(tcf)
        total_contract_penalties[r] = np.float32(tcp)
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
        state.total_processed,
        state.reputation,
        state.total_contracts_completed,
        state.total_contracts_failed,
        state.total_contract_penalties,
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
        state.job_output_item_id,
        state.job_output_quantity,
        state.job_completion_day,
        state.contract_state,
        state.contract_item_idx,
        state.contract_remaining,
        state.contract_unit_price,
        state.contract_min_quality_rank,
        state.contract_deadline_day,
        state.contract_expiry_day,
        state.contract_penalty_rate,
        state.buyer_relationship,
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
        config.seasonal_demand,
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
        float(config.market_minimum_supply_multiplier),
        float(config.market_supply_decay),
        config.recipe_input_item_idx,
        config.recipe_input_quantity,
        config.recipe_min_quality_rank,
        config.recipe_output_item_idx,
        config.recipe_output_quantity,
        config.recipe_processing_days,
        config.recipe_cost,
        config.buyer_item_idx,
        config.buyer_num_items,
        config.buyer_quantity_min,
        config.buyer_quantity_max,
        config.buyer_min_quality_rank,
        config.buyer_price_multiplier,
        config.buyer_deadline_days,
        config.buyer_penalty_rate,
        config.buyer_min_reputation,
        config.buyer_relationship_bonus_rate,
        config.contract_offer_interval_days,
        config.contract_offer_expiry_days,
        float(config.contract_relationship_gain),
        float(config.contract_relationship_loss),
        float(config.contract_relationship_bonus_cap),
    )

    if np.any(overflow_events):
        raise RuntimeError(
            "vectorized/kernel.py: lot-slot overflow -- a plot needed more concurrent "
            "storage/job-completion lots than config_arrays.py's lots_per_plot + "
            "base_capacity bound provides for. "
            f"overflow_events={overflow_events[overflow_events > 0]!r} at run indices "
            f"{np.nonzero(overflow_events)[0]!r}. This means config/crops.json's "
            "shelf_life_days/growth_days ratio, or config/processing.json's "
            "base_capacity, drifted past the assumption lots_per_plot was sized for "
            "-- see config_arrays.py's VectorConfig."
        )
