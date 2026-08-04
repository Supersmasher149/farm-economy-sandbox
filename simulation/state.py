"""Mutable state for one deterministic farm simulation run."""
from dataclasses import dataclass, field


QUALITY_ORDER = {"rejected": 0, "processing": 1, "standard": 2, "premium": 3}


@dataclass
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

    def __post_init__(self):
        if self.last_watered_day is None:
            self.last_watered_day = self.day_planted

    def is_mature(self, current_day: int) -> bool:
        return current_day - self.day_planted >= self.growth_days_required


@dataclass
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


@dataclass
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


@dataclass
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


@dataclass
class ProcessingJob:
    recipe_id: str
    output_item_id: str
    output_quantity: int
    completion_day: int
    shelf_life_days: int
    unit_cost: float


@dataclass
class PlayerState:
    money: float
    slots_total: int
    day: int = 0

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
    market_prices: dict = field(default_factory=dict)
    market_supply: dict = field(default_factory=dict)
    channel_capacity_used: dict = field(default_factory=dict)
    current_weather: dict = field(default_factory=dict)

    total_planted: int = 0
    total_harvested: int = 0
    total_sold: int = 0
    total_revenue: float = 0.0
    total_expenses: float = 0.0
    idle_days: int = 0
    bankrupt: bool = False
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
    lowest_money: float | None = None
    highest_money: float | None = None

    def __post_init__(self):
        if not self.plots:
            self.plots = [PlotState() for _ in range(self.slots_total)]

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

    def import_legacy_inventory(self, crops_by_id: dict) -> None:
        represented = {}
        for lot in self.inventory_lots:
            if lot.item_type == "crop":
                represented[lot.item_id] = represented.get(lot.item_id, 0) + lot.quantity
        for crop_id, quantity in self.crop_inventory.items():
            missing = quantity - represented.get(crop_id, 0)
            if missing > 0:
                crop = crops_by_id[crop_id]
                self.inventory_lots.append(InventoryLot(
                    item_id=crop_id,
                    quantity=missing,
                    produced_day=self.day,
                    shelf_life_days=crop.get("shelf_life_days", 7),
                ))
