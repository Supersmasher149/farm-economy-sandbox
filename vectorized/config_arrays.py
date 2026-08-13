"""config/*.json -> flat numpy arrays, loaded once (this module's `derived.py`).

Phase 1 ("crop/soil physics parity") reversed the earlier decision in
`crops.py` not to couple this package to the real balance config. That
decision was right for the first build (a 3-crop toy model has no business
tracking `config/crops.json`'s edits) and wrong for this one: physics parity
*means* tracking the real numbers, so this module reads `config/crops.json`,
`config/soil.json`, `config/watering_settings.json`, `config/fertilizer.json`,
and `config/weather.json` directly, the same files `simulation/derived.py`
resolves for the real engine -- just into numpy arrays instead of per-crop
`CropProfile` objects, because the kernel needs `array[crop_idx]`, not
`dict[crop_id]`.

Phase 2 ("storage & spoilage") added `config/storage.json` and each crop's
`shelf_life_days` from `config/crops.json` on top of that.

Phase 3 ("markets", single-channel scope -- see kernel.py's module
docstring) added `config/markets.json` and each crop's `seasonal_demand`
from `config/crops.json`. Not read: the 5-channel `channels` list in
`config/markets.json` (`price_multiplier`/`min_quality`/`daily_capacity`/
fees per channel) -- this phase models one effective channel, not
`simulation/markets.py`'s full channel system. `price_variation` (used as
market variation, matching `simulation/derived.py:_build_market_profiles`'s
`item.get("price_variation", default_variation)`) was already read in
Phase 1 for the harvest-time price roll it's replacing.

Phase 4 ("processing") added `config/processing.json` and, with it, a
unified **item space**: `base_price`/`price_variation`/`seasonal_demand`/
`effective_shelf_life_days` grew from crop-only arrays (length `num_crops`)
to item arrays (length `num_items = num_crops + num_products`), index
0..num_crops-1 still meaning exactly what it always has (`crop_type[r,p]`
values are unchanged), index num_crops..num_items-1 meaning a processed
product -- the same unification `simulation/derived.py`'s `items_by_id`
already does for the real engine, so `lot_item_id` can hold either a crop
or a product without a second lot-array system. See `kernel.py`'s module
docstring for the processing mechanics this enables.

Phase 5 ("contracts", simplified scope -- see kernel.py's module
docstring) added `config/buyers.json` and `config/contracts.json`. Buyer
`items` lists are resolved to item-space indices at load time (loud
failure on an unknown id, same policy as recipes) into a padded
`(num_buyers, buyer_max_items)` array. NOT ported:
`contracts.py`'s `is_offer_feasible` multi-day production-forecast
scheduler (`production_safety_factor`, the whole greedy batch-scheduling
machinery) -- this phase's accept policy is a simplified stand-in, see
kernel.py's module docstring. `fallback_price_multiplier` (used only by
that scheduler's market-alternative comparison) is correspondingly not
read either.

Phase 7 ("upgrades") added `config/upgrades.json`, read generically by
`effect["type"]` (`capacity`, `growth_time_reduction`, `storage`,
`processing_capacity`) rather than by hardcoded id, matching
`simulation/derived.py`'s own type-dispatch fold. Two of those four types
grow a fixed-shape array dimension that every other phase sized once per
batch (`capacity` grows plots, `processing_capacity` grows processing job
slots) -- see `state.py`'s docstring for how `active_plots`/
`active_job_slots` gate that. This also changed `lots_per_plot`: its bound
now uses the *worst case* growth_days (shortest, if any owned
`growth_time_reduction` upgrade applies) and shelf life (longest, if any
owned `storage` upgrade's `shelf_life_multiplier` applies) instead of the
base config values, since a run that buys those upgrades can hold more
concurrent lots per plot than a run that never does, and the bound has to
cover every run regardless of what it ends up owning.

Only `unlock_requirement.type == "total_revenue"` is understood (the only
type any shipped crop uses). A crop with a different unlock type fails to
load loudly (`NotImplementedError` naming the crop and type) rather than
silently planting an unreachable crop as if it were always unlocked --
matching this repo's general preference for a loud failure over a silent
wrong answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Sentinel for "no revenue-based unlock gate" -- any run's total_revenue
# (which starts at 0 and only grows) satisfies `total_revenue >= 0`
# immediately, so a crop with this sentinel is unlocked from day one.
NO_UNLOCK_REQUIREMENT = np.float32(0.0)

# Matches simulation/state.py:QUALITY_ORDER exactly -- a recipe's
# min_quality gates which lots it can consume as input.
_QUALITY_RANK = {"rejected": 0, "processing": 1, "standard": 2, "premium": 3}


@dataclass(frozen=True)
class VectorConfig:
    # -- crops (config/crops.json), one entry per index 0..num_crops-1 --
    crop_ids: tuple
    num_crops: int
    seed_cost: np.ndarray  # float32
    growth_days: np.ndarray  # int16
    min_yield: np.ndarray  # int32 (roll_yield is an inclusive randint, not continuous)
    max_yield: np.ndarray  # int32
    # float32, ITEM-space (length num_items, Phase 4): index 0..num_crops-1 is
    # this crop's own base_price, index num_crops.. is a processed product's
    # processed_base_price -- see this module's docstring
    base_price: np.ndarray
    price_variation: np.ndarray  # float32, item-space (Phase 4), same layout as base_price
    loss_chance: np.ndarray  # float32
    water_interval_days: np.ndarray  # int16
    min_moisture: np.ndarray  # float32
    ph_low: np.ndarray  # float32
    ph_high: np.ndarray  # float32
    temperature_low: np.ndarray  # float32
    temperature_high: np.ndarray  # float32
    pest_susceptibility: np.ndarray  # float32
    disease_susceptibility: np.ndarray  # float32
    nitrogen_demand: np.ndarray  # float32
    phosphorus_demand: np.ndarray  # float32
    potassium_demand: np.ndarray  # float32
    family_id: np.ndarray  # int8, index into `families`
    unlock_total_revenue: np.ndarray  # float32, NO_UNLOCK_REQUIREMENT if none
    families: tuple
    shelf_life_days: np.ndarray  # int16, c.get("shelf_life_days", 7) -- crop-space only
    # int16, ITEM-space (Phase 4): max(1, round(shelf_life_days * multiplier)) for
    # crops (index < num_crops), or the producing recipe's own shelf_life_days for
    # products (index >= num_crops) -- see this module's docstring
    effective_shelf_life_days: np.ndarray
    # float32 (num_items, 4), ITEM-space (Phase 4), season order
    # spring/summer/autumn/winter, from c.get("seasonal_demand", {}).get(season, 1.0)
    seasonal_demand: np.ndarray

    # -- products (config/processing.json's "products" list), Phase 4, one
    # entry per index num_crops..num_items-1 in the item-space arrays above --
    num_products: int
    num_items: int  # num_crops + num_products
    product_ids: tuple

    # -- recipes (config/processing.json's "recipes" list), Phase 4 --
    num_recipes: int
    recipe_input_item_idx: np.ndarray  # int8, item-space index (always a crop today)
    recipe_input_quantity: np.ndarray  # int32
    recipe_min_quality_rank: (
        np.ndarray
    )  # int8, QUALITY_ORDER rank, r.get("min_quality","processing")
    recipe_output_item_idx: np.ndarray  # int8, item-space index (always a product today)
    recipe_output_quantity: np.ndarray  # int32
    recipe_processing_days: np.ndarray  # int16
    recipe_cost: np.ndarray  # float32
    # Concurrent processing job slots, global (not per-plot) -- see kernel.py's
    # module docstring for why this is a tiny, provably-safe bound.
    base_capacity: int

    # -- buyers (config/buyers.json), Phase 5, one entry per index
    # 0..num_buyers-1 -- one contract "slot" per buyer, see kernel.py's
    # module docstring for the simplified accept policy this enables --
    num_buyers: int
    buyer_ids: tuple
    buyer_max_items: int  # padding width of buyer_item_idx's second axis
    buyer_item_idx: np.ndarray  # int8 (num_buyers, buyer_max_items), -1 padding
    buyer_num_items: np.ndarray  # int8 (num_buyers,), real (unpadded) item count
    buyer_quantity_min: np.ndarray  # int32
    buyer_quantity_max: np.ndarray  # int32
    buyer_min_quality_rank: np.ndarray  # int8, QUALITY_ORDER rank
    buyer_price_multiplier: np.ndarray  # float32
    buyer_deadline_days: np.ndarray  # int32
    buyer_penalty_rate: np.ndarray  # float32
    buyer_min_reputation: np.ndarray  # float32
    buyer_relationship_bonus_rate: np.ndarray  # float32

    # -- contracts (config/contracts.json), Phase 5 --
    contract_offer_interval_days: int
    contract_offer_expiry_days: int
    contract_relationship_gain: np.float32
    contract_relationship_loss: np.float32
    contract_relationship_bonus_cap: np.float32

    # Preference rankings for the 3 fixed strategies (component C): crop
    # indices ordered by that strategy's preference, best first. The kernel
    # walks a ranking looking for the first entry that's both unlocked (by
    # total_revenue) and affordable (by money) *at runtime*, since unlock
    # state changes over a run -- a statically "best" crop chosen at load
    # time could be one the run hasn't unlocked yet on day 1.
    greedy_rank: np.ndarray  # int8
    conservative_rank: np.ndarray  # int8

    # -- soil (config/soil.json) --
    initial_moisture: np.float32
    initial_nitrogen: np.float32
    initial_phosphorus: np.float32
    initial_potassium: np.float32
    initial_ph: np.float32
    initial_soil_health: np.float32
    initial_pest_pressure: np.float32
    initial_disease_pressure: np.float32
    regen_moisture: np.float32
    regen_nitrogen: np.float32
    regen_phosphorus: np.float32
    regen_potassium: np.float32
    regen_soil_health: np.float32
    regen_pest_pressure: np.float32
    regen_disease_pressure: np.float32
    same_family_yield_penalty: np.float32
    same_family_quality_penalty: np.float32
    soil_health_yield_floor: np.float32
    soil_health_yield_span: np.float32
    harvest_soil_health_cost: np.float32
    min_soil_health: np.float32
    fallow_pest_decay: np.float32
    fallow_disease_decay: np.float32
    fallow_soil_health_regen: np.float32
    pest_growth_per_day: np.float32
    disease_growth_per_rainfall: np.float32
    max_pest_pressure: np.float32
    max_disease_pressure: np.float32

    # -- watering (config/watering_settings.json) --
    water_cost_per_plot: np.float32
    water_moisture_added: np.float32
    neglect_loss_chance_penalty_per_day: np.float32
    max_neglect_loss_chance_bonus: np.float32
    neglect_yield_penalty_per_day: np.float32
    max_neglect_yield_penalty: np.float32

    # -- fertilizer (config/fertilizer.json) --
    fertilizer_cost: np.float32
    fertilizer_yield_bonus_pct: np.float32
    fertilizer_loss_chance_reduction: np.float32
    fertilizer_quality_bonus: np.float32
    fertilizer_nitrogen_added: np.float32
    fertilizer_phosphorus_added: np.float32
    fertilizer_potassium_added: np.float32

    # -- weather (config/weather.json), 4 seasons: spring/summer/autumn/winter --
    season_length_days: int
    season_temp_low: np.ndarray  # float32[4]
    season_temp_high: np.ndarray  # float32[4]
    season_rain_chance: np.ndarray  # float32[4]
    season_rain_low: np.ndarray  # float32[4]
    season_rain_high: np.ndarray  # float32[4]
    season_evaporation: np.ndarray  # float32[4]

    # -- storage (config/storage.json) --
    storage_capacity: np.int32
    storage_daily_cost: np.float32
    storage_shelf_life_multiplier: np.float32
    # Upper bound on concurrent live lots per plot: a plot harvests at most
    # once every `growth_days`, and any lot older than
    # `effective_shelf_life_days` is guaranteed fully spoiled regardless of
    # capacity trim, so `ceil(effective_shelf_life_days / growth_days) + 1`
    # (max over crops, +1 buffer) is a provable per-plot bound, not a guess.
    # Plain int, not an array -- it's a shape parameter for the lot-slot
    # dimension, not a per-crop value.
    lots_per_plot: int

    # -- markets (config/markets.json), single-channel scope -- see
    # kernel.py's module docstring for what's not ported (the 5-channel
    # system, fees, reputation) --
    market_minimum_supply_multiplier: np.float32
    market_supply_decay: np.float32

    # -- top-level (config/simulation_settings.json) --
    start_money: np.float32

    # -- upgrades (config/upgrades.json), Phase 7, one entry per index
    # 0..num_upgrades-1, read generically by effect["type"] -- see this
    # module's docstring. Amount/bonus fields default to the identity for
    # an upgrade of a different type (0 for additive amounts, 1.0 for the
    # shelf_life_multiplier), so summing/multiplying across the whole
    # catalog is always exactly the fold real engine's own dispatch does
    # per owned upgrade, without a type check at the fold site --
    num_upgrades: int
    upgrade_ids: tuple
    upgrade_cost: np.ndarray  # float32
    upgrade_capacity_amount: np.ndarray  # int32, u.effect.amount if type=="capacity" else 0
    # float32, u.effect.amount if type=="growth_time_reduction" else 0.0
    upgrade_growth_time_reduction: np.ndarray
    # int32, u.effect.capacity_bonus if type=="storage" else 0
    upgrade_storage_capacity_bonus: np.ndarray
    # float32, u.effect.shelf_life_multiplier if type=="storage" else 1.0 (identity)
    upgrade_storage_shelf_life_multiplier: np.ndarray
    # int32, u.effect.amount if type=="processing_capacity" else 0
    upgrade_processing_capacity_amount: np.ndarray
    # Sum across the whole catalog -- the extra plots/job slots available if a
    # run buys every capacity/processing_capacity upgrade there is. This is
    # what callers (orchestrator.py) grow num_plots/num_job_slots by, since
    # BatchState's arrays are fixed-shape for the whole batch (see state.py's
    # docstring for active_plots/active_job_slots, which track how much of
    # that headroom a given run has actually unlocked).
    total_capacity_bonus: int
    total_processing_capacity_bonus: int
    # int16, ITEM-space, RAW (no shelf_life_multiplier applied) -- unlike
    # effective_shelf_life_days above (baked in at load time with only the
    # base config/storage.json multiplier), a run's effective multiplier
    # depends on which storage upgrade(s) it owns, so the kernel needs the
    # raw per-item value to recompute the multiplied value per run per day.
    shelf_life_days_item: np.ndarray


def _load_json(config_dir: Path, name: str) -> dict:
    with open(config_dir / name) as f:
        return json.load(f)


def load_vector_config(config_dir: Path = CONFIG_DIR) -> VectorConfig:
    crops = _load_json(config_dir, "crops.json")
    soil = _load_json(config_dir, "soil.json")
    watering = _load_json(config_dir, "watering_settings.json")
    fertilizer = _load_json(config_dir, "fertilizer.json")
    weather = _load_json(config_dir, "weather.json")
    settings = _load_json(config_dir, "simulation_settings.json")
    storage = _load_json(config_dir, "storage.json")
    markets = _load_json(config_dir, "markets.json")
    processing = _load_json(config_dir, "processing.json")
    buyers = _load_json(config_dir, "buyers.json")
    contracts_cfg = _load_json(config_dir, "contracts.json")
    upgrades = _load_json(config_dir, "upgrades.json")

    crop_ids = tuple(c["id"] for c in crops)
    num_crops = len(crop_ids)
    season_names = ("spring", "summer", "autumn", "winter")

    families_seen: list = []
    family_id = np.empty(num_crops, dtype=np.int8)
    for i, c in enumerate(crops):
        family = c.get("family", c["id"])
        if family not in families_seen:
            families_seen.append(family)
        family_id[i] = families_seen.index(family)
    families = tuple(families_seen)

    unlock_total_revenue = np.full(num_crops, NO_UNLOCK_REQUIREMENT, dtype=np.float32)
    for i, c in enumerate(crops):
        req = c.get("unlock_requirement")
        if req is None:
            continue
        if req.get("type") != "total_revenue":
            raise NotImplementedError(
                f"crop {c['id']!r} has unlock_requirement type {req.get('type')!r}, "
                "which vectorized/config_arrays.py does not understand (only "
                "'total_revenue' is supported) -- add support before loading this "
                "config, don't silently treat the crop as always-unlocked."
            )
        unlock_total_revenue[i] = np.float32(req["value"])

    seed_cost = np.array([c["seed_cost"] for c in crops], dtype=np.float32)
    growth_days = np.array([c["growth_days"] for c in crops], dtype=np.int16)
    min_yield = np.array([c["min_yield"] for c in crops], dtype=np.int32)
    max_yield = np.array([c["max_yield"] for c in crops], dtype=np.int32)
    base_price = np.array([c["base_price"] for c in crops], dtype=np.float32)
    price_variation = np.array([c.get("price_variation", 0.12) for c in crops], dtype=np.float32)
    loss_chance = np.array([c["loss_chance"] for c in crops], dtype=np.float32)
    water_interval_days = np.array([c.get("water_interval_days", 3) for c in crops], dtype=np.int16)
    min_moisture = np.array([c.get("min_moisture", 0.35) for c in crops], dtype=np.float32)
    ph_range = [c.get("ph_range", [5.8, 7.0]) for c in crops]
    ph_low = np.array([r[0] for r in ph_range], dtype=np.float32)
    ph_high = np.array([r[1] for r in ph_range], dtype=np.float32)
    temp_range = [c.get("temperature_range", [10, 30]) for c in crops]
    temperature_low = np.array([r[0] for r in temp_range], dtype=np.float32)
    temperature_high = np.array([r[1] for r in temp_range], dtype=np.float32)
    pest_susceptibility = np.array(
        [c.get("pest_susceptibility", 1.0) for c in crops], dtype=np.float32
    )
    disease_susceptibility = np.array(
        [c.get("disease_susceptibility", 1.0) for c in crops], dtype=np.float32
    )
    default_demand = {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01}
    demands = [c.get("nutrient_demand", default_demand) for c in crops]
    nitrogen_demand = np.array([d.get("nitrogen", 0.0) for d in demands], dtype=np.float32)
    phosphorus_demand = np.array([d.get("phosphorus", 0.0) for d in demands], dtype=np.float32)
    potassium_demand = np.array([d.get("potassium", 0.0) for d in demands], dtype=np.float32)
    shelf_life_days = np.array([c.get("shelf_life_days", 7) for c in crops], dtype=np.int16)
    seasonal_demand = np.array(
        [[c.get("seasonal_demand", {}).get(s, 1.0) for s in season_names] for c in crops],
        dtype=np.float32,
    )

    expected_value_per_day = base_price * (min_yield + max_yield) / 2.0 / growth_days
    greedy_rank = np.argsort(-expected_value_per_day).astype(np.int8)
    conservative_rank = np.argsort(loss_chance).astype(np.int8)

    soil_initial = soil.get("initial", {})
    soil_regen = soil.get("regen_per_day", {})
    soil_dynamics = soil.get("dynamics", {})

    seasons_cfg = weather.get("seasons", {})

    def season_field(key, sub_default):
        return np.array(
            [seasons_cfg.get(s, {}).get(key, sub_default) for s in season_names], dtype=np.float32
        )

    temp_ranges = [seasons_cfg.get(s, {}).get("temperature_range", [12, 24]) for s in season_names]
    rain_ranges = [seasons_cfg.get(s, {}).get("rainfall_range", [0.08, 0.25]) for s in season_names]

    storage_capacity = np.int32(storage.get("capacity", 100))
    storage_daily_cost = np.float32(storage.get("daily_cost", 0.0))
    storage_shelf_life_multiplier = np.float32(storage.get("shelf_life_multiplier", 1.0))
    effective_shelf_life_days = np.maximum(
        1, np.round(shelf_life_days.astype(np.float64) * float(storage_shelf_life_multiplier))
    ).astype(np.int16)
    market_minimum_supply_multiplier = np.float32(markets.get("minimum_supply_multiplier", 0.65))
    market_supply_decay = np.float32(markets.get("supply_decay", 0.75))

    # -- upgrades (config/upgrades.json), Phase 7 -- read generically by
    # effect["type"], matching simulation/derived.py's own dispatch. Amount
    # arrays default to the identity for a non-matching type (0 additive,
    # 1.0 multiplicative) so a fold across the whole catalog never needs a
    # type check at the fold site -- see this module's docstring.
    upgrade_ids = tuple(u["id"] for u in upgrades)
    num_upgrades = len(upgrade_ids)
    upgrade_cost = np.array([u["cost"] for u in upgrades], dtype=np.float32)
    upgrade_capacity_amount = np.zeros(num_upgrades, dtype=np.int32)
    upgrade_growth_time_reduction = np.zeros(num_upgrades, dtype=np.float32)
    upgrade_storage_capacity_bonus = np.zeros(num_upgrades, dtype=np.int32)
    upgrade_storage_shelf_life_multiplier = np.ones(num_upgrades, dtype=np.float32)
    upgrade_processing_capacity_amount = np.zeros(num_upgrades, dtype=np.int32)
    for i, u in enumerate(upgrades):
        effect = u["effect"]
        etype = effect["type"]
        if etype == "capacity":
            upgrade_capacity_amount[i] = effect["amount"]
        elif etype == "growth_time_reduction":
            upgrade_growth_time_reduction[i] = effect["amount"]
        elif etype == "storage":
            upgrade_storage_capacity_bonus[i] = effect.get("capacity_bonus", 0)
            upgrade_storage_shelf_life_multiplier[i] = effect.get("shelf_life_multiplier", 1.0)
        elif etype == "processing_capacity":
            upgrade_processing_capacity_amount[i] = effect["amount"]
        else:
            raise NotImplementedError(
                f"upgrade {u['id']!r} has effect type {etype!r}, which "
                "vectorized/config_arrays.py does not understand (only 'capacity', "
                "'growth_time_reduction', 'storage', and 'processing_capacity' are "
                "supported) -- add support before loading this config, don't "
                "silently drop the upgrade's effect."
            )
    total_capacity_bonus = int(upgrade_capacity_amount.sum())
    total_processing_capacity_bonus = int(upgrade_processing_capacity_amount.sum())

    # lots_per_plot's bound (see its docstring) has to cover a run that owns
    # every growth_time_reduction/storage upgrade at once: shorter growth_days
    # means more harvests fit inside one shelf-life window, and a longer
    # shelf life directly extends that window -- both worst cases, not the
    # base config values, are what the ceiling below has to use.
    worst_growth_factor = 1.0
    for amount in upgrade_growth_time_reduction:
        if amount > 0.0:
            worst_growth_factor *= 1.0 - float(amount)
    growth_days_worst = np.maximum(
        1, np.round(growth_days.astype(np.float64) * worst_growth_factor)
    ).astype(np.int16)
    worst_shelf_life_multiplier = float(storage_shelf_life_multiplier) * float(
        np.prod(upgrade_storage_shelf_life_multiplier)
    )
    effective_shelf_life_days_worst = np.maximum(
        1, np.round(shelf_life_days.astype(np.float64) * worst_shelf_life_multiplier)
    ).astype(np.int16)

    lots_per_plot = (
        int(
            np.max(
                np.ceil(
                    effective_shelf_life_days_worst.astype(np.float64)
                    / growth_days_worst.astype(np.float64)
                )
            )
        )
        + 1
    )

    # -- item space (Phase 4): crops (0..num_crops-1) + products
    # (num_crops..num_items-1) -- see this module's docstring --
    products = processing.get("products", [])
    product_ids = tuple(p["id"] for p in products)
    num_products = len(product_ids)
    num_items = num_crops + num_products
    item_ids = crop_ids + product_ids
    item_index = {item_id: i for i, item_id in enumerate(item_ids)}

    recipes = processing.get("recipes", [])
    # A product's shelf life is recipe-specific in simulation/state.py's
    # ProcessingJob (each job stores its own shelf_life_days from the recipe
    # that started it), not a per-item constant -- but this module's
    # item-space arrays need exactly one value per item. That's only sound
    # if every product has exactly one producing recipe (true of every
    # shipped recipe today); a second recipe producing the same output with
    # a different shelf_life_days would silently pick whichever recipe's
    # value happened to be seen last, so that case fails loudly instead.
    product_shelf_life_days: dict = {}
    for r in recipes:
        out_id = r["output_item_id"]
        life = r.get("shelf_life_days", 30)
        if out_id in product_shelf_life_days and product_shelf_life_days[out_id] != life:
            raise NotImplementedError(
                f"product {out_id!r} is produced by recipes with different "
                f"shelf_life_days ({product_shelf_life_days[out_id]!r} vs {life!r}) -- "
                "vectorized/config_arrays.py's item-space effective_shelf_life_days "
                "assumes one shelf life per product, matching every shipped recipe "
                "today; add per-recipe shelf life tracking before loading this config."
            )
        product_shelf_life_days[out_id] = life

    product_base_price = np.array(
        [p.get("processed_base_price", p.get("base_price", 1.0)) for p in products],
        dtype=np.float32,
    )
    product_price_variation = np.array(
        [p.get("price_variation", 0.12) for p in products], dtype=np.float32
    )
    product_seasonal_demand = np.array(
        [[p.get("seasonal_demand", {}).get(s, 1.0) for s in season_names] for p in products],
        dtype=np.float32,
    ).reshape(num_products, 4)
    product_shelf_life = np.array(
        [product_shelf_life_days.get(pid, 30) for pid in product_ids], dtype=np.int16
    )
    product_effective_shelf_life_days = np.maximum(
        1,
        np.round(product_shelf_life.astype(np.float64) * float(storage_shelf_life_multiplier)),
    ).astype(np.int16)

    base_price = np.concatenate([base_price, product_base_price])
    price_variation = np.concatenate([price_variation, product_price_variation])
    seasonal_demand = np.concatenate([seasonal_demand, product_seasonal_demand], axis=0)
    effective_shelf_life_days = np.concatenate(
        [effective_shelf_life_days, product_effective_shelf_life_days]
    )
    # Raw (no multiplier applied), item-space -- Phase 7 needs this alongside
    # effective_shelf_life_days above: that array is baked in with only the
    # base config/storage.json multiplier at load time, but a run's actual
    # multiplier depends on which storage upgrade(s) it owns, so the kernel
    # recomputes the multiplied value per run per day from this raw array.
    shelf_life_days_item = np.concatenate([shelf_life_days, product_shelf_life])

    def _resolve_item(item_id: str, recipe_id: str, field: str) -> int:
        if item_id not in item_index:
            raise NotImplementedError(
                f"recipe {recipe_id!r}'s {field}={item_id!r} is not a known crop or "
                "product id -- vectorized/config_arrays.py's item space only covers "
                "config/crops.json + config/processing.json's products list; add it "
                "there before loading this config."
            )
        return item_index[item_id]

    num_recipes = len(recipes)
    recipe_input_item_idx = np.array(
        [_resolve_item(r["input_item_id"], r["id"], "input_item_id") for r in recipes],
        dtype=np.int8,
    )
    recipe_input_quantity = np.array([r["input_quantity"] for r in recipes], dtype=np.int32)
    recipe_min_quality_rank = np.array(
        [_QUALITY_RANK[r.get("min_quality", "processing")] for r in recipes], dtype=np.int8
    )
    recipe_output_item_idx = np.array(
        [_resolve_item(r["output_item_id"], r["id"], "output_item_id") for r in recipes],
        dtype=np.int8,
    )
    recipe_output_quantity = np.array([r["output_quantity"] for r in recipes], dtype=np.int32)
    recipe_processing_days = np.array(
        [r.get("processing_days", 1) for r in recipes], dtype=np.int16
    )
    recipe_cost = np.array([r.get("cost", 0.0) for r in recipes], dtype=np.float32)
    base_capacity = int(processing.get("base_capacity", 1))

    # -- buyers (config/buyers.json), Phase 5 --
    buyer_ids = tuple(b["id"] for b in buyers)
    num_buyers = len(buyer_ids)
    buyer_max_items = max((len(b.get("items", [])) for b in buyers), default=0)
    buyer_item_idx = np.full((num_buyers, max(1, buyer_max_items)), -1, dtype=np.int8)
    buyer_num_items = np.empty(num_buyers, dtype=np.int8)
    for i, b in enumerate(buyers):
        items = b.get("items", [])
        buyer_num_items[i] = len(items)
        for j, item_id in enumerate(items):
            if item_id not in item_index:
                raise NotImplementedError(
                    f"buyer {b['id']!r}'s items includes {item_id!r}, not a known crop "
                    "or product id -- vectorized/config_arrays.py's item space only "
                    "covers config/crops.json + config/processing.json's products list; "
                    "add it there before loading this config."
                )
            buyer_item_idx[i, j] = item_index[item_id]
    quantity_ranges = [b.get("quantity_range", [5, 12]) for b in buyers]
    buyer_quantity_min = np.array([qr[0] for qr in quantity_ranges], dtype=np.int32)
    buyer_quantity_max = np.array([qr[1] for qr in quantity_ranges], dtype=np.int32)
    buyer_min_quality_rank = np.array(
        [_QUALITY_RANK[b.get("min_quality", "standard")] for b in buyers], dtype=np.int8
    )
    default_penalty_rate = np.float32(contracts_cfg.get("default_penalty_rate", 0.35))
    buyer_price_multiplier = np.array(
        [b.get("contract_price_multiplier", 1.2) for b in buyers], dtype=np.float32
    )
    buyer_deadline_days = np.array([b.get("deadline_days", 10) for b in buyers], dtype=np.int32)
    buyer_penalty_rate = np.array(
        [b.get("penalty_rate", default_penalty_rate) for b in buyers], dtype=np.float32
    )
    buyer_min_reputation = np.array(
        [b.get("min_reputation", 0.0) for b in buyers], dtype=np.float32
    )
    buyer_relationship_bonus_rate = np.array(
        [b.get("relationship_bonus_rate", 0.0) for b in buyers], dtype=np.float32
    )

    contract_offer_interval_days = int(contracts_cfg.get("offer_interval_days", 7))
    contract_offer_expiry_days = int(contracts_cfg.get("offer_expiry_days", 3))
    contract_relationship_gain = np.float32(
        contracts_cfg.get("relationship_gain_per_delivery", 6.0)
    )
    contract_relationship_loss = np.float32(contracts_cfg.get("relationship_loss_per_failure", 5.0))
    contract_relationship_bonus_cap = np.float32(contracts_cfg.get("relationship_bonus_cap", 0.25))

    return VectorConfig(
        crop_ids=crop_ids,
        num_crops=num_crops,
        seed_cost=seed_cost,
        growth_days=growth_days,
        min_yield=min_yield,
        max_yield=max_yield,
        base_price=base_price,
        price_variation=price_variation,
        loss_chance=loss_chance,
        water_interval_days=water_interval_days,
        min_moisture=min_moisture,
        ph_low=ph_low,
        ph_high=ph_high,
        temperature_low=temperature_low,
        temperature_high=temperature_high,
        pest_susceptibility=pest_susceptibility,
        disease_susceptibility=disease_susceptibility,
        nitrogen_demand=nitrogen_demand,
        phosphorus_demand=phosphorus_demand,
        potassium_demand=potassium_demand,
        family_id=family_id,
        unlock_total_revenue=unlock_total_revenue,
        families=families,
        shelf_life_days=shelf_life_days,
        effective_shelf_life_days=effective_shelf_life_days,
        seasonal_demand=seasonal_demand,
        greedy_rank=greedy_rank,
        conservative_rank=conservative_rank,
        initial_moisture=np.float32(soil_initial.get("moisture", 0.65)),
        initial_nitrogen=np.float32(soil_initial.get("nitrogen", 0.75)),
        initial_phosphorus=np.float32(soil_initial.get("phosphorus", 0.75)),
        initial_potassium=np.float32(soil_initial.get("potassium", 0.75)),
        initial_ph=np.float32(soil_initial.get("ph", 6.5)),
        initial_soil_health=np.float32(soil_initial.get("soil_health", 0.7)),
        initial_pest_pressure=np.float32(soil_initial.get("pest_pressure", 0.05)),
        initial_disease_pressure=np.float32(soil_initial.get("disease_pressure", 0.03)),
        regen_moisture=np.float32(soil_regen.get("moisture", 0.0)),
        regen_nitrogen=np.float32(soil_regen.get("nitrogen", 0.0)),
        regen_phosphorus=np.float32(soil_regen.get("phosphorus", 0.0)),
        regen_potassium=np.float32(soil_regen.get("potassium", 0.0)),
        regen_soil_health=np.float32(soil_regen.get("soil_health", 0.0)),
        regen_pest_pressure=np.float32(soil_regen.get("pest_pressure", 0.0)),
        regen_disease_pressure=np.float32(soil_regen.get("disease_pressure", 0.0)),
        same_family_yield_penalty=np.float32(soil_dynamics.get("same_family_yield_penalty", 0.85)),
        same_family_quality_penalty=np.float32(
            soil_dynamics.get("same_family_quality_penalty", 0.9)
        ),
        soil_health_yield_floor=np.float32(soil_dynamics.get("soil_health_yield_floor", 0.85)),
        soil_health_yield_span=np.float32(soil_dynamics.get("soil_health_yield_span", 0.25)),
        harvest_soil_health_cost=np.float32(soil_dynamics.get("harvest_soil_health_cost", 0.02)),
        min_soil_health=np.float32(soil_dynamics.get("min_soil_health", 0.1)),
        fallow_pest_decay=np.float32(soil_dynamics.get("fallow_pest_decay", 0.9)),
        fallow_disease_decay=np.float32(soil_dynamics.get("fallow_disease_decay", 0.9)),
        fallow_soil_health_regen=np.float32(soil_dynamics.get("fallow_soil_health_regen", 0.005)),
        pest_growth_per_day=np.float32(soil_dynamics.get("pest_growth_per_day", 0.005)),
        disease_growth_per_rainfall=np.float32(
            soil_dynamics.get("disease_growth_per_rainfall", 0.08)
        ),
        max_pest_pressure=np.float32(soil_dynamics.get("max_pest_pressure", 0.8)),
        max_disease_pressure=np.float32(soil_dynamics.get("max_disease_pressure", 0.8)),
        water_cost_per_plot=np.float32(watering.get("cost_per_plot", 0.35)),
        water_moisture_added=np.float32(watering.get("moisture_added", 0.45)),
        neglect_loss_chance_penalty_per_day=np.float32(
            watering.get("neglect_loss_chance_penalty_per_day", 0.09)
        ),
        max_neglect_loss_chance_bonus=np.float32(
            watering.get("max_neglect_loss_chance_bonus", 0.43)
        ),
        neglect_yield_penalty_per_day=np.float32(
            watering.get("neglect_yield_penalty_per_day", 0.19)
        ),
        max_neglect_yield_penalty=np.float32(watering.get("max_neglect_yield_penalty", 0.53)),
        fertilizer_cost=np.float32(fertilizer.get("cost", 15)),
        fertilizer_yield_bonus_pct=np.float32(fertilizer.get("yield_bonus_pct", 0.25)),
        fertilizer_loss_chance_reduction=np.float32(fertilizer.get("loss_chance_reduction", 0.03)),
        fertilizer_quality_bonus=np.float32(fertilizer.get("quality_bonus", 0.05)),
        fertilizer_nitrogen_added=np.float32(
            fertilizer.get("nutrients_added", {}).get("nitrogen", 0.0)
        ),
        fertilizer_phosphorus_added=np.float32(
            fertilizer.get("nutrients_added", {}).get("phosphorus", 0.0)
        ),
        fertilizer_potassium_added=np.float32(
            fertilizer.get("nutrients_added", {}).get("potassium", 0.0)
        ),
        season_length_days=weather.get("season_length_days", 15),
        season_temp_low=np.array([r[0] for r in temp_ranges], dtype=np.float32),
        season_temp_high=np.array([r[1] for r in temp_ranges], dtype=np.float32),
        season_rain_chance=season_field("rain_chance", 0.25),
        season_rain_low=np.array([r[0] for r in rain_ranges], dtype=np.float32),
        season_rain_high=np.array([r[1] for r in rain_ranges], dtype=np.float32),
        season_evaporation=season_field("evaporation", 0.08),
        storage_capacity=storage_capacity,
        storage_daily_cost=storage_daily_cost,
        storage_shelf_life_multiplier=storage_shelf_life_multiplier,
        lots_per_plot=lots_per_plot,
        market_minimum_supply_multiplier=market_minimum_supply_multiplier,
        market_supply_decay=market_supply_decay,
        num_products=num_products,
        num_items=num_items,
        product_ids=product_ids,
        num_recipes=num_recipes,
        recipe_input_item_idx=recipe_input_item_idx,
        recipe_input_quantity=recipe_input_quantity,
        recipe_min_quality_rank=recipe_min_quality_rank,
        recipe_output_item_idx=recipe_output_item_idx,
        recipe_output_quantity=recipe_output_quantity,
        recipe_processing_days=recipe_processing_days,
        recipe_cost=recipe_cost,
        base_capacity=base_capacity,
        num_buyers=num_buyers,
        buyer_ids=buyer_ids,
        buyer_max_items=max(1, buyer_max_items),
        buyer_item_idx=buyer_item_idx,
        buyer_num_items=buyer_num_items,
        buyer_quantity_min=buyer_quantity_min,
        buyer_quantity_max=buyer_quantity_max,
        buyer_min_quality_rank=buyer_min_quality_rank,
        buyer_price_multiplier=buyer_price_multiplier,
        buyer_deadline_days=buyer_deadline_days,
        buyer_penalty_rate=buyer_penalty_rate,
        buyer_min_reputation=buyer_min_reputation,
        buyer_relationship_bonus_rate=buyer_relationship_bonus_rate,
        contract_offer_interval_days=contract_offer_interval_days,
        contract_offer_expiry_days=contract_offer_expiry_days,
        contract_relationship_gain=contract_relationship_gain,
        contract_relationship_loss=contract_relationship_loss,
        contract_relationship_bonus_cap=contract_relationship_bonus_cap,
        start_money=np.float32(settings.get("start_money", 100)),
        num_upgrades=num_upgrades,
        upgrade_ids=upgrade_ids,
        upgrade_cost=upgrade_cost,
        upgrade_capacity_amount=upgrade_capacity_amount,
        upgrade_growth_time_reduction=upgrade_growth_time_reduction,
        upgrade_storage_capacity_bonus=upgrade_storage_capacity_bonus,
        upgrade_storage_shelf_life_multiplier=upgrade_storage_shelf_life_multiplier,
        upgrade_processing_capacity_amount=upgrade_processing_capacity_amount,
        total_capacity_bonus=total_capacity_bonus,
        total_processing_capacity_bonus=total_processing_capacity_bonus,
        shelf_life_days_item=shelf_life_days_item,
    )
