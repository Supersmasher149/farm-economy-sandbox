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


class CropProfile:
    """Static growth inputs for one crop, read straight off the config dict.

    Field-for-field the same values (including the same defaults) that
    crop_growth.update_crop_stress used to re-read from the crop dict for
    every plot on every day.
    """

    __slots__ = (
        "min_moisture", "nutrient_demand", "ph_low", "ph_high",
        "temperature_low", "temperature_high",
        "pest_susceptibility", "disease_susceptibility",
    )

    def __init__(self, crop: dict):
        self.min_moisture = crop.get("min_moisture", 0.35)
        needs = crop.get("nutrient_demand", {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01})
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


class WorldLookups:
    """Everything the day loop needs that is a pure function of the config.

    Bundled into one object so a simulated day pays a single cache lookup
    instead of one per derived structure.
    """

    __slots__ = (
        "items_by_id", "recipes", "recipes_by_id", "channels", "channels_by_id",
        "market_profiles", "crop_profiles", "_storage", "_capacity",
        "watering", "fertilizer", "storage_config", "weather", "markets",
        "contracts", "buyers", "processing",
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
        products = world["processing"].get("products", [])
        self.items_by_id = dict(crops_by_id)
        self.items_by_id.update({product["id"]: product for product in products})
        self.recipes_by_id = {recipe["id"]: recipe for recipe in world["processing"].get("recipes", [])}
        # Materialized once; the engine hands this same list to agents every
        # day, which previously got a fresh list() built per day. Safe because
        # no agent mutates the list it is given.
        self.recipes = list(self.recipes_by_id.values())
        self.channels = world["markets"]["channels"]
        self.channels_by_id = {channel["id"]: channel for channel in self.channels}
        self.crop_profiles = {crop_id: CropProfile(crop) for crop_id, crop in crops_by_id.items()}
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
        key = frozenset(upgrades_owned)
        cached = self._storage.get(key)
        if cached is None:
            cached = dict(base)
            # sorted() only to keep the fold order stable across processes;
            # the shipped config has a single storage upgrade, so no ordering
            # of the multiplier below is observable either way.
            for upgrade_id in sorted(upgrades_owned):
                effect = upgrades_by_id[upgrade_id]["effect"]
                if effect["type"] == "storage":
                    cached["capacity"] = cached.get("capacity", 100) + effect.get("capacity_bonus", 0)
                    cached["shelf_life_multiplier"] = (
                        cached.get("shelf_life_multiplier", 1) * effect.get("shelf_life_multiplier", 1)
                    )
            self._storage[key] = cached
        return dict(cached)

    def processing_capacity(self, config: dict, upgrades_owned: set, upgrades_by_id: dict) -> int:
        key = frozenset(upgrades_owned)
        capacity = self._capacity.get(key)
        if capacity is None:
            capacity = config.get("base_capacity", 0)
            for upgrade_id in upgrades_owned:
                effect = upgrades_by_id[upgrade_id]["effect"]
                if effect["type"] == "processing_capacity":
                    capacity += effect["amount"]
            self._capacity[key] = capacity
        return capacity


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
                temperature_range[0], temperature_range[1],
                values.get("rain_chance", 0.25),
                rainfall_range[0], rainfall_range[1],
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


_cheapest_seed: dict = {}


def cheapest_seed_cost(crops: list) -> float:
    entry = _cheapest_seed.get(id(crops))
    if entry is None:
        if len(_cheapest_seed) >= _MAX_ENTRIES:
            _cheapest_seed.clear()
        entry = (crops, min(crop["seed_cost"] for crop in crops))
        _cheapest_seed[id(crops)] = entry
    return entry[1]
