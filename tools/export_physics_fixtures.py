"""Export golden fixtures for farm-c's Phase 1 physics port
(farm-c/tests/test_physics.c): simulation/crop_growth.py's four functions
plus simulation/weather.py's season/generate/apply.

Same shape and rationale as tools/export_agent_fixtures.py and
tools/export_rng_fixtures.py: Python is the oracle (these are pure/RNG-
adjacent functions, not a golden-replay run), so this drives real
crop_growth.py/weather.py functions over a handful of synthetic crops and
hand-picked edge-case scenarios (moisture/pH/temperature on both sides of a
crop's tolerance band, clamp-saturating accumulated stress, fertilized vs
not, same-family rotation penalty, plot=None), and records inputs/outputs
as float.hex() so farm-c/tests/test_physics.c can assert bit-for-bit
equality rather than epsilon-compare derived arithmetic.

compute_harvest_outcome's fixtures are RNG-adjacent: each case uses a fresh
`RandomEvents(seed)`, and the C test seeds a fresh FarmRng with the same
seed and calls rng_roll_loss/rng_roll_yield in the same order -- already
proven bit-exact by farm-c/tests/test_rng.c (Phase 0), so recording just the
seed (not the raw draws) is sufficient here.

Usage: python3 tools/export_physics_fixtures.py
(also invoked by `make fixtures-physics` in farm-c/Makefile)
"""

import json
import os
import sys
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from simulation import crop_growth, derived, weather  # noqa: E402
from simulation.random_events import RandomEvents  # noqa: E402
from simulation.state import PlantedCrop, PlotState  # noqa: E402

OUT_PATH = os.path.join(REPO_ROOT, "farm-c", "tests", "fixtures", "physics.json")


def hexf(x: float) -> str:
    return float(x).hex()


# --- synthetic crop catalog -------------------------------------------------
# Deliberately varied: some crops have a family (for the rotation-penalty
# path), tolerance bands narrow enough that the scenario values below land
# both inside and outside them, and differing susceptibilities/demand so the
# per-term contributions to stress are distinguishable in a diff.
CROPS = [
    {
        "id": "sunwheat",
        "family": "grain",
        "seed_cost": 10.0,
        "growth_days": 6,
        "min_yield": 3,
        "max_yield": 7,
        "loss_chance": 0.12,
        "water_interval_days": 2,
        "temperature_range": [12.0, 28.0],
        "ph_range": [6.0, 7.0],
        "min_moisture": 0.4,
        "pest_susceptibility": 0.8,
        "disease_susceptibility": 1.1,
        "nutrient_demand": {"nitrogen": 0.03, "phosphorus": 0.015, "potassium": 0.02},
    },
    {
        "id": "moonberry",
        "family": "berry",
        "seed_cost": 22.0,
        "growth_days": 9,
        "min_yield": 2,
        "max_yield": 5,
        "loss_chance": 0.2,
        "water_interval_days": 1,
        "temperature_range": [15.0, 24.0],
        "ph_range": [5.5, 6.5],
        "min_moisture": 0.55,
        "pest_susceptibility": 1.4,
        "disease_susceptibility": 0.9,
        "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.03, "potassium": 0.015},
    },
    {
        "id": "dustroot",
        "family": None,
        "seed_cost": 5.0,
        "growth_days": 4,
        "min_yield": 4,
        "max_yield": 9,
        "loss_chance": 0.08,
        "water_interval_days": 3,
        "temperature_range": [5.0, 35.0],
        "ph_range": [5.0, 8.0],
        "min_moisture": 0.15,
        "pest_susceptibility": 0.5,
        "disease_susceptibility": 0.5,
        "nutrient_demand": {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01},
    },
]

FERTILIZER_CONFIG = {
    "cost": 15.0,
    "loss_chance_reduction": 0.03,
    "yield_bonus_pct": 0.25,
    "quality_bonus": 0.07,
}

WATERING_SETTINGS = {
    "neglect_loss_chance_penalty_per_day": 0.09,
    "neglect_yield_penalty_per_day": 0.19,
    "max_neglect_loss_chance_bonus": 0.43,
    "max_neglect_yield_penalty": 0.53,
    "cost_per_plot": 0.35,
    "moisture_added": 0.45,
}

SOIL_CONFIG = {
    "dynamics": {
        "harvest_soil_health_cost": 0.02,
        "min_soil_health": 0.1,
        "fallow_pest_decay": 0.9,
        "fallow_disease_decay": 0.9,
        "fallow_soil_health_regen": 0.005,
        "pest_growth_per_day": 0.005,
        "disease_growth_per_rainfall": 0.08,
        "max_pest_pressure": 0.8,
        "max_disease_pressure": 0.8,
        "same_family_yield_penalty": 0.72,
        "same_family_quality_penalty": 0.8,
        "soil_health_yield_floor": 0.85,
        "soil_health_yield_span": 0.25,
    },
    "regen_per_day": {
        "moisture": 0.0,
        "nitrogen": 0.07,
        "phosphorus": 0.04,
        "potassium": 0.04,
        "soil_health": 0.01,
        "pest_pressure": 0.005,
        "disease_pressure": 0.01,
    },
}
DYNAMICS = derived.SoilDynamics(SOIL_CONFIG)
PLOT_REGEN = SOIL_CONFIG["regen_per_day"]

WEATHER_WORLD = {
    "season_length_days": 15,
    "seasons": {
        "spring": {
            "temperature_range": [8, 23],
            "rain_chance": 0.42,
            "rainfall_range": [0.12, 0.32],
            "evaporation": 0.06,
        },
        "summer": {
            "temperature_range": [18, 36],
            "rain_chance": 0.18,
            "rainfall_range": [0.08, 0.24],
            "evaporation": 0.12,
        },
        "autumn": {
            "temperature_range": [7, 25],
            "rain_chance": 0.35,
            "rainfall_range": [0.10, 0.30],
            "evaporation": 0.07,
        },
        "winter": {
            "temperature_range": [-3, 14],
            "rain_chance": 0.28,
            "rainfall_range": [0.06, 0.20],
            "evaporation": 0.04,
        },
    },
}


def serialize_crops():
    out = []
    for i, crop in enumerate(CROPS):
        out.append({**crop, "index": i})
    return out


def make_planted(crop_index, **overrides):
    crop = CROPS[crop_index]
    kwargs = dict(
        crop_id=crop["id"],
        day_planted=0,
        growth_days_required=crop["growth_days"],
        last_watered_day=0,
        neglect_days=0,
        fertilized=False,
        water_stress=0.0,
        nutrient_stress=0.0,
        temperature_stress=0.0,
        pest_stress=0.0,
        disease_stress=0.0,
    )
    kwargs.update(overrides)
    return PlantedCrop(**kwargs)


def make_plot(**overrides):
    kwargs = dict(
        moisture=0.5,
        nitrogen=0.5,
        phosphorus=0.5,
        potassium=0.5,
        ph=6.2,
        soil_health=0.6,
        pest_pressure=0.1,
        disease_pressure=0.05,
        previous_crop_family=None,
    )
    kwargs.update(overrides)
    return PlotState(**kwargs)


CROP_ID_TO_INDEX = {c["id"]: i for i, c in enumerate(CROPS)}


def serialize_planted(p):
    return {
        "crop_id": p.crop_id,
        "crop_index": CROP_ID_TO_INDEX[p.crop_id],
        "day_planted": p.day_planted,
        "growth_days_required": p.growth_days_required,
        "last_watered_day": p.last_watered_day,
        "neglect_days": p.neglect_days,
        "fertilized": p.fertilized,
        "water_stress": hexf(p.water_stress),
        "nutrient_stress": hexf(p.nutrient_stress),
        "temperature_stress": hexf(p.temperature_stress),
        "pest_stress": hexf(p.pest_stress),
        "disease_stress": hexf(p.disease_stress),
    }


def serialize_plot(plot):
    return {
        "moisture": hexf(plot.moisture),
        "nitrogen": hexf(plot.nitrogen),
        "phosphorus": hexf(plot.phosphorus),
        "potassium": hexf(plot.potassium),
        "ph": hexf(plot.ph),
        "soil_health": hexf(plot.soil_health),
        "pest_pressure": hexf(plot.pest_pressure),
        "disease_pressure": hexf(plot.disease_pressure),
        "previous_crop_family": plot.previous_crop_family,
    }


def crop_dict(crop_index):
    return CROPS[crop_index]


# --- update_crop_stress cases -----------------------------------------------


def build_update_stress_cases():
    cases = []

    def add(name, crop_index, planted_kwargs, plot_kwargs, temperature, evaporation):
        planted = make_planted(crop_index, **planted_kwargs)
        plot = make_plot(**plot_kwargs)
        before_planted = serialize_planted(planted)
        before_plot = serialize_plot(plot)
        weather_dict = {"temperature": temperature, "evaporation": evaporation}
        crop_growth.update_crop_stress(planted, plot, crop_dict(crop_index), weather_dict)
        cases.append(
            {
                "name": name,
                "crop_index": crop_index,
                "before_planted": before_planted,
                "before_plot": before_plot,
                "temperature": hexf(temperature),
                "evaporation": hexf(evaporation),
                "after_planted": serialize_planted(planted),
                "after_plot": serialize_plot(plot),
            }
        )

    # Comfortable middle of every band: small/no stress accrual.
    add("comfortable", 0, {}, {"moisture": 0.6, "ph": 6.4}, 20.0, 0.08)
    # Moisture below min_moisture, pH below range, temperature below range.
    add(
        "below_all_bands",
        0,
        {},
        {"moisture": 0.1, "nitrogen": 0.05, "phosphorus": 0.02, "potassium": 0.01, "ph": 5.0},
        4.0,
        0.05,
    )
    # pH above range, temperature above range, high pest/disease pressure.
    add(
        "above_all_bands",
        1,
        {},
        {"moisture": 0.9, "ph": 7.8, "pest_pressure": 0.7, "disease_pressure": 0.6},
        30.0,
        0.12,
    )
    # Nutrients already fully depleted -- shortfall saturates, and the
    # post-update clamp keeps them at 0 rather than going negative.
    add(
        "nutrients_depleted",
        2,
        {},
        {"moisture": 0.5, "nitrogen": 0.0, "phosphorus": 0.0, "potassium": 0.0, "ph": 6.5},
        18.0,
        0.03,
    )
    # High moisture near saturation, evaporation large enough to test the
    # upper clamp roundtrip in one step.
    add(
        "near_saturation",
        2,
        {"neglect_days": 3},
        {"moisture": 0.98, "nitrogen": 0.99, "phosphorus": 0.99, "potassium": 0.99, "ph": 6.5},
        22.0,
        0.15,
    )
    return cases


# --- harvest_multipliers cases ----------------------------------------------


def build_harvest_multipliers_cases():
    cases = []

    def add(name, crop_index, planted_kwargs, plot_kwargs):
        planted = make_planted(crop_index, **planted_kwargs)
        plot = make_plot(**plot_kwargs) if plot_kwargs is not None else None
        yield_mult, quality_mult = crop_growth.harvest_multipliers(
            planted, crop_dict(crop_index), plot, FERTILIZER_CONFIG, DYNAMICS
        )
        cases.append(
            {
                "name": name,
                "crop_index": crop_index,
                "planted": serialize_planted(planted),
                "plot": serialize_plot(plot) if plot is not None else None,
                "expected_yield_multiplier": hexf(yield_mult),
                "expected_quality_multiplier": hexf(quality_mult),
            }
        )

    # No stress at all, not fertilized, no plot -> multipliers untouched by
    # the plot-dependent terms.
    add("no_stress_no_plot", 0, {}, None)
    # No stress, fertilized, no plot -> quality bonus only.
    add("fertilized_no_plot", 0, {"fertilized": True}, None)
    # Heavy accumulated stress -> both multipliers pinned at their floor.
    add(
        "heavy_stress_no_plot",
        0,
        {
            "water_stress": 5.0,
            "nutrient_stress": 5.0,
            "temperature_stress": 5.0,
            "pest_stress": 5.0,
            "disease_stress": 5.0,
            "neglect_days": 10,
        },
        None,
    )
    # Family match -> rotation penalty applies to both multipliers, plus the
    # soil-health yield curve.
    add(
        "family_match_with_plot",
        0,
        {"water_stress": 0.2, "nutrient_stress": 0.1},
        {"previous_crop_family": "grain", "soil_health": 0.5},
    )
    # Family mismatch (plot's previous family differs from this crop's) ->
    # no rotation penalty, but soil-health term still applies.
    add(
        "family_mismatch_with_plot",
        0,
        {"water_stress": 0.2, "nutrient_stress": 0.1},
        {"previous_crop_family": "berry", "soil_health": 0.9},
    )
    # Crop has no family at all -> rotation penalty branch is skipped even
    # though the plot has a previous family recorded.
    add(
        "crop_has_no_family",
        2,
        {"water_stress": 0.1},
        {"previous_crop_family": "grain", "soil_health": 0.3},
    )
    # Fertilized quality bonus stacked on top of moderate stress, with a plot
    # present (soil-health term applies to yield only).
    add(
        "fertilized_with_plot",
        1,
        {"fertilized": True, "water_stress": 0.3, "nutrient_stress": 0.2},
        {"previous_crop_family": None, "soil_health": 0.7},
    )
    return cases


# --- quality_grade cases -----------------------------------------------------


def build_quality_grade_cases():
    scores = [1.0, 0.95, 0.9, 0.899999, 0.75, 0.62, 0.619999, 0.45, 0.3, 0.299999, 0.1, 0.0, -0.5]
    return [{"score": hexf(s), "expected_grade": crop_growth.quality_grade(s)} for s in scores]


# --- compute_harvest_outcome cases ------------------------------------------


def build_harvest_outcome_cases():
    cases = []

    def add(name, crop_index, planted_kwargs, plot_kwargs, seed):
        planted = make_planted(crop_index, **planted_kwargs)
        plot = make_plot(**plot_kwargs) if plot_kwargs is not None else None
        rng = RandomEvents(seed)
        lost, yield_amount = crop_growth.compute_harvest_outcome(
            planted,
            crop_dict(crop_index),
            WATERING_SETTINGS,
            FERTILIZER_CONFIG,
            rng,
            plot,
            DYNAMICS,
        )
        cases.append(
            {
                "name": name,
                "crop_index": crop_index,
                "planted": serialize_planted(planted),
                "plot": serialize_plot(plot) if plot is not None else None,
                "seed": seed,
                "expected_lost": lost,
                "expected_yield": yield_amount,
            }
        )

    add("clean_harvest", 0, {}, {"soil_health": 0.8}, 1)
    add("clean_harvest_alt_seed", 0, {}, {"soil_health": 0.8}, 42)
    add("fertilized", 0, {"fertilized": True}, {"soil_health": 0.9}, 777)
    add(
        "heavy_neglect",
        1,
        {"neglect_days": 8, "water_stress": 0.4, "nutrient_stress": 0.3},
        {"soil_health": 0.4},
        123456789,
    )
    add("no_plot", 2, {}, None, 2024)
    add("high_loss_chance_crop", 1, {"neglect_days": 6}, {"soil_health": 0.5}, 99)
    for seed in range(3000, 3040):
        add(f"sweep_seed_{seed}", 2, {"neglect_days": seed % 5}, {"soil_health": 0.6}, seed)
    return cases


# --- weather.season_for_day / generate_weather cases ------------------------


def build_season_cases():
    cases = []
    for day in [0, 1, 14, 15, 16, 29, 30, 44, 45, 59, 60, 100, 365, 1000]:
        cases.append(
            {
                "day": day,
                "season_length_days": 15,
                "expected_season": weather.season_for_day(day, 15),
            }
        )
    for season_length in [1, 7, 30]:
        for day in [0, 5, 12, 40, 90]:
            cases.append(
                {
                    "day": day,
                    "season_length_days": season_length,
                    "expected_season": weather.season_for_day(day, season_length),
                }
            )
    return cases


def build_generate_weather_cases():
    cases = []
    for seed in [0, 1, 42, 777, 123456789, 55555]:
        for day in [0, 5, 15, 20, 30, 44, 45, 100, 200]:
            rng = RandomEvents(seed)
            result = weather.generate_weather(day, WEATHER_WORLD, rng)
            cases.append(
                {
                    "seed": seed,
                    "day": day,
                    "expected_season": result["season"],
                    "expected_temperature": hexf(result["temperature"]),
                    "expected_rainfall": hexf(result["rainfall"]),
                    "expected_evaporation": hexf(result["evaporation"]),
                }
            )
    return cases


# --- weather.apply_weather cases --------------------------------------------


def build_apply_weather_cases():
    cases = []
    crops_by_id = {c["id"]: c for c in CROPS}

    def make_player_plots():
        plots = [
            make_plot(
                moisture=0.4,
                nitrogen=0.3,
                phosphorus=0.6,
                potassium=0.5,
                ph=6.1,
                soil_health=0.5,
                pest_pressure=0.2,
                disease_pressure=0.1,
                previous_crop_family=None,
            ),
            make_plot(
                moisture=0.9,
                nitrogen=0.9,
                phosphorus=0.9,
                potassium=0.9,
                ph=7.9,
                soil_health=0.95,
                pest_pressure=0.05,
                disease_pressure=0.02,
                previous_crop_family="grain",
            ),
            make_plot(
                moisture=0.05,
                nitrogen=0.1,
                phosphorus=0.05,
                potassium=0.05,
                ph=4.5,
                soil_health=0.15,
                pest_pressure=0.6,
                disease_pressure=0.55,
                previous_crop_family="berry",
            ),
        ]
        # Plot 0: fallow.
        plots[0].crop = None
        # Plot 1: growing sunwheat (family "grain"), fertilized, some neglect.
        plots[1].crop = make_planted(0, fertilized=True, neglect_days=1, last_watered_day=-2)
        # Plot 2: growing dustroot (no family), heavy pre-existing stress.
        plots[2].crop = make_planted(
            2, water_stress=0.3, nutrient_stress=0.2, temperature_stress=0.1, last_watered_day=-5
        )
        return plots

    def add(name, day, weather_dict, plot_regen, dynamics_soil_config):
        player = SimpleNamespace(day=day, plots=make_player_plots())
        before = [serialize_full_plot(p) for p in player.plots]
        dynamics = derived.SoilDynamics(dynamics_soil_config)
        weather.apply_weather(
            player,
            crops_by_id,
            weather_dict,
            crop_growth,
            plot_regen=plot_regen,
            dynamics=dynamics,
        )
        after = [serialize_full_plot(p) for p in player.plots]
        cases.append(
            {
                "name": name,
                "day": day,
                "weather": {
                    "temperature": hexf(weather_dict["temperature"]),
                    "rainfall": hexf(weather_dict["rainfall"]),
                    "evaporation": hexf(weather_dict["evaporation"]),
                },
                "plot_regen": plot_regen,
                "soil_dynamics": dynamics_soil_config["dynamics"],
                "before": before,
                "after": after,
            }
        )

    add(
        "typical_day_with_regen",
        10,
        {"temperature": 21.5, "rainfall": 0.18, "evaporation": 0.09},
        PLOT_REGEN,
        SOIL_CONFIG,
    )
    add(
        "no_rain_no_regen",
        11,
        {"temperature": 33.0, "rainfall": 0.0, "evaporation": 0.14},
        {k: 0.0 for k in PLOT_REGEN},
        {"dynamics": SOIL_CONFIG["dynamics"]},
    )
    add(
        "cold_snap_heavy_rain",
        45,
        {"temperature": -2.0, "rainfall": 0.31, "evaporation": 0.04},
        PLOT_REGEN,
        SOIL_CONFIG,
    )
    return cases


def serialize_full_plot(plot):
    out = serialize_plot(plot)
    out["crop"] = serialize_planted(plot.crop) if plot.crop is not None else None
    return out


def main():
    fixtures = {
        "crops": serialize_crops(),
        "fertilizer": FERTILIZER_CONFIG,
        "watering": WATERING_SETTINGS,
        "soil_dynamics": SOIL_CONFIG["dynamics"],
        "plot_regen": SOIL_CONFIG["regen_per_day"],
        "weather_world": WEATHER_WORLD,
        "update_crop_stress_cases": build_update_stress_cases(),
        "harvest_multipliers_cases": build_harvest_multipliers_cases(),
        "quality_grade_cases": build_quality_grade_cases(),
        "harvest_outcome_cases": build_harvest_outcome_cases(),
        "season_cases": build_season_cases(),
        "generate_weather_cases": build_generate_weather_cases(),
        "apply_weather_cases": build_apply_weather_cases(),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(fixtures, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
