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

Still NOT read here: `config/contracts.json`, `config/buyers.json`,
`config/processing.json`, `config/markets.json`, `config/upgrades.json`.
Those subsystems aren't ported yet -- see vectorized/README.md's roadmap --
so loading their config now would be dead weight that silently goes stale.

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


@dataclass(frozen=True)
class VectorConfig:
    # -- crops (config/crops.json), one entry per index 0..num_crops-1 --
    crop_ids: tuple
    num_crops: int
    seed_cost: np.ndarray  # float32
    growth_days: np.ndarray  # int16
    min_yield: np.ndarray  # int32 (roll_yield is an inclusive randint, not continuous)
    max_yield: np.ndarray  # int32
    base_price: np.ndarray  # float32
    price_variation: np.ndarray  # float32
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
    shelf_life_days: np.ndarray  # int16, c.get("shelf_life_days", 7)
    # int16, derived: max(1, round(shelf_life_days * storage_shelf_life_multiplier))
    effective_shelf_life_days: np.ndarray

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

    # -- top-level (config/simulation_settings.json) --
    start_money: np.float32


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

    crop_ids = tuple(c["id"] for c in crops)
    num_crops = len(crop_ids)

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

    expected_value_per_day = base_price * (min_yield + max_yield) / 2.0 / growth_days
    greedy_rank = np.argsort(-expected_value_per_day).astype(np.int8)
    conservative_rank = np.argsort(loss_chance).astype(np.int8)

    soil_initial = soil.get("initial", {})
    soil_regen = soil.get("regen_per_day", {})
    soil_dynamics = soil.get("dynamics", {})

    season_names = ("spring", "summer", "autumn", "winter")
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
    lots_per_plot = (
        int(
            np.max(
                np.ceil(
                    effective_shelf_life_days.astype(np.float64) / growth_days.astype(np.float64)
                )
            )
        )
        + 1
    )

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
        start_money=np.float32(settings.get("start_money", 100)),
    )
