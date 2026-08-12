import hashlib
from dataclasses import dataclass, field

QUALITY_ORDER = {"rejected": 0, "processing": 1, "standard": 2, "premium": 3}


@dataclass(slots=True)
class PlantedCrop:
    crop_id: str
    day_planted: int
    growth_days_required: int
    last_watered_day: int | None = None
    neglect_days: int = 0
    fertilized: bool = False
    plot_index: int | None = None
    water_stress: float = 0.0
    nutrient_stress: float = 0.0
    temperature_stress: float = 0.0
    pest_stress: float = 0.0
    disease_stress: float = 0.0
    # Cash actually spent on this planting (seed, plus any fertilizer and
    # watering it received). Carried into the harvested lot's unit_cost so
    # processing margin is priced against real production cost rather than
    # seed cost alone. Appended last to keep positional construction working.
    accrued_cost: float = 0.0

    def __post_init__(self):
        if self.last_watered_day is None:
            self.last_watered_day = self.day_planted

    def is_mature(self, current_day: int) -> bool:
        return current_day - self.day_planted >= self.growth_days_required


@dataclass(slots=True)
class PlotState:
    moisture: float = 0.65
    nitrogen: float = 0.75
    phosphorus: float = 0.75
    potassium: float = 0.75
    ph: float = 6.5
    soil_health: float = 0.7
    pest_pressure: float = 0.05
    disease_pressure: float = 0.03
    previous_crop_family: str | None = None
    crop: PlantedCrop | None = None


@dataclass(slots=True)
class InventoryLot:
    item_id: str
    quantity: int
    quality: str = "standard"
    produced_day: int = 0
    shelf_life_days: int = 7
    unit_cost: float = 0.0
    item_type: str = "crop"
    age_days: int = 0
    effective_shelf_life_days: int | None = None

    @property
    def remaining_shelf_life(self) -> int:
        shelf_life = self.effective_shelf_life_days or self.shelf_life_days
        return shelf_life - self.age_days


@dataclass(slots=True)
class ContractState:
    id: str
    buyer_id: str
    item_id: str
    quantity: int
    min_quality: str
    unit_price: float
    offered_day: int
    deadline_day: int
    penalty_rate: float
    accepted: bool = False
    delivered: int = 0
    resolved: bool = False

    @property
    def remaining(self) -> int:
        return max(0, self.quantity - self.delivered)


@dataclass(slots=True)
class ProcessingJob:
    recipe_id: str
    output_item_id: str
    output_quantity: int
    completion_day: int
    shelf_life_days: int
    unit_cost: float


@dataclass(slots=True)
class PlayerState:
    money: float
    slots_total: int
    day: int = 0
    operating_reserve: float = 0.0
    total_days: int | None = None

    crop_inventory: dict = field(default_factory=dict)
    inventory_lots: list[InventoryLot] = field(default_factory=list)
    seed_inventory: dict = field(default_factory=dict)
    planted: list[PlantedCrop] = field(default_factory=list)
    plots: list[PlotState] = field(default_factory=list)
    upgrades_owned: set = field(default_factory=set)
    upgrade_purchase_days: dict = field(default_factory=dict)
    crop_plant_counts: dict = field(default_factory=dict)
    fertilizer_inventory: int = 0
    water_units: float = 0.0

    active_contracts: list[ContractState] = field(default_factory=list)
    contract_offers: list[ContractState] = field(default_factory=list)
    processing_jobs: list[ProcessingJob] = field(default_factory=list)
    reputation: float = 0.0
    # Per-buyer standing, separate from the global `reputation` gate above.
    # `reputation` decides *whether* a buyer will deal with the farm at all
    # (min_reputation); this decides how good the terms get with a buyer the
    # farm keeps coming back to -- see simulation.contracts' relationship
    # bonus applied when pricing that buyer's next offer. Keyed by buyer id,
    # missing key means no history yet (equivalent to 0.0).
    buyer_relationships: dict = field(default_factory=dict)
    market_prices: dict = field(default_factory=dict)
    market_supply: dict = field(default_factory=dict)
    channel_capacity_used: dict = field(default_factory=dict)
    market_channels: list = field(default_factory=list)
    crop_catalog: dict = field(default_factory=dict)
    upgrades_catalog: dict = field(default_factory=dict)
    contract_config: dict = field(default_factory=dict)
    current_weather: dict = field(default_factory=dict)

    total_planted: int = 0
    total_harvested: int = 0
    total_sold: int = 0
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    expenses_by_category: dict = field(default_factory=dict)
    idle_days: int = 0
    bankrupt: bool = False
    bankruptcy_day: int | None = None
    bankruptcy_reason: str | None = None
    milestones: list = field(default_factory=list)

    total_waterings: int = 0
    total_harvest_events: int = 0
    total_crops_lost: int = 0
    total_fertilizer_bought: int = 0
    total_fertilizer_applied: int = 0
    total_spoiled: int = 0
    total_processed: int = 0
    processing_revenue: float = 0.0
    contracts_completed: int = 0
    contracts_failed: int = 0
    contract_penalties: float = 0.0
    revenue_by_channel: dict = field(default_factory=dict)
    quality_harvested: dict = field(default_factory=dict)
    losses_by_cause: dict = field(default_factory=dict)

    slot_days: int = 0
    occupied_slot_days: int = 0
    lowest_money: float | None = None
    highest_money: float | None = None
    crop_decision_observations: dict = field(default_factory=dict)
    # Run context and processing config are appended to preserve positional
    # construction compatibility for older callers.
    run_seed: int | None = None
    processing_recipes: list[dict] = field(default_factory=list)
    processing_capacity: int | None = None
    # Resolved simulation.derived.SoilDynamics for the run's world config,
    # stashed by the engine alongside crop_catalog/contract_config so the
    # action and forecasting helpers can reach it without a signature change
    # on every legacy-compatible entry point. None outside the full engine.
    soil_dynamics: object | None = None

    def __post_init__(self):
        if not self.plots:
            self.plots = [PlotState() for _ in range(self.slots_total)]

    def decision_random(self, *context) -> float:
        """Return a replayable policy value without consuming event RNG."""
        payload = repr((self.run_seed if self.run_seed is not None else 0, self.day, context))
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") / 2**64

    @property
    def open_slots(self) -> int:
        return self.slots_total - len(self.planted)

    def add_slots(self, amount: int) -> None:
        self.slots_total += amount
        self.plots.extend(PlotState() for _ in range(amount))

    def rebuild_crop_inventory(self) -> None:
        inventory = {}
        for lot in self.inventory_lots:
            if lot.item_type == "crop" and lot.quantity > 0:
                inventory[lot.item_id] = inventory.get(lot.item_id, 0) + lot.quantity
        self.crop_inventory = inventory

    def record_expense(self, category: str, amount: float) -> None:
        if amount <= 0:
            return
        self.total_expenses += amount
        self.expenses_by_category[category] = self.expenses_by_category.get(category, 0.0) + amount

    def track_peak_cash(self) -> None:
        """Update the recorded cash peak immediately, not just at day end.

        Sales, contract deliveries, and any other source of revenue must call
        this right after crediting `money` -- a same-day sale can push cash
        to a new high that later spending the same day (upgrades, care,
        planting) erases before engine._finish_day's once-per-day update
        would ever see it. Budget gates that ration spend against the farm's
        peak cash (economy_rules.should_buy_upgrade_within_budget) need to
        see that peak the moment it happens, not the day after.
        """
        if self.highest_money is None or self.money > self.highest_money:
            self.highest_money = self.money

    def observe_crop_decision(
        self,
        crop: dict,
        unlocked: bool,
        affordable: bool,
        selected: bool = False,
        blocked_reason: str | None = None,
        count_opportunity: bool = True,
    ) -> None:
        crop_id = crop["id"]
        # .get + explicit insert rather than setdefault: this runs for every
        # candidate crop on every planting attempt, and setdefault would build
        # the default dict on every call including the overwhelming majority
        # that already have an entry.
        observation = self.crop_decision_observations.get(crop_id)
        if observation is None:
            observation = {
                "opportunities": 0,
                "unlocked": 0,
                "affordable": 0,
                "selected": 0,
                "blocked_locked": 0,
                "blocked_unaffordable": 0,
            }
            self.crop_decision_observations[crop_id] = observation
        if count_opportunity:
            observation["opportunities"] += 1
            observation["unlocked"] += int(unlocked)
            observation["affordable"] += int(affordable)
        observation["selected"] += int(selected)
        if blocked_reason == "locked":
            observation["blocked_locked"] += 1
        elif blocked_reason == "unaffordable":
            observation["blocked_unaffordable"] += 1

    def import_legacy_inventory(self, crops_by_id: dict) -> None:
        represented = {}
        for lot in self.inventory_lots:
            if lot.item_type == "crop":
                represented[lot.item_id] = represented.get(lot.item_id, 0) + lot.quantity
        for crop_id, quantity in self.crop_inventory.items():
            missing = quantity - represented.get(crop_id, 0)
            if missing > 0:
                crop = crops_by_id[crop_id]
                self.inventory_lots.append(
                    InventoryLot(
                        item_id=crop_id,
                        quantity=missing,
                        produced_day=self.day,
                        shelf_life_days=crop.get("shelf_life_days", 7),
                    )
                )
