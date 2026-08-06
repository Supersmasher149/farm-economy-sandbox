"""Seasonal deterministic weather generation and plot updates."""

from simulation import derived

SEASONS = ("spring", "summer", "autumn", "winter")


def season_for_day(day: int, season_length: int = 15) -> str:
    return SEASONS[(day // season_length) % 4]


def generate_weather(day: int, config: dict, rng) -> dict:
    # Season parameters are fixed config, resolved once per config object
    # rather than re-read (with defaults) on every simulated day. The rng call
    # order below is load-bearing and unchanged: temperature draw, then the
    # rain check, then the rainfall draw only if it rained.
    params = derived.weather_params(config)
    season = SEASONS[(day // params.season_length) % 4]
    temp_low, temp_high, rain_chance, rain_low, rain_high, base_evaporation = params.by_season[
        season
    ]
    temperature = rng.uniform(temp_low, temp_high)
    rainfall = rng.uniform(rain_low, rain_high) if rng.chance(rain_chance) else 0.0
    evaporation = base_evaporation + max(0.0, temperature - 25) * 0.005
    return {
        "season": season,
        "temperature": round(temperature, 2),
        "rainfall": round(rainfall, 3),
        "evaporation": round(evaporation, 3),
    }


def apply_weather(
    player, crops_by_id: dict, weather: dict, growth_module, crop_profiles=None, plot_regen=None
) -> None:
    # Weather values are the same for every plot, so they are read once here
    # rather than per plot. `crop_profiles` maps crop_id to its cached static
    # growth inputs; the engine passes the one it already holds, and omitting
    # it just falls back to an equivalent per-crop lookup.
    rainfall = weather.get("rainfall", 0.0)
    day = player.day
    # Resolved once per day, not per plot -- `plot_regen` is already a
    # cached, per-world dict (see derived.WorldLookups.plot_regen), and
    # defaults to no regen so a caller that omits it keeps today's behaviour.
    # Covers nitrogen/phosphorus/potassium plus soil_health/pest_pressure/
    # disease_pressure -- all four of these only otherwise recover when a
    # plot sits completely fallow (see the `planted is None` branch below),
    # which no shipped agent strategy ever deliberately does, so under
    # continuous farming every one of them used to march to its worst value
    # and stay there. This makes that recovery a small, constant trickle
    # regardless of occupancy; the fallow-only bonuses below still stack on
    # top, so deliberate rest/rotation keeps paying off faster.
    regen_moisture = plot_regen.get("moisture", 0.0) if plot_regen else 0.0
    regen_n = plot_regen.get("nitrogen", 0.0) if plot_regen else 0.0
    regen_p = plot_regen.get("phosphorus", 0.0) if plot_regen else 0.0
    regen_k = plot_regen.get("potassium", 0.0) if plot_regen else 0.0
    regen_soil_health = plot_regen.get("soil_health", 0.0) if plot_regen else 0.0
    regen_pest = plot_regen.get("pest_pressure", 0.0) if plot_regen else 0.0
    regen_disease = plot_regen.get("disease_pressure", 0.0) if plot_regen else 0.0
    regenerates_nutrients = regen_n or regen_p or regen_k
    for plot in player.plots:
        # Config-accepted (soil.regen_per_day.moisture) alongside the other
        # SOIL_LEVELS regen fields, folded into the same rainfall addition
        # since both are a same-day, once-capped increase to moisture.
        plot.moisture = min(1.0, plot.moisture + rainfall + regen_moisture)
        if regenerates_nutrients:
            # A crop's own demand (see crop_growth.update_crop_stress) still
            # outpaces this for nutrient-hungry crops under sustained
            # monocropping; it only caps runaway depletion, it doesn't erase
            # the cost of farming.
            if regen_n:
                plot.nitrogen = min(1.0, plot.nitrogen + regen_n)
            if regen_p:
                plot.phosphorus = min(1.0, plot.phosphorus + regen_p)
            if regen_k:
                plot.potassium = min(1.0, plot.potassium + regen_k)
        if regen_soil_health:
            plot.soil_health = min(1.0, plot.soil_health + regen_soil_health)
        if regen_pest:
            plot.pest_pressure = max(0.0, plot.pest_pressure - regen_pest)
        if regen_disease:
            plot.disease_pressure = max(0.0, plot.disease_pressure - regen_disease)
        planted = plot.crop
        if planted is None:
            plot.pest_pressure = max(0.0, plot.pest_pressure * 0.9)
            plot.disease_pressure = max(0.0, plot.disease_pressure * 0.9)
            plot.soil_health = min(1.0, plot.soil_health + 0.005)
            continue
        crop_id = planted.crop_id
        crop = crops_by_id[crop_id]
        growth_module.update_crop_stress(
            planted,
            plot,
            crop,
            weather,
            crop_profiles[crop_id] if crop_profiles is not None else None,
        )
        interval = crop.get("water_interval_days", 3)
        overdue = day - planted.last_watered_day - interval
        planted.neglect_days = max(0, overdue)
        plot.disease_pressure = min(0.8, plot.disease_pressure + rainfall * 0.08)
        plot.pest_pressure = min(0.8, plot.pest_pressure + 0.005)
