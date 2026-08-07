"""Lookups derived from immutable config, computed once and reused.

The config objects (crops, upgrades, and every dict under `world`) are loaded
once and never mutated, but the day loop kept re-deriving the same structures
from them on every simulated day: rebuilding item/recipe/channel indexes,
re-reading the same `crop.get("ph_range", ...)` defaults per plot per day,
deep-copying the storage config. For a 45-day run that is ~45x more work than
needed, and a batch multiplies it by every run and every strategy.

Everything is keyed on the *identity* of the config object it came from, so
callers keep passing the same config they always did and get the cached result
back. Values derived from config alone are computed once per process; the two
that also depend on which upgrades are owned are keyed on that set too.

The lookups themselves sit on the hot path, so they are deliberately cheap:
config-derived state is bundled into a single WorldLookups fetched once per
simulated day, rather than a handful of separate cached calls, and per-crop
profiles are then plain dict hits off that bundle. A cache whose lookup costs
as much as the work it skips is not a cache.

Nothing here may depend on mutable run state beyond the explicit
`upgrades_owned` key -- a cache entry outlives the run that created it.
"""

from simulation.state import QUALITY_ORDER

# Field order of CropProfile.flat, which the optional C kernel indexes
# positionally. Bump on any change to that tuple; simulation/weather.py
# refuses to use a compiled kernel whose own constant disagrees, so a stale
# .so cannot silently read the wrong fields.
PROFILE_LAYOUT = 1

# Bounded so a caller that builds fresh config dicts in a loop (tests, mostly)
# can't grow these without limit. Real use has a handful of long-lived config
# objects, so the cap is never approached and a clear costs nothing.
_MAX_ENTRIES = 512

# id() is only a sound cache key while the object it came from is still alive:
# once freed, an unrelated object can be allocated at the same address and
# produce a bogus hit. Every cache entry therefore holds a strong reference to
# its key object(s) alongside the derived value, pinning them for the process
# lifetime. That is the intent -- these are config objects that live that long
# anyway.


# Per-nutrient demand assumed for a crop whose config omits `nutrient_demand`
# entirely -- the single authoritative definition. Every consumer that needs
# a crop's effective demand (CropProfile below, and simulation.economy_rules'
# soil-risk pricing and critical-soil triage via nutrient_demand_total) reads
# it from here, so an omitted field and this exact explicit value are always
# treated identically regardless of which code path looks at the crop.
DEFAULT_NUTRIENT_DEMAND = {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01}


# Plot dynamics that were previously hard-coded across weather.py,
# actions.py, and crop_growth.py. `config/soil.json`'s "dynamics" section
# overrides any of them; every default here is the exact constant the code
# used before, so a config that omits the section reproduces prior behaviour
# bit-for-bit. Depletion is now tunable from config for the same reason
# regeneration already was -- a balance pass should not have to edit
# simulation code to move either side of the same mechanic.
DEFAULT_SOIL_DYNAMICS = {
    # actions.harvest_mature: soil cost of taking a harvest off a plot.
    "harvest_soil_health_cost": 0.02,
    "min_soil_health": 0.1,
    # weather.apply_weather: fallow-only recovery, on top of regen_per_day.
    "fallow_pest_decay": 0.9,
    "fallow_disease_decay": 0.9,
    "fallow_soil_health_regen": 0.005,
    # weather.apply_weather: pressure accumulated while a crop is growing.
    "pest_growth_per_day": 0.005,
    "disease_growth_per_rainfall": 0.08,
    "max_pest_pressure": 0.8,
    "max_disease_pressure": 0.8,
    # crop_growth.harvest_multipliers: rotation incentive and the soil-health
    # yield curve (yield scales by floor + soil_health * span).
    "same_family_yield_penalty": 0.85,
    "same_family_quality_penalty": 0.9,
    "soil_health_yield_floor": 0.85,
    "soil_health_yield_span": 0.25,
}


class SoilDynamics:
    """Resolved `soil.dynamics` values, read once per world config."""

    __slots__ = tuple(DEFAULT_SOIL_DYNAMICS)

    def __init__(self, soil_config: dict | None = None):
        configured = (soil_config or {}).get("dynamics", {})
        for name, default in DEFAULT_SOIL_DYNAMICS.items():
            setattr(self, name, configured.get(name, default))


# Shared fallback for callers outside the engine's day loop (direct unit
# tests, forecasting helpers) that have no world config to hand. Never
# mutated -- SoilDynamics is written once at construction.
DEFAULT_DYNAMICS = SoilDynamics()


class CropProfile:
    """Static growth inputs for one crop, read straight off the config dict.

    Field-for-field the same values (including the same defaults) that
    crop_growth.update_crop_stress used to re-read from the crop dict for
    every plot on every day.
    """

    __slots__ = (
        "min_moisture",
        "nutrient_demand",
        "ph_low",
        "ph_high",
        "temperature_low",
        "temperature_high",
        "pest_susceptibility",
        "disease_susceptibility",
        "water_interval_days",
        "flat",
    )

    def __init__(self, crop: dict):
        self.water_interval_days = crop.get("water_interval_days", 3)
        self.min_moisture = crop.get("min_moisture", 0.35)
        needs = crop.get("nutrient_demand", DEFAULT_NUTRIENT_DEMAND)
        # Tuple, not dict: iterated twice per plot per day, and the order must
        # stay exactly as configured because the shortfall sum is float
        # addition (reordering it would perturb the last bits and break
        # seed-for-seed reproducibility against previously recorded runs).
        self.nutrient_demand = tuple(needs.items())
        ph_range = crop.get("ph_range", [5.8, 7.0])
        self.ph_low, self.ph_high = ph_range[0], ph_range[1]
        temperature_range = crop.get("temperature_range", [10, 30])
        self.temperature_low, self.temperature_high = temperature_range[0], temperature_range[1]
        self.pest_susceptibility = crop.get("pest_susceptibility", 1.0)
        self.disease_susceptibility = crop.get("disease_susceptibility", 1.0)
        # Same values again as a flat tuple, purely so the optional C kernel
        # (simulation._fastplot) can read them with PyTuple_GET_ITEM instead
        # of nine PyObject_GetAttr calls per plot per day. Field order is
        # part of the C module's ABI -- see _fastplotmodule.c, and bump
        # _fastplot.PROFILE_LAYOUT if it ever changes.
        self.flat = (
            self.min_moisture,
            self.ph_low,
            self.ph_high,
            self.temperature_low,
            self.temperature_high,
            self.pest_susceptibility,
            self.disease_susceptibility,
            self.water_interval_days,
            self.nutrient_demand,
        )


class ChannelProfile:
    """Static sale terms for one market channel, read off the config dict.

    Field-for-field the same values (including the same defaults) that
    markets.quote used to re-read from the channel dict on every call -- and
    it is called several times per item per day per agent, so those ~7 dict
    lookups were showing up in the profile. `min_quality_rank` additionally
    folds in the QUALITY_ORDER lookup, which is equally fixed.
    """

    __slots__ = (
        "channel_id",
        "min_quality_rank",
        "min_reputation",
        "daily_capacity",
        "price_multiplier",
        "reputation_bonus",
        "flat_fee",
        "fee_rate",
    )

    def __init__(self, channel: dict):
        self.channel_id = channel["id"]
        self.min_quality_rank = QUALITY_ORDER[channel.get("min_quality", "rejected")]
        self.min_reputation = channel.get("min_reputation", 0)
        # None, not a number: the un-cached default was the *caller's*
        # quantity, which is not known here. quote() substitutes it.
        self.daily_capacity = channel.get("daily_capacity")
        self.price_multiplier = channel.get("price_multiplier", 1.0)
        self.reputation_bonus = channel.get("reputation_bonus", 0.002)
        self.flat_fee = channel.get("flat_fee", 0.0)
        self.fee_rate = channel.get("fee_rate", 0.0)


_channel_profiles: dict = {}


def channel_profile(channel: dict) -> ChannelProfile:
    entry = _channel_profiles.get(id(channel))
    if entry is None:
        if len(_channel_profiles) >= _MAX_ENTRIES:
            _channel_profiles.clear()
        entry = (channel, ChannelProfile(channel))
        _channel_profiles[id(channel)] = entry
    return entry[1]


class WorldLookups:
    """Everything the day loop needs that is a pure function of the config.

    Bundled into one object so a simulated day pays a single cache lookup
    instead of one per derived structure.
    """

    __slots__ = (
        "items_by_id",
        "recipes",
        "recipes_by_id",
        "channels",
        "channels_by_id",
        "market_profiles",
        "crop_profiles",
        "crop_profiles_flat",
        "_storage",
        "_capacity",
        "watering",
        "fertilizer",
        "storage_config",
        "weather",
        "markets",
        "contracts",
        "buyers",
        "processing",
        "plot_regen",
        "soil_dynamics",
    )

    def __init__(self, world: dict, crops_by_id: dict):
        # The day loop reaches into `world` for these on every day of every
        # run; bind them once.
        self.watering = world["watering"]
        self.fertilizer = world["fertilizer"]
        self.storage_config = world["storage"]
        self.weather = world["weather"]
        self.markets = world["markets"]
        self.contracts = world["contracts"]
        self.buyers = world["buyers"]
        self.processing = world["processing"]
        # Passive per-day recovery for nitrogen/phosphorus/potassium plus
        # soil_health/pest_pressure/disease_pressure, independent of
        # fertilizer or fallowing -- see simulation.weather.apply_weather.
        # Defaults to no regen at all so a world config without a
        # "soil.regen_per_day" section behaves exactly as before this was
        # added.
        soil = world.get("soil", {})
        self.plot_regen = soil.get("regen_per_day", {})
        self.soil_dynamics = SoilDynamics(soil)
        products = world["processing"].get("products", [])
        self.items_by_id = dict(crops_by_id)
        self.items_by_id.update({product["id"]: product for product in products})
        self.recipes_by_id = {
            recipe["id"]: recipe for recipe in world["processing"].get("recipes", [])
        }
        # Materialized once; the engine hands this same list to agents every
        # day, which previously got a fresh list() built per day. Safe because
        # no agent mutates the list it is given.
        self.recipes = list(self.recipes_by_id.values())
        self.channels = world["markets"]["channels"]
        self.channels_by_id = {channel["id"]: channel for channel in self.channels}
        self.crop_profiles = {crop_id: CropProfile(crop) for crop_id, crop in crops_by_id.items()}
        # crop_id -> CropProfile.flat, so the optional C kernel resolves a
        # plot's crop to its inputs with a single dict hit and no attribute
        # access at all. Unused by the pure-Python path.
        self.crop_profiles_flat = {
            crop_id: profile.flat for crop_id, profile in self.crop_profiles.items()
        }
        self.market_profiles = _build_market_profiles(self.items_by_id, world["markets"])
        # Keyed by frozenset(upgrades_owned); at most one entry per distinct
        # combination of owned upgrades.
        self._storage = {}
        self._capacity = {}

    def effective_storage(self, base: dict, upgrades_owned: set, upgrades_by_id: dict) -> dict:
        """Storage config with owned storage upgrades folded in.

        Returns a fresh top-level dict per call (as the previous deepcopy did)
        so a caller mutating the result cannot corrupt the cache; only the
        derived values are shared, and no caller mutates those.
        """
        # `base` is part of the key, not just `upgrades_owned`: the derived
        # value depends on both, and keying on the upgrade set alone silently
        # returned another config's storage whenever a caller passed a
        # different base with the same upgrades owned. The entry keeps a
        # strong reference to `base` so its id() cannot be recycled.
        key = (id(base), frozenset(upgrades_owned))
        entry = self._storage.get(key)
        if entry is None:
            cached = dict(base)
            # sorted() only to keep the fold order stable across processes;
            # the shipped config has a single storage upgrade, so no ordering
            # of the multiplier below is observable either way.
            for upgrade_id in sorted(upgrades_owned):
                effect = upgrades_by_id[upgrade_id]["effect"]
                if effect["type"] == "storage":
                    cached["capacity"] = cached.get("capacity", 100) + effect.get(
                        "capacity_bonus", 0
                    )
                    cached["shelf_life_multiplier"] = cached.get(
                        "shelf_life_multiplier", 1
                    ) * effect.get("shelf_life_multiplier", 1)
            entry = (base, cached)
            self._storage[key] = entry
        return dict(entry[1])

    def processing_capacity(self, config: dict, upgrades_owned: set, upgrades_by_id: dict) -> int:
        # Keyed on `config` as well as the owned upgrades, for the same reason
        # effective_storage above is.
        key = (id(config), frozenset(upgrades_owned))
        entry = self._capacity.get(key)
        if entry is None:
            capacity = config.get("base_capacity", 0)
            # sorted() so the fold order cannot depend on set iteration order.
            # Configuration forces every processing_capacity amount to be an
            # integer, so the sum is exact and order is unobservable today --
            # this keeps that true if a fractional effect is ever introduced,
            # rather than letting it silently break replay.
            for upgrade_id in sorted(upgrades_owned):
                effect = upgrades_by_id[upgrade_id]["effect"]
                if effect["type"] == "processing_capacity":
                    capacity += effect["amount"]
            entry = (config, capacity)
            self._capacity[key] = entry
        return entry[1]


def _build_market_profiles(items_by_id: dict, market_config: dict) -> tuple:
    """Per-item price inputs, in the iteration order of items_by_id.

    Order is load-bearing: it fixes the sequence of rng.uniform draws in
    markets.update_daily_prices, so it must match items_by_id's iteration
    order exactly or every recorded seed replays differently.
    """
    default_variation = market_config.get("default_variation", 0.12)
    return tuple(
        (
            item_id,
            item.get("base_price", item.get("processed_base_price", 1.0)),
            item.get("price_variation", default_variation),
            item.get("seasonal_demand", {}),
        )
        for item_id, item in items_by_id.items()
    )


class WeatherParams:
    """Per-season weather inputs, resolved once instead of per simulated day."""

    __slots__ = ("season_length", "by_season")

    def __init__(self, config: dict):
        self.season_length = config.get("season_length_days", 15)
        seasons = config.get("seasons", {})
        self.by_season = {}
        for season in ("spring", "summer", "autumn", "winter"):
            values = seasons.get(season, {})
            temperature_range = values.get("temperature_range", [12, 24])
            rainfall_range = values.get("rainfall_range", [0.08, 0.25])
            self.by_season[season] = (
                temperature_range[0],
                temperature_range[1],
                values.get("rain_chance", 0.25),
                rainfall_range[0],
                rainfall_range[1],
                values.get("evaporation", 0.08),
            )


_weather_params: dict = {}


def weather_params(config: dict) -> WeatherParams:
    entry = _weather_params.get(id(config))
    if entry is None:
        if len(_weather_params) >= _MAX_ENTRIES:
            _weather_params.clear()
        entry = (config, WeatherParams(config))
        _weather_params[id(config)] = entry
    return entry[1]


_world_lookups: dict = {}


def world_lookups(world: dict, crops_by_id: dict) -> WorldLookups:
    key = (id(world), id(crops_by_id))
    entry = _world_lookups.get(key)
    if entry is None:
        if len(_world_lookups) >= _MAX_ENTRIES:
            _world_lookups.clear()
        entry = (world, crops_by_id, WorldLookups(world, crops_by_id))
        _world_lookups[key] = entry
    return entry[2]


_crop_profiles: dict = {}


def crop_profile(crop: dict) -> CropProfile:
    """Standalone profile lookup, for callers outside the engine's day loop.

    The engine reads profiles off WorldLookups.crop_profiles instead, which
    avoids this lookup entirely on the per-plot path.
    """
    entry = _crop_profiles.get(id(crop))
    if entry is None:
        if len(_crop_profiles) >= _MAX_ENTRIES:
            _crop_profiles.clear()
        entry = (crop, CropProfile(crop))
        _crop_profiles[id(crop)] = entry
    return entry[1]


def nutrient_demand_total(crop: dict) -> float:
    """Sum of a crop's normalized per-nutrient demand (nitrogen + phosphorus +
    potassium), via the same CropProfile normalization runtime nutrient
    consumption uses (crop_growth.py reads profile.nutrient_demand directly).
    A crop that omits `nutrient_demand` gets DEFAULT_NUTRIENT_DEMAND here --
    never zero -- so ranking by this always reflects effective runtime
    demand, not whether the config happened to spell the field out.
    """
    return sum(value for _, value in crop_profile(crop).nutrient_demand)


_market_profiles: dict = {}


def market_profiles(items_by_id: dict, market_config: dict) -> tuple:
    key = (id(items_by_id), id(market_config))
    entry = _market_profiles.get(key)
    if entry is None:
        if len(_market_profiles) >= _MAX_ENTRIES:
            _market_profiles.clear()
        entry = (items_by_id, market_config, _build_market_profiles(items_by_id, market_config))
        _market_profiles[key] = entry
    return entry[2]


_growth_days: dict = {}


def effective_growth_days(crop: dict, upgrades_owned: set, upgrades_by_id: dict) -> int:
    """Growth duration for `crop` given the currently owned upgrades.

    Hot enough to matter: agents call this while ranking every candidate crop,
    so it ran ~590k times in a 2200-run batch and re-walked the whole upgrade
    catalog each time. The result is a pure function of the crop, the owned
    set, and the catalog, so it is memoized on exactly those -- and the fold
    below (the only thing that computes a value) is unchanged, so every
    rounded intermediate is bit-identical to the un-cached version.
    """
    # Overwhelmingly the common case, especially early in a run: with nothing
    # owned the fold below is a no-op, so skip the key construction entirely.
    if not upgrades_owned:
        return crop["growth_days"]
    key = (id(crop), id(upgrades_by_id), frozenset(upgrades_owned))
    entry = _growth_days.get(key)
    if entry is None:
        if len(_growth_days) >= _MAX_ENTRIES:
            _growth_days.clear()
        days = crop["growth_days"]
        # The owned-upgrade set has no stable iteration order. Fold in the
        # order supplied by the configuration list so each rounded
        # intermediate result is reproducible across processes and Python runs.
        for upgrade_id, upgrade in upgrades_by_id.items():
            if upgrade_id not in upgrades_owned:
                continue
            effect = upgrade["effect"]
            if effect["type"] == "growth_time_reduction":
                days = max(1, round(days * (1 - effect["amount"])))
        # Strong refs to both key objects, so neither id() can be recycled
        # while the entry lives -- same discipline as the caches above.
        entry = (crop, upgrades_by_id, days)
        _growth_days[key] = entry
    return entry[2]


_cheapest_seed: dict = {}


def cheapest_seed_cost(crops: list) -> float:
    entry = _cheapest_seed.get(id(crops))
    if entry is None:
        if len(_cheapest_seed) >= _MAX_ENTRIES:
            _cheapest_seed.clear()
        entry = (crops, min(crop["seed_cost"] for crop in crops))
        _cheapest_seed[id(crops)] = entry
    return entry[1]
