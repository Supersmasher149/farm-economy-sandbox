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
engine (no contracts, no processing, no upgrades).

Phase 2 ("storage & spoilage") added a fixed-size per-run lot-slot
dimension `(B, L)`, `L = num_plots * config.lots_per_plot`, mirroring
`simulation/state.py`'s `InventoryLot` list with a bounded array instead of
a dynamic list (see `config_arrays.py`'s `lots_per_plot` docstring for why
that bound is provably safe).

Phase 3 ("markets", single-channel scope) made those lots real: harvest no
longer credits `money` at all, only `kernel.py`'s daily "sell all matured
lots" step does, at a per-crop price that's rolled once per run per day
(single-channel supply/demand, not persisted in `BatchState` -- it's
per-run scratch, reset fresh each run, see `kernel.py`'s docstring). No new
`BatchState` fields were needed for this: Phase 2's lot-slot dimension and
`total_spoiled`/`total_storage_cost`/`total_harvest`/`total_revenue` fields
already covered everything Phase 3 needed to report.

Phase 4 ("processing") added a fixed-size per-run job-slot dimension
`(B, J)`, `J = config.base_capacity` -- the real engine's own processing
capacity, not a per-plot bound the way lot slots are, since a processing
job is a global (not per-plot) resource, mirroring
`simulation/state.py`'s `ProcessingJob` list the same bounded-array way
Phase 2 bounded `InventoryLot`. A completed job's output becomes an
ordinary lot in the *existing* lot-slot array (see `config_arrays.py`'s
item-space docstring for why crops and processed products share one
array) -- callers now size `num_lot_slots` as `num_plots *
config.lots_per_plot + config.base_capacity`, reserving one extra lot
slot per concurrent job so a completing job's output always has
somewhere to go (at most `base_capacity` jobs can be in flight, so at
most that many product lots can be pending at once).

Phase 5 ("contracts", simplified scope) added a `(B,)` `reputation` field
(matches `simulation/state.py`'s `player.reputation`, starts at 0.0) and a
fixed-size **per-buyer** dimension `(B, K)`, `K = config.num_buyers` --
not a bounded approximation of a dynamic list the way lots/jobs are, but
an exact one-to-one mapping: this phase gives each buyer exactly one
contract "slot" (empty / offered / active) at a time instead of the real
engine's unbounded concurrent offers-and-active-contracts per buyer, so
`K = num_buyers` is exact, not a derived bound. See kernel.py's module
docstring for the accept/deliver/resolve policy this enables and what it
simplifies away.

Phase 7 ("upgrades") added a fixed-size **per-upgrade** dimension `(B, U)`,
`U = config.num_upgrades` -- `upgrade_owned`, an exact catalog-bound the
same way contract slots are, not a derived bound. It also added two new
`(B,)` fields, `active_plots` and `active_job_slots`: two of
`config/upgrades.json`'s four effect types (`capacity`, `processing_capacity`)
grow a dimension every earlier phase sized once per batch and shared by
every run (plots, processing job slots). Rather than a per-run variable-
shape array -- not expressible in a dense SoA layout -- `moisture` and
friends' `P` axis and `job_output_item_id`'s `J` axis are allocated at
their *maximum* possible width (`num_plots + config.total_capacity_bonus`,
`config.base_capacity + config.total_processing_capacity_bonus`) for every
run, and `active_plots`/`active_job_slots` track how much of that width a
given run has actually unlocked so far (`kernel.py` skips plot/job-slot
indices at or past the active count entirely, same as if they didn't exist
yet -- matching `simulation/state.py:add_slots`, where an unbought
capacity upgrade means the plot literally isn't in `player.plots`). Every
run starts with `active_plots = num_plots` (the base/starting count, a
caller-supplied `init_runs` parameter, not derived from `state.num_plots`
since that property is now the *max* width) and
`active_job_slots = config.base_capacity`, and only grows when that run's
agent buys the corresponding upgrade.
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
    # float32 (B,) -- cumulative processed-product units completed (Phase 4),
    # matches simulation/state.py's player.total_processed
    total_processed: np.ndarray
    # float32 (B,) -- matches simulation/state.py's player.reputation, starts at 0.0
    reputation: np.ndarray
    total_contracts_completed: np.ndarray  # float32 (B,)
    total_contracts_failed: np.ndarray  # float32 (B,)
    # float32 (B,) -- cumulative deadline-failure penalties paid, real (unlike
    # storage liability): deducted from money, not shadow accounting
    total_contract_penalties: np.ndarray
    # int16 (B,) -- Phase 7: how many of the P (max-width) plot columns this
    # run has unlocked so far; starts at the base/starting plot count, grows
    # by a capacity upgrade's amount when bought. Plot indices >= this value
    # are skipped entirely, same as if they didn't exist yet.
    active_plots: np.ndarray
    # int16 (B,) -- Phase 7: same idea for job_output_* 's J axis; starts at
    # config.base_capacity, grows by a processing_capacity upgrade's amount.
    active_job_slots: np.ndarray

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

    # -- job-level: processing (Phase 4), shape (B, J), J = config.base_capacity --
    job_output_item_id: np.ndarray  # int8 (B, J), -1 == empty slot
    job_output_quantity: np.ndarray  # int32 (B, J)
    job_completion_day: np.ndarray  # int32 (B, J) -- absolute day the job's output is ready

    # -- buyer-level: contracts (Phase 5), shape (B, K), K = config.num_buyers,
    # one contract "slot" per buyer -- see this module's docstring --
    contract_state: np.ndarray  # int8 (B, K), 0=empty 1=offered 2=active
    contract_item_idx: np.ndarray  # int8 (B, K), item-space index, -1 if empty
    contract_remaining: np.ndarray  # int32 (B, K), quantity not yet delivered
    contract_unit_price: np.ndarray  # float32 (B, K)
    contract_min_quality_rank: np.ndarray  # int8 (B, K), QUALITY_ORDER rank
    contract_deadline_day: np.ndarray  # int32 (B, K), absolute day
    contract_expiry_day: np.ndarray  # int32 (B, K), absolute day, meaningful while state==1
    contract_penalty_rate: np.ndarray  # float32 (B, K)
    # float32 (B, K) -- per-buyer standing, persists across that buyer's
    # contracts (NOT reset on resolve, unlike the fields above)
    buyer_relationship: np.ndarray

    # -- upgrade-level (Phase 7), shape (B, U), U = config.num_upgrades,
    # one entry per catalog upgrade -- exact bound, same as contract slots --
    upgrade_owned: np.ndarray  # int8 (B, U), 0/1

    @property
    def num_runs(self) -> int:
        return self.money.shape[0]

    @property
    def num_plots(self) -> int:
        return self.moisture.shape[1]

    @property
    def num_lot_slots(self) -> int:
        return self.lot_item_id.shape[1]

    @property
    def num_job_slots(self) -> int:
        return self.job_output_item_id.shape[1]

    @property
    def num_buyers(self) -> int:
        return self.contract_state.shape[1]

    @property
    def num_upgrades(self) -> int:
        return self.upgrade_owned.shape[1]


def bytes_per_run(
    num_plots: int, num_lot_slots: int, num_job_slots: int, num_buyers: int, num_upgrades: int
) -> int:
    """Bytes/run this layout costs, for `run_millions`' memory-budget chunking.

    Closed-form sum kept in sync with the dataclass fields above by hand --
    exact because it's small and reviewed alongside the fields, not because
    it's introspected. `num_plots`/`num_job_slots` are the *max* width
    (base + total_capacity_bonus / total_processing_capacity_bonus, Phase 7 --
    see this module's docstring), `num_lot_slots` is normally `num_plots *
    config.lots_per_plot + num_job_slots`, and `num_buyers`/`num_upgrades`
    are `config.num_buyers`/`config.num_upgrades` (see `config_arrays.py`),
    passed explicitly here rather than derived so this module doesn't need a
    `VectorConfig` import.
    """
    # money, total_harvest, total_revenue, strategy_id, rng_run, total_spoiled,
    # total_storage_cost, total_processed, reputation, total_contracts_completed,
    # total_contracts_failed, total_contract_penalties, active_plots(i2),
    # active_job_slots(i2)
    per_run = 4 + 4 + 4 + 1 + 8 + 4 + 4 + 4 + 4 + 4 + 4 + 4 + 2 + 2
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
    per_job_slot = (
        1 + 4 + 4
    )  # job_output_item_id(i1) + job_output_quantity(i4) + completion_day(i4)
    per_buyer = (
        1 + 1 + 4 + 4 + 1 + 4 + 4 + 4 + 4
    )  # contract_state(i1) + item_idx(i1) + remaining(i4) + unit_price(f4) +
    # min_quality_rank(i1) + deadline_day(i4) + expiry_day(i4) + penalty_rate(f4) +
    # buyer_relationship(f4)
    per_upgrade = 1  # upgrade_owned(i1)
    return (
        per_run
        + num_plots * per_plot
        + num_lot_slots * per_lot_slot
        + num_job_slots * per_job_slot
        + num_buyers * per_buyer
        + num_upgrades * per_upgrade
    )


def allocate(
    num_runs: int,
    num_plots: int,
    num_lot_slots: int,
    num_job_slots: int,
    num_buyers: int,
    num_upgrades: int,
) -> BatchState:
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
        total_processed=np.empty(num_runs, dtype=np.float32),
        reputation=np.empty(num_runs, dtype=np.float32),
        total_contracts_completed=np.empty(num_runs, dtype=np.float32),
        total_contracts_failed=np.empty(num_runs, dtype=np.float32),
        total_contract_penalties=np.empty(num_runs, dtype=np.float32),
        active_plots=np.empty(num_runs, dtype=np.int16),
        active_job_slots=np.empty(num_runs, dtype=np.int16),
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
        job_output_item_id=np.empty((num_runs, num_job_slots), dtype=np.int8),
        job_output_quantity=np.empty((num_runs, num_job_slots), dtype=np.int32),
        job_completion_day=np.empty((num_runs, num_job_slots), dtype=np.int32),
        contract_state=np.empty((num_runs, num_buyers), dtype=np.int8),
        contract_item_idx=np.empty((num_runs, num_buyers), dtype=np.int8),
        contract_remaining=np.empty((num_runs, num_buyers), dtype=np.int32),
        contract_unit_price=np.empty((num_runs, num_buyers), dtype=np.float32),
        contract_min_quality_rank=np.empty((num_runs, num_buyers), dtype=np.int8),
        contract_deadline_day=np.empty((num_runs, num_buyers), dtype=np.int32),
        contract_expiry_day=np.empty((num_runs, num_buyers), dtype=np.int32),
        contract_penalty_rate=np.empty((num_runs, num_buyers), dtype=np.float32),
        buyer_relationship=np.empty((num_runs, num_buyers), dtype=np.float32),
        upgrade_owned=np.empty((num_runs, num_upgrades), dtype=np.int8),
    )


def init_runs(
    state: BatchState,
    config: VectorConfig,
    master_seed: int,
    run_index_offset: int,
    strategy_of_run: np.ndarray,
    num_plots_base: int,
) -> None:
    """Initialize a freshly-allocated chunk in place.

    `run_index_offset` is the global run index of row 0 in this chunk -- the
    thing that makes seeding independent of chunk size and chunk position
    (see vectorized/rng.py's module docstring and
    scripts/vectorized_validate.py, which checks exactly this).
    `strategy_of_run` assigns each row a strategy id (component C);
    `run_millions` round-robins the roster across a chunk. `num_plots_base`
    is the starting (pre-upgrade) plot count -- `state.num_plots` is now the
    *max* width (Phase 7, see this module's docstring), so the starting
    count can't be derived from the array shape and has to be passed
    explicitly; `active_job_slots` doesn't need an equivalent parameter
    since its base (`config.base_capacity`) is already in `config`.
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
    state.total_processed[:] = 0.0
    state.total_storage_cost[:] = 0.0
    state.reputation[:] = 0.0
    state.total_contracts_completed[:] = 0.0
    state.total_contracts_failed[:] = 0.0
    state.total_contract_penalties[:] = 0.0
    state.active_plots[:] = num_plots_base
    state.active_job_slots[:] = config.base_capacity

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

    state.job_output_item_id[:, :] = -1
    state.job_output_quantity[:, :] = 0
    state.job_completion_day[:, :] = 0

    state.contract_state[:, :] = 0
    state.contract_item_idx[:, :] = -1
    state.contract_remaining[:, :] = 0
    state.contract_unit_price[:, :] = 0.0
    state.contract_min_quality_rank[:, :] = 0
    state.contract_deadline_day[:, :] = 0
    state.contract_expiry_day[:, :] = 0
    state.contract_penalty_rate[:, :] = 0.0
    state.buyer_relationship[:, :] = 0.0

    state.upgrade_owned[:, :] = 0
