"""Pure crop stress and harvest calculations."""


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def update_crop_stress(planted, plot, crop: dict, weather: dict) -> None:
    """Accumulate bounded stress for one growing crop after today's weather."""
    minimum_moisture = crop.get("min_moisture", 0.35)
    planted.water_stress += max(0.0, minimum_moisture - plot.moisture)

    needs = crop.get("nutrient_demand", {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01})
    nutrient_shortfall = sum(max(0.0, amount - getattr(plot, name)) for name, amount in needs.items())
    preferred_ph = crop.get("ph_range", [5.8, 7.0])
    ph_stress = 0.0
    if plot.ph < preferred_ph[0]:
        ph_stress = (preferred_ph[0] - plot.ph) * 0.1
    elif plot.ph > preferred_ph[1]:
        ph_stress = (plot.ph - preferred_ph[1]) * 0.1
    planted.nutrient_stress += nutrient_shortfall + ph_stress

    preferred = crop.get("temperature_range", [10, 30])
    temperature = weather.get("temperature", 20)
    if temperature < preferred[0]:
        planted.temperature_stress += (preferred[0] - temperature) / 20
    elif temperature > preferred[1]:
        planted.temperature_stress += (temperature - preferred[1]) / 20

    planted.pest_stress += plot.pest_pressure * crop.get("pest_susceptibility", 1.0)
    planted.disease_stress += plot.disease_pressure * crop.get("disease_susceptibility", 1.0)

    evaporation = weather.get("evaporation", 0.08)
    plot.moisture = _clamp(plot.moisture - evaporation)
    for nutrient, amount in needs.items():
        setattr(plot, nutrient, _clamp(getattr(plot, nutrient) - amount))


def harvest_multipliers(planted, crop: dict, plot=None) -> tuple[float, float]:
    """Return yield and quality multipliers from accumulated crop conditions."""
    stress = (
        planted.water_stress * 0.16
        + planted.nutrient_stress * 0.18
        + planted.temperature_stress * 0.12
        + planted.pest_stress * 0.10
        + planted.disease_stress * 0.12
        + planted.neglect_days * 0.08
    )
    yield_multiplier = _clamp(1.0 - stress, 0.15, 1.35)
    quality_multiplier = _clamp(1.0 - stress * 1.25, 0.0, 1.2)

    if planted.fertilized:
        yield_multiplier += 0.15
        quality_multiplier += 0.05
    if plot is not None:
        family = crop.get("family")
        if family and plot.previous_crop_family == family:
            yield_multiplier *= 0.85
            quality_multiplier *= 0.9
        yield_multiplier *= 0.85 + plot.soil_health * 0.25
    return _clamp(yield_multiplier, 0.1, 1.5), _clamp(quality_multiplier, 0.0, 1.25)


def quality_grade(score: float) -> str:
    if score >= 0.9:
        return "premium"
    if score >= 0.62:
        return "standard"
    if score >= 0.3:
        return "processing"
    return "rejected"


def compute_harvest_outcome(planted, crop: dict, watering_settings: dict, fertilizer_config: dict, rng, plot=None):
    """Compatibility API returning `(lost, yield)` for one mature plant."""
    loss_bonus = min(
        planted.neglect_days * watering_settings["neglect_loss_chance_penalty_per_day"],
        watering_settings["max_neglect_loss_chance_bonus"],
    )
    loss_chance = crop["loss_chance"] + loss_bonus
    if planted.fertilized:
        loss_chance -= fertilizer_config["loss_chance_reduction"]
    if rng.roll_loss(_clamp(loss_chance, 0.0, 0.95)):
        return True, 0

    base_yield = rng.roll_yield(crop["min_yield"], crop["max_yield"])
    yield_multiplier, _quality = harvest_multipliers(planted, crop, plot)
    if planted.fertilized:
        configured_bonus = fertilizer_config.get("yield_bonus_pct", 0.25)
        yield_multiplier += max(0.0, configured_bonus - 0.15)
    neglect_penalty = min(
        planted.neglect_days * watering_settings["neglect_yield_penalty_per_day"],
        watering_settings["max_neglect_yield_penalty"],
    )
    return False, max(0, round(base_yield * yield_multiplier * (1 - neglect_penalty)))
