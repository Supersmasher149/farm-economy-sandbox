"""BatchState: the Structure-of-Arrays data contract (component A).

One flat array per field, shape (B,) for run-level fields and (B, P) for
plot-level fields, B = runs in this chunk, P = plots per run. No per-run
Python object, no daily history -- a chunk's total footprint is
`bytes_per_run(P) * B` regardless of num_days, which is what lets
`run_millions` size chunks off a memory budget instead of a run count.

Phase 1 ("crop/soil physics parity") grew this from the original 8-field
toy layout to the fields below, mirroring `simulation/state.py`'s
`PlotState`/`PlantedCrop` field-for-field (multi-nutrient soil, multi-term
stress accumulators, neglect tracking, fertilizer state, family rotation) --
see `config_arrays.py` and `kernel.py` for how those fields are used, and
vectorized/README.md for what's still simplified relative to the real
engine (no markets, no contracts, no processing, no upgrades).

Phase 2 ("storage & spoilage") added a fixed-size per-run lot-slot
dimension `(B, L)`, `L = num_plots * config.lots_per_plot`, mirroring
`simulation/state.py`'s `InventoryLot` list with a bounded array instead of
a dynamic list (see `config_arrays.py`'s `lots_per_plot` docstring for why
that bound is provably safe). This is *shadow accounting*: harvests still
credit `money` instantly (Phase 3/markets hasn't landed), lots exist purely
to age/spoil/capacity-trim and produce `total_spoiled`/`total_storage_cost`
-- informational stats that don't feed back into `money` yet. See
`kernel.py`'s docstring for the full rationale.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from vectorized.config_arrays import VectorConfig


@dataclass
class BatchState:
    # -- run-level --
    money: np.ndarray  # float32 (B,)
    total_harvest: np.ndarray  # float32 (B,) -- units actually sold (non-rejected grade)
    total_revenue: np.ndarray  # float32 (B,) -- cumulative gross sales, gates crop unlocks
    strategy_id: np.ndarray  # int8 (B,)
    total_spoiled: np.ndarray  # float32 (B,) -- cumulative units lost to age-out + capacity trim
    # float32 (B,) -- cumulative storage liability charged (shadow: not subtracted from money)
    total_storage_cost: np.ndarray

    # -- plot-level: soil --
    moisture: np.ndarray  # float32 (B, P)
    nitrogen: np.ndarray  # float32 (B, P)
    phosphorus: np.ndarray  # float32 (B, P)
    potassium: np.ndarray  # float32 (B, P)
    ph: np.ndarray  # float32 (B, P) -- never mutated post-init, no mechanic changes it
    soil_health: np.ndarray  # float32 (B, P)
    pest_pressure: np.ndarray  # float32 (B, P)
    disease_pressure: np.ndarray  # float32 (B, P)

    # -- plot-level: what's planted --
    crop_type: np.ndarray  # int8 (B, P), -1 == empty
    growth_stage: np.ndarray  # int8 (B, P)
    days_to_harvest: np.ndarray  # int16 (B, P)
    previous_crop_family: np.ndarray  # int8 (B, P), -1 == none yet (rotation penalty)
    fertilized: np.ndarray  # int8 (B, P), 0/1

    # -- plot-level: accumulated stress (reset at each planting) --
    water_stress: np.ndarray  # float32 (B, P)
    nutrient_stress: np.ndarray  # float32 (B, P)
    temperature_stress: np.ndarray  # float32 (B, P)
    pest_stress: np.ndarray  # float32 (B, P)
    disease_stress: np.ndarray  # float32 (B, P)

    # -- plot-level: watering/neglect --
    neglect_days: np.ndarray  # int32 (B, P)
    last_watered_day: np.ndarray  # int32 (B, P)

    # -- rng streams (not part of the prompt's field list; see rng.py) --
    rng_run_state: np.ndarray  # uint64 (B,)
    rng_plot_state: np.ndarray  # uint64 (B, P)

    # -- lot-level: storage (Phase 2), shape (B, L), L = num_plots * lots_per_plot --
    lot_item_id: np.ndarray  # int8 (B, L), -1 == empty slot (same convention as crop_type)
    lot_quantity: np.ndarray  # int32 (B, L)
    # int8 (B, L) -- QUALITY_ORDER: 1=processing, 2=standard, 3=premium (0/"rejected"
    # never appears -- a rejected harvest never becomes a lot)
    lot_quality: np.ndarray
    lot_age_days: np.ndarray  # int16 (B, L)

    @property
    def num_runs(self) -> int:
        return self.money.shape[0]

    @property
    def num_plots(self) -> int:
        return self.moisture.shape[1]

    @property
    def num_lot_slots(self) -> int:
        return self.lot_item_id.shape[1]


def bytes_per_run(num_plots: int, num_lot_slots: int) -> int:
    """Bytes/run this layout costs, for `run_millions`' memory-budget chunking.

    Closed-form sum kept in sync with the dataclass fields above by hand --
    exact because it's small and reviewed alongside the fields, not because
    it's introspected. `num_lot_slots` is normally `num_plots *
    config.lots_per_plot` (see `config_arrays.py`), passed explicitly here
    rather than derived so this module doesn't need a `VectorConfig` import.
    """
    # money, total_harvest, total_revenue, strategy_id, rng_run, total_spoiled, total_storage_cost
    per_run = 4 + 4 + 4 + 1 + 8 + 4 + 4
    per_plot = (
        4 * 8  # moisture, nitrogen, phosphorus, potassium, ph, soil_health, pest, disease (f4)
        + 1 * 4  # crop_type, growth_stage, previous_crop_family, fertilized (i1)
        + 2  # days_to_harvest (i2)
        + 4 * 5  # water/nutrient/temperature/pest/disease_stress (f4)
        + 4 * 2  # neglect_days, last_watered_day (i4)
        + 8  # rng_plot_state (u8)
    )
    per_lot_slot = (
        1 + 4 + 1 + 2
    )  # lot_item_id(i1) + lot_quantity(i4) + lot_quality(i1) + lot_age_days(i2)
    return per_run + num_plots * per_plot + num_lot_slots * per_lot_slot


def allocate(num_runs: int, num_plots: int, num_lot_slots: int) -> BatchState:
    """Allocate an uninitialized chunk. Call `init_runs` before simulating."""
    f4 = lambda: np.empty((num_runs, num_plots), dtype=np.float32)  # noqa: E731
    i1 = lambda: np.empty((num_runs, num_plots), dtype=np.int8)  # noqa: E731
    i4 = lambda: np.empty((num_runs, num_plots), dtype=np.int32)  # noqa: E731
    return BatchState(
        money=np.empty(num_runs, dtype=np.float32),
        total_harvest=np.empty(num_runs, dtype=np.float32),
        total_revenue=np.empty(num_runs, dtype=np.float32),
        strategy_id=np.empty(num_runs, dtype=np.int8),
        total_spoiled=np.empty(num_runs, dtype=np.float32),
        total_storage_cost=np.empty(num_runs, dtype=np.float32),
        moisture=f4(),
        nitrogen=f4(),
        phosphorus=f4(),
        potassium=f4(),
        ph=f4(),
        soil_health=f4(),
        pest_pressure=f4(),
        disease_pressure=f4(),
        crop_type=i1(),
        growth_stage=i1(),
        days_to_harvest=np.empty((num_runs, num_plots), dtype=np.int16),
        previous_crop_family=i1(),
        fertilized=i1(),
        water_stress=f4(),
        nutrient_stress=f4(),
        temperature_stress=f4(),
        pest_stress=f4(),
        disease_stress=f4(),
        neglect_days=i4(),
        last_watered_day=i4(),
        rng_run_state=np.empty(num_runs, dtype=np.uint64),
        rng_plot_state=np.empty((num_runs, num_plots), dtype=np.uint64),
        lot_item_id=np.empty((num_runs, num_lot_slots), dtype=np.int8),
        lot_quantity=np.empty((num_runs, num_lot_slots), dtype=np.int32),
        lot_quality=np.empty((num_runs, num_lot_slots), dtype=np.int8),
        lot_age_days=np.empty((num_runs, num_lot_slots), dtype=np.int16),
    )


def init_runs(
    state: BatchState,
    config: VectorConfig,
    master_seed: int,
    run_index_offset: int,
    strategy_of_run: np.ndarray,
) -> None:
    """Initialize a freshly-allocated chunk in place.

    `run_index_offset` is the global run index of row 0 in this chunk -- the
    thing that makes seeding independent of chunk size and chunk position
    (see vectorized/rng.py's module docstring and
    scripts/vectorized_validate.py, which checks exactly this).
    `strategy_of_run` assigns each row a strategy id (component C);
    `run_millions` round-robins the roster across a chunk.
    """
    from vectorized import rng as _rng

    num_runs, num_plots = state.num_runs, state.num_plots
    global_index = run_index_offset + np.arange(num_runs, dtype=np.int64)

    # Per-run weather stream seed: one splitmix64 mix of (master_seed + k),
    # vectorized. Must match vectorized.rng.run_seed's scalar arithmetic
    # exactly, element by element -- see vectorized/rng.py and
    # scripts/vectorized_validate.py.
    x = (np.uint64(master_seed) + global_index.astype(np.uint64)) & np.uint64(_rng.MASK64)
    z = (x + np.uint64(_rng.GOLDEN_GAMMA)) & np.uint64(_rng.MASK64)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(_rng.MIX_MULT_1)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(_rng.MIX_MULT_2)
    run_state = z ^ (z >> np.uint64(31))
    state.rng_run_state[:] = run_state

    plot_index = np.arange(num_plots, dtype=np.uint64)
    salted = run_state[:, None] ^ (plot_index[None, :] * np.uint64(_rng.PLOT_SALT))
    salted &= np.uint64(_rng.MASK64)
    pz = (salted + np.uint64(_rng.GOLDEN_GAMMA)) & np.uint64(_rng.MASK64)
    pz = (pz ^ (pz >> np.uint64(30))) * np.uint64(_rng.MIX_MULT_1)
    pz = (pz ^ (pz >> np.uint64(27))) * np.uint64(_rng.MIX_MULT_2)
    state.rng_plot_state[:, :] = pz ^ (pz >> np.uint64(31))

    state.money[:] = config.start_money
    state.total_harvest[:] = 0.0
    state.total_revenue[:] = 0.0
    state.strategy_id[:] = strategy_of_run
    state.total_spoiled[:] = 0.0
    state.total_storage_cost[:] = 0.0

    state.moisture[:, :] = config.initial_moisture
    state.nitrogen[:, :] = config.initial_nitrogen
    state.phosphorus[:, :] = config.initial_phosphorus
    state.potassium[:, :] = config.initial_potassium
    state.ph[:, :] = config.initial_ph
    state.soil_health[:, :] = config.initial_soil_health
    state.pest_pressure[:, :] = config.initial_pest_pressure
    state.disease_pressure[:, :] = config.initial_disease_pressure

    state.crop_type[:, :] = -1
    state.growth_stage[:, :] = 0
    state.days_to_harvest[:, :] = 0
    state.previous_crop_family[:, :] = -1
    state.fertilized[:, :] = 0

    state.water_stress[:, :] = 0.0
    state.nutrient_stress[:, :] = 0.0
    state.temperature_stress[:, :] = 0.0
    state.pest_stress[:, :] = 0.0
    state.disease_stress[:, :] = 0.0

    state.neglect_days[:, :] = 0
    state.last_watered_day[:, :] = 0

    state.lot_item_id[:, :] = -1
    state.lot_quantity[:, :] = 0
    state.lot_quality[:, :] = 0
    state.lot_age_days[:, :] = 0
