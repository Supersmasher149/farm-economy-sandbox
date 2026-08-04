"""Seasonal deterministic weather generation and plot updates."""


def season_for_day(day: int, season_length: int = 15) -> str:
    seasons = ("spring", "summer", "autumn", "winter")
    return seasons[(day // season_length) % len(seasons)]


def generate_weather(day: int, config: dict, rng) -> dict:
    season = season_for_day(day, config.get("season_length_days", 15))
    values = config.get("seasons", {}).get(season, {})
    temp_range = values.get("temperature_range", [12, 24])
    rain_chance = values.get("rain_chance", 0.25)
    rainfall_range = values.get("rainfall_range", [0.08, 0.25])
    temperature = rng.uniform(temp_range[0], temp_range[1])
    rainfall = rng.uniform(*rainfall_range) if rng.chance(rain_chance) else 0.0
    evaporation = values.get("evaporation", 0.08) + max(0.0, temperature - 25) * 0.005
    return {
        "season": season,
        "temperature": round(temperature, 2),
        "rainfall": round(rainfall, 3),
        "evaporation": round(evaporation, 3),
    }


def apply_weather(player, crops_by_id: dict, weather: dict, growth_module) -> None:
    for plot in player.plots:
        plot.moisture = min(1.0, plot.moisture + weather.get("rainfall", 0.0))
        if plot.crop is None:
            plot.pest_pressure = max(0.0, plot.pest_pressure * 0.9)
            plot.disease_pressure = max(0.0, plot.disease_pressure * 0.9)
            plot.soil_health = min(1.0, plot.soil_health + 0.005)
            continue
        crop = crops_by_id[plot.crop.crop_id]
        growth_module.update_crop_stress(plot.crop, plot, crop, weather)
        interval = crop.get("water_interval_days", 3)
        overdue = player.day - plot.crop.last_watered_day - interval
        plot.crop.neglect_days = max(0, overdue)
        humidity = weather.get("rainfall", 0.0)
        plot.disease_pressure = min(0.8, plot.disease_pressure + humidity * 0.08)
        plot.pest_pressure = min(0.8, plot.pest_pressure + 0.005)
