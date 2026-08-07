"""The C accelerator must be bit-identical to the Python reference.

`simulation/_fastplot` replaces the per-plot daily physics loop with compiled
code. Bit-exact seed replay is load-bearing in this project (see CLAUDE.md
and the replay-guard skill), so "close enough" is a failure: a single
last-bit difference compounds across 365 simulated days and changes recorded
outcomes.

These tests therefore compare the two implementations with `==` on floats,
never `pytest.approx`. The Python loop in simulation/weather.py is the
reference; if they disagree, the C code is wrong.

Every test is skipped when the extension is not built, so the suite passes
on a checkout that never ran `python3 tools/build_fastplot.py`.
"""

import copy
import random

import pytest

from simulation import crop_growth, derived, weather
from simulation.state import PlantedCrop, PlayerState, PlotState

pytestmark = pytest.mark.skipif(
    weather._fastplot is None,
    reason="C accelerator not built (python3 tools/build_fastplot.py)",
)


CROPS = [
    {
        "id": "quickweed",
        "growth_days": 3,
        "water_interval_days": 2,
        "min_moisture": 0.35,
        "nutrient_demand": {"nitrogen": 0.025, "phosphorus": 0.01, "potassium": 0.01},
        "ph_range": [5.8, 7.0],
        "temperature_range": [10, 30],
        "pest_susceptibility": 1.0,
        "disease_susceptibility": 1.0,
    },
    {
        "id": "purplehaze",
        "growth_days": 6,
        "water_interval_days": 2,
        "min_moisture": 0.5,
        "nutrient_demand": {"nitrogen": 0.04, "phosphorus": 0.03, "potassium": 0.035},
        "ph_range": [6.2, 6.8],
        "temperature_range": [16, 26],
        "pest_susceptibility": 1.4,
        "disease_susceptibility": 0.8,
    },
]

PLOT_FIELDS = (
    "moisture",
    "nitrogen",
    "phosphorus",
    "potassium",
    "ph",
    "soil_health",
    "pest_pressure",
    "disease_pressure",
)
STRESS_FIELDS = (
    "water_stress",
    "nutrient_stress",
    "temperature_stress",
    "pest_stress",
    "disease_stress",
    "neglect_days",
)


def _profiles():
    crops_by_id = {crop["id"]: crop for crop in CROPS}
    profiles = {crop_id: derived.CropProfile(crop) for crop_id, crop in crops_by_id.items()}
    flat = {crop_id: profile.flat for crop_id, profile in profiles.items()}
    return crops_by_id, profiles, flat


def _random_player(rng, plot_count=6):
    player = PlayerState(money=100.0, slots_total=plot_count)
    player.day = rng.randrange(0, 400)
    player.plots = []
    for index in range(plot_count):
        plot = PlotState(
            # Deliberately spans the clamp boundaries (and slightly beyond),
            # so saturated and depleted plots are exercised, not just the
            # comfortable middle where every branch agrees trivially.
            moisture=rng.uniform(-0.05, 1.05),
            nitrogen=rng.uniform(0.0, 1.0),
            phosphorus=rng.uniform(0.0, 1.0),
            potassium=rng.uniform(0.0, 1.0),
            ph=rng.uniform(4.5, 8.5),
            soil_health=rng.uniform(0.0, 1.0),
            pest_pressure=rng.uniform(0.0, 0.9),
            disease_pressure=rng.uniform(0.0, 0.9),
        )
        # A third of plots left fallow, to cover the `planted is None` branch.
        if rng.random() > 0.33:
            crop = rng.choice(CROPS)
            planted = PlantedCrop(
                crop_id=crop["id"],
                day_planted=max(0, player.day - rng.randrange(0, 12)),
                growth_days_required=crop["growth_days"],
                last_watered_day=max(0, player.day - rng.randrange(0, 15)),
            )
            planted.water_stress = rng.uniform(0.0, 3.0)
            planted.nutrient_stress = rng.uniform(0.0, 3.0)
            planted.temperature_stress = rng.uniform(0.0, 3.0)
            planted.pest_stress = rng.uniform(0.0, 3.0)
            planted.disease_stress = rng.uniform(0.0, 3.0)
            planted.plot_index = index
            plot.crop = planted
            player.planted.append(planted)
        player.plots.append(plot)
    return player


def _random_weather(rng):
    return {
        "season": rng.choice(["spring", "summer", "autumn", "winter"]),
        "temperature": round(rng.uniform(-5.0, 40.0), 2),
        "rainfall": round(rng.uniform(0.0, 0.4), 3),
        "evaporation": round(rng.uniform(0.0, 0.2), 3),
    }


def _snapshot(player):
    state = []
    for plot in player.plots:
        entry = {field: getattr(plot, field) for field in PLOT_FIELDS}
        if plot.crop is None:
            entry["crop"] = None
        else:
            entry["crop"] = {field: getattr(plot.crop, field) for field in STRESS_FIELDS}
        state.append(entry)
    return state


def _assert_bit_identical(expected, actual, context):
    assert len(expected) == len(actual)
    for index, (want, got) in enumerate(zip(expected, actual, strict=True)):
        for field in PLOT_FIELDS:
            # Exact equality, deliberately: see this module's docstring.
            assert got[field] == want[field], (
                f"{context}: plot {index} field {field!r} diverged: "
                f"python={want[field]!r} c={got[field]!r}"
            )
        assert (want["crop"] is None) == (got["crop"] is None)
        if want["crop"] is not None:
            for field in STRESS_FIELDS:
                assert got["crop"][field] == want["crop"][field], (
                    f"{context}: plot {index} crop field {field!r} diverged: "
                    f"python={want['crop'][field]!r} c={got['crop'][field]!r}"
                )


def _run_both(player, day_weather, plot_regen, dynamics):
    """Apply one day to two identical copies -- Python loop and C kernel."""
    crops_by_id, profiles, flat = _profiles()
    python_player = copy.deepcopy(player)
    c_player = copy.deepcopy(player)

    # crop_profiles_flat omitted -> the fast path is declined, so this really
    # is the Python reference loop even when the extension is importable.
    weather.apply_weather(
        python_player, crops_by_id, day_weather, crop_growth, profiles, plot_regen, dynamics
    )
    weather.apply_weather(
        c_player, crops_by_id, day_weather, crop_growth, profiles, plot_regen, dynamics, flat
    )
    return _snapshot(python_player), _snapshot(c_player)


REGEN = {
    "moisture": 0.004,
    "nitrogen": 0.003,
    "phosphorus": 0.002,
    "potassium": 0.002,
    "soil_health": 0.001,
    "pest_pressure": 0.002,
    "disease_pressure": 0.002,
}


def test_matches_python_over_a_long_randomized_run():
    """The headline check: many days, many plots, exact float equality."""
    rng = random.Random(20260807)
    dynamics = derived.SoilDynamics({})
    for iteration in range(400):
        player = _random_player(rng)
        day_weather = _random_weather(rng)
        expected, actual = _run_both(player, day_weather, REGEN, dynamics)
        _assert_bit_identical(expected, actual, f"iteration {iteration}")


def test_matches_python_with_regeneration_disabled():
    """No-regen is a different set of branches -- fields go untouched."""
    rng = random.Random(11)
    dynamics = derived.SoilDynamics({})
    for iteration in range(120):
        player = _random_player(rng)
        expected, actual = _run_both(player, _random_weather(rng), {}, dynamics)
        _assert_bit_identical(expected, actual, f"no-regen iteration {iteration}")


def test_matches_python_with_configured_soil_dynamics():
    """Non-default dynamics, so the kernel cannot be passing on defaults."""
    rng = random.Random(99)
    dynamics = derived.SoilDynamics(
        {
            "dynamics": {
                "fallow_pest_decay": 0.77,
                "fallow_disease_decay": 0.81,
                "fallow_soil_health_regen": 0.013,
                "pest_growth_per_day": 0.011,
                "disease_growth_per_rainfall": 0.13,
                "max_pest_pressure": 0.6,
                "max_disease_pressure": 0.55,
            }
        }
    )
    for iteration in range(120):
        player = _random_player(rng)
        expected, actual = _run_both(player, _random_weather(rng), REGEN, dynamics)
        _assert_bit_identical(expected, actual, f"dynamics iteration {iteration}")


def test_all_plots_fallow_matches_python():
    rng = random.Random(5)
    dynamics = derived.SoilDynamics({})
    for iteration in range(40):
        player = _random_player(rng)
        for plot in player.plots:
            plot.crop = None
        player.planted.clear()
        expected, actual = _run_both(player, _random_weather(rng), REGEN, dynamics)
        _assert_bit_identical(expected, actual, f"fallow iteration {iteration}")


def test_unknown_crop_raises_key_error_like_python():
    """A plot whose crop is missing from the catalog must still be a KeyError."""
    _crops_by_id, _profiles_by_id, flat = _profiles()
    player = _random_player(random.Random(3))
    player.plots[0].crop = PlantedCrop(crop_id="not_a_crop", day_planted=0, growth_days_required=3)
    with pytest.raises(KeyError):
        weather.apply_weather(
            player,
            {},
            _random_weather(random.Random(4)),
            crop_growth,
            {},
            REGEN,
            derived.SoilDynamics({}),
            flat,
        )


def test_profile_layout_constant_matches_python():
    """A stale .so must be rejected rather than misread CropProfile.flat."""
    assert weather._fastplot.PROFILE_LAYOUT == derived.PROFILE_LAYOUT
