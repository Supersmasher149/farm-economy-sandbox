"""Pure crop stress and harvest calculations."""

from simulation.derived import DEFAULT_DYNAMICS, crop_profile

# Bounds on the multipliers harvest_multipliers produces. Named so that
# compute_harvest_outcome can re-apply the yield bound after adding the
# configured fertilizer bonus, rather than letting that one input escape a
# cap the design doc says every input is subject to.
YIELD_MULTIPLIER_BOUNDS = (0.1, 1.5)
QUALITY_MULTIPLIER_BOUNDS = (0.0, 1.25)

# Fertilizer's quality benefit when the fertilizer config does not specify
# one. Kept equal to the value this was hard-coded to.
DEFAULT_FERTILIZER_QUALITY_BONUS = 0.05


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def update_crop_stress(planted, plot, crop: dict, weather: dict, profile=None) -> None:
    """Accumulate bounded stress for one growing crop after today's weather.

    `profile` is the crop's cached static inputs (see simulation.derived). The
    engine passes the one it already has; callers that omit it get an
    equivalent lookup, so the behaviour is identical either way.
    """
    # Runs once per growing plot per day -- the hottest path in the sim, hence
    # the hoisted profile instead of re-reading the crop dict's defaults on
    # every call, and the inlined _clamp below.
    if profile is None:
        profile = crop_profile(crop)
    planted.water_stress += max(0.0, profile.min_moisture - plot.moisture)

    needs = profile.nutrient_demand
    # Must stay a sum() over the same terms in the same order: since 3.12
    # sum() applies compensated (Neumaier) summation to floats, so replacing
    # it with a plain += loop shifts the last bits and silently breaks
    # seed-for-seed replay of previously recorded runs.
    nutrient_shortfall = sum(max(0.0, amount - getattr(plot, name)) for name, amount in needs)
    ph = plot.ph
    ph_stress = 0.0
    if ph < profile.ph_low:
        ph_stress = (profile.ph_low - ph) * 0.1
    elif ph > profile.ph_high:
        ph_stress = (ph - profile.ph_high) * 0.1
    planted.nutrient_stress += nutrient_shortfall + ph_stress

    temperature = weather.get("temperature", 20)
    if temperature < profile.temperature_low:
        planted.temperature_stress += (profile.temperature_low - temperature) / 20
    elif temperature > profile.temperature_high:
        planted.temperature_stress += (temperature - profile.temperature_high) / 20

    planted.pest_stress += plot.pest_pressure * profile.pest_susceptibility
    planted.disease_stress += plot.disease_pressure * profile.disease_susceptibility

    # _clamp inlined as its literal max/min body -- it saves a Python-level
    # call per plot per nutrient per day, and keeping the exact max/min form
    # preserves the +0.0-vs--0.0 result that a comparison chain would not.
    evaporation = weather.get("evaporation", 0.08)
    plot.moisture = max(0.0, min(1.0, plot.moisture - evaporation))
    for nutrient, amount in needs:
        setattr(plot, nutrient, max(0.0, min(1.0, getattr(plot, nutrient) - amount)))


def harvest_multipliers(
    planted, crop: dict, plot=None, fertilizer_config=None, dynamics=None
) -> tuple[float, float]:
    """Return yield and quality multipliers from accumulated crop conditions.

    `fertilizer_config` supplies the fertilized-quality bonus and `dynamics`
    the rotation/soil-health tuning; both fall back to the shipped defaults,
    so omitting them reproduces the previous hard-coded behaviour exactly.
    """
    dynamics = dynamics if dynamics is not None else DEFAULT_DYNAMICS
    environmental_stress = (
        planted.water_stress * 0.16
        + planted.nutrient_stress * 0.18
        + planted.temperature_stress * 0.12
        + planted.pest_stress * 0.10
        + planted.disease_stress * 0.12
    )
    # Neglect has historically affected quality through stress, while its
    # yield loss is configured in watering_settings and applied by
    # compute_harvest_outcome. Keep those effects separate so the configured
    # yield penalty is not compounded with this quality signal.
    quality_stress = environmental_stress + planted.neglect_days * 0.08
    yield_multiplier = _clamp(1.0 - environmental_stress, 0.15, 1.35)
    quality_multiplier = _clamp(1.0 - quality_stress * 1.25, 0.0, 1.2)

    if planted.fertilized:
        quality_multiplier += (fertilizer_config or {}).get(
            "quality_bonus", DEFAULT_FERTILIZER_QUALITY_BONUS
        )
    if plot is not None:
        family = crop.get("family")
        if family and plot.previous_crop_family == family:
            yield_multiplier *= dynamics.same_family_yield_penalty
            quality_multiplier *= dynamics.same_family_quality_penalty
        yield_multiplier *= (
            dynamics.soil_health_yield_floor + plot.soil_health * dynamics.soil_health_yield_span
        )
    return (
        _clamp(yield_multiplier, *YIELD_MULTIPLIER_BOUNDS),
        _clamp(quality_multiplier, *QUALITY_MULTIPLIER_BOUNDS),
    )


def quality_grade(score: float) -> str:
    if score >= 0.9:
        return "premium"
    if score >= 0.62:
        return "standard"
    if score >= 0.3:
        return "processing"
    return "rejected"


def compute_harvest_outcome(
    planted,
    crop: dict,
    watering_settings: dict,
    fertilizer_config: dict,
    rng,
    plot=None,
    dynamics=None,
):
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
    yield_multiplier, _quality = harvest_multipliers(
        planted, crop, plot, fertilizer_config, dynamics
    )
    if planted.fertilized:
        configured_bonus = fertilizer_config.get("yield_bonus_pct", 0.25)
        # Re-bounded after the bonus: harvest_multipliers' own clamp happens
        # before this is added, so without this a fertilized harvest was the
        # one input that could exceed the cap the design doc states applies
        # to every factor ("capped so no single factor creates unbounded
        # outcomes"). The bonus still has full effect below the cap.
        yield_multiplier = _clamp(yield_multiplier + configured_bonus, *YIELD_MULTIPLIER_BOUNDS)
    neglect_penalty = min(
        planted.neglect_days * watering_settings["neglect_yield_penalty_per_day"],
        watering_settings["max_neglect_yield_penalty"],
    )
    return False, max(0, round(base_yield * yield_multiplier * (1 - neglect_penalty)))
