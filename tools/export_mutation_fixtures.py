"""Export golden fixtures for farm-c's Phase 2 state-mutation port
(farm-c/tests/test_mutation.c): simulation/actions.py, inventory.py,
markets.py, processing.py, and contracts.py's day-loop mutators.

Same shape and rationale as tools/export_physics_fixtures.py: Python is the
oracle, a small synthetic config is shared across every scenario, and each
scenario records a full *snapshot* of the mutable player state before and
after calling one real Python function -- not just that function's own
documented return value/side effects -- so any incidental extra mutation the
C port gets wrong is caught too, not just the ones this docstring happened
to think to check.

Usage: python3 tools/export_mutation_fixtures.py
(also invoked by `make fixtures-mutation` in farm-c/Makefile)
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from simulation import actions, contracts, derived, inventory, markets, processing  # noqa: E402
from simulation.random_events import RandomEvents  # noqa: E402
from simulation.state import (  # noqa: E402
    ContractState,
    InventoryLot,
    PlantedCrop,
    PlayerState,
    ProcessingJob,
)

OUT_PATH = os.path.join(REPO_ROOT, "farm-c", "tests", "fixtures", "mutation.json")


def hexf(x) -> str:
    return float(x).hex()


# --- synthetic world -------------------------------------------------------

WHEAT = {
    "id": "wheat",
    "name": "Wheat",
    "role": "standard",
    "family": "grain",
    "seed_cost": 10.0,
    "growth_days": 4,
    "min_yield": 3,
    "max_yield": 6,
    "base_price": 8.0,
    "price_variation": 0.1,
    "loss_chance": 0.1,
    "water_interval_days": 2,
    "unlock_requirement": None,
    "shelf_life_days": 6,
    "temperature_range": [10, 30],
    "ph_range": [5.8, 7.2],
    "pest_susceptibility": 1.0,
    "disease_susceptibility": 1.0,
    "min_moisture": 0.35,
    "nutrient_demand": {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01},
    "seasonal_demand": {"spring": 1.0, "summer": 1.0, "autumn": 1.0, "winter": 1.0},
}
BERRY = {
    "id": "berry",
    "name": "Berry",
    "role": "other",
    "family": None,
    "seed_cost": 20.0,
    "growth_days": 8,
    "min_yield": 2,
    "max_yield": 4,
    "base_price": 14.0,
    "price_variation": 0.2,
    "loss_chance": 0.05,
    "water_interval_days": 1,
    "unlock_requirement": None,
    "shelf_life_days": 4,
    "temperature_range": [12, 26],
    "ph_range": [5.5, 6.8],
    "pest_susceptibility": 1.2,
    "disease_susceptibility": 0.9,
    "min_moisture": 0.5,
    "nutrient_demand": {"nitrogen": 0.015, "phosphorus": 0.02, "potassium": 0.01},
    "seasonal_demand": {"spring": 1.0, "summer": 1.0, "autumn": 1.0, "winter": 1.0},
}
FLOUR = {
    "id": "flour",
    "name": "Flour",
    "base_price": 20.0,
    "price_variation": 0.1,
    "processed_base_price": 20.0,
    "seasonal_demand": {},
}
CROPS = [WHEAT, BERRY]
CROPS_BY_ID = {c["id"]: c for c in CROPS}
PRODUCTS = [FLOUR]
ITEMS_BY_ID = {**CROPS_BY_ID, **{p["id"]: p for p in PRODUCTS}}

CAPACITY_UPGRADE = {
    "id": "capacity_1",
    "name": "Second Plot",
    "cost": 100.0,
    "effect": {"type": "capacity", "amount": 1},
}
GROWTH_UPGRADE = {
    "id": "growth_1",
    "name": "Greenhouse",
    "cost": 150.0,
    "effect": {"type": "growth_time_reduction", "amount": 0.2},
}
UPGRADES = [CAPACITY_UPGRADE, GROWTH_UPGRADE]

WHEAT_RECIPE = {
    "id": "mill_wheat",
    "input_item_id": "wheat",
    "output_item_id": "flour",
    "input_quantity": 3,
    "output_quantity": 1,
    "processing_days": 2,
    "min_quality": "processing",
    "cost": 2.0,
    "shelf_life_days": 20,
}
RECIPES = [WHEAT_RECIPE]

SPOT_CHANNEL = {
    "id": "spot",
    "min_quality": "rejected",
    "min_reputation": 0,
    # A real, large numeric cap -- not None. Python's `dict.get(key,
    # default)` only falls back to `default` when the key is *absent*, not
    # when it's present with value None, so a literal None here would
    # actually crash markets.sell's `daily_capacity - used` arithmetic (a
    # real Python gotcha, not a farm-c port issue) rather than mean
    # "uncapped". Every real config/markets.json channel always sets a
    # concrete number, which this mirrors.
    "daily_capacity": 1000,
    "price_multiplier": 1.0,
    "reputation_bonus": 0.002,
    "flat_fee": 0.0,
    "fee_rate": 0.0,
}
PREMIUM_CHANNEL = {
    "id": "premium",
    "min_quality": "standard",
    "min_reputation": 5,
    "daily_capacity": 4,
    "price_multiplier": 1.3,
    "reputation_bonus": 0.001,
    "flat_fee": 0.5,
    "fee_rate": 0.02,
}
CHANNELS = [SPOT_CHANNEL, PREMIUM_CHANNEL]

MILL_BUYER = {
    "id": "mill",
    "name": "Mill Co",
    "min_reputation": 0,
    "relationship_bonus_rate": 0.01,
    "items": ["wheat"],
    "quantity_range": [4, 8],
    "min_quality": "standard",
    "contract_price_multiplier": 1.2,
    "deadline_days": 6,
    "penalty_rate": 0.3,
}
BAKERY_BUYER = {
    "id": "bakery",
    "name": "Bakery",
    "min_reputation": 10,
    "relationship_bonus_rate": 0.02,
    "items": ["berry", "flour"],
    "quantity_range": [2, 5],
    "min_quality": "processing",
    "contract_price_multiplier": 1.4,
    "deadline_days": 8,
    "penalty_rate": 0.4,
}
BUYERS = [MILL_BUYER, BAKERY_BUYER]

FERTILIZER_CONFIG = {
    "cost": 8.0,
    "yield_bonus_pct": 0.25,
    "loss_chance_reduction": 0.03,
    "quality_bonus": 0.05,
    "nutrients_added": {"nitrogen": 0.25, "phosphorus": 0.15, "potassium": 0.15},
}
WATERING_SETTINGS = {
    "neglect_loss_chance_penalty_per_day": 0.05,
    "neglect_yield_penalty_per_day": 0.08,
    "max_neglect_loss_chance_bonus": 0.60,
    "max_neglect_yield_penalty": 0.80,
    "cost_per_plot": 0.4,
    "moisture_added": 0.45,
}
SOIL_CONFIG = {
    "dynamics": {
        "harvest_soil_health_cost": 0.02,
        "min_soil_health": 0.1,
        "fallow_pest_decay": 0.9,
        "fallow_disease_decay": 0.9,
        "fallow_soil_health_regen": 0.005,
        "pest_growth_per_day": 0.005,
        "disease_growth_per_rainfall": 0.08,
        "max_pest_pressure": 0.8,
        "max_disease_pressure": 0.8,
        "same_family_yield_penalty": 0.72,
        "same_family_quality_penalty": 0.8,
        "soil_health_yield_floor": 0.85,
        "soil_health_yield_span": 0.25,
    },
    "regen_per_day": {
        "moisture": 0.0,
        "nitrogen": 0.0,
        "phosphorus": 0.0,
        "potassium": 0.0,
        "soil_health": 0.0,
        "pest_pressure": 0.0,
        "disease_pressure": 0.0,
    },
}
DYNAMICS = derived.SoilDynamics(SOIL_CONFIG)
STORAGE_CONFIG = {"daily_cost": 0.3, "capacity": 12, "shelf_life_multiplier": 1.0}
MARKET_CONFIG = {
    "minimum_supply_multiplier": 0.6,
    "supply_decay": 0.8,
    "default_variation": 0.12,
    "channels": CHANNELS,
}
CONTRACTS_CONFIG = {
    "fallback_price_multiplier": 1.15,
    "production_safety_factor": 0.45,
    "offer_expiry_days": 3,
    "offer_interval_days": 5,
    "default_penalty_rate": 0.35,
    "relationship_gain_per_delivery": 6.0,
    "relationship_loss_per_failure": 5.0,
    "relationship_bonus_cap": 0.25,
}

ITEM_INDEX = {"wheat": 0, "berry": 1, "flour": 2}
UPGRADE_INDEX = {"capacity_1": 0, "growth_1": 1}
BUYER_INDEX = {"mill": 0, "bakery": 1}
CHANNEL_INDEX = {"spot": 0, "premium": 1}
RECIPE_INDEX = {"mill_wheat": 0}


def serialize_config():
    items = [
        {
            "id": ITEM_INDEX["wheat"],
            "type": "crop",
            "external_id": "wheat",
            "base_price": WHEAT["base_price"],
            "price_variation": WHEAT["price_variation"],
            "seasonal_demand": WHEAT["seasonal_demand"],
        },
        {
            "id": ITEM_INDEX["berry"],
            "type": "crop",
            "external_id": "berry",
            "base_price": BERRY["base_price"],
            "price_variation": BERRY["price_variation"],
            "seasonal_demand": BERRY["seasonal_demand"],
        },
        {
            "id": ITEM_INDEX["flour"],
            "type": "product",
            "external_id": "flour",
            "base_price": FLOUR["base_price"],
            "price_variation": FLOUR["price_variation"],
            "seasonal_demand": {},
        },
    ]
    crops = []
    for crop in CROPS:
        temp = crop["temperature_range"]
        ph = crop["ph_range"]
        crops.append(
            {
                "item_id": ITEM_INDEX[crop["id"]],
                "external_id": crop["id"],
                "role": crop["role"],
                "family": crop["family"],
                "seed_cost": crop["seed_cost"],
                "growth_days": crop["growth_days"],
                "min_yield": crop["min_yield"],
                "max_yield": crop["max_yield"],
                "loss_chance": crop["loss_chance"],
                "water_interval_days": crop["water_interval_days"],
                "nutrient_demand": crop["nutrient_demand"],
                "unlock_requirement": {"type": "none"},
                "temperature_low": temp[0],
                "temperature_high": temp[1],
                "ph_low": ph[0],
                "ph_high": ph[1],
                "min_moisture": crop["min_moisture"],
                "pest_susceptibility": crop["pest_susceptibility"],
                "disease_susceptibility": crop["disease_susceptibility"],
                "shelf_life_days": crop["shelf_life_days"],
            }
        )
    upgrades = [
        {
            "id": UPGRADE_INDEX[u["id"]],
            "external_id": u["id"],
            "cost": u["cost"],
            "effect": u["effect"],
        }
        for u in UPGRADES
    ]
    recipes = [
        {
            "id": RECIPE_INDEX[r["id"]],
            "external_id": r["id"],
            "input_item_id": ITEM_INDEX[r["input_item_id"]],
            "output_item_id": ITEM_INDEX[r["output_item_id"]],
            "input_quantity": r["input_quantity"],
            "output_quantity": r["output_quantity"],
            "processing_days": r["processing_days"],
            "min_quality": r["min_quality"],
            "cost": r["cost"],
            "shelf_life_days": r["shelf_life_days"],
        }
        for r in RECIPES
    ]
    channels = [
        {
            "channel_id": CHANNEL_INDEX[c["id"]],
            "external_id": c["id"],
            "min_quality": c["min_quality"],
            "min_reputation": c["min_reputation"],
            "daily_capacity": c["daily_capacity"],
            "price_multiplier": c["price_multiplier"],
            "reputation_bonus": c["reputation_bonus"],
            "flat_fee": c["flat_fee"],
            "fee_rate": c["fee_rate"],
        }
        for c in CHANNELS
    ]
    buyers = [
        {
            "id": BUYER_INDEX[b["id"]],
            "external_id": b["id"],
            "min_reputation": b["min_reputation"],
            "relationship_bonus_rate": b["relationship_bonus_rate"],
            "allowed_items": [ITEM_INDEX[i] for i in b["items"]],
            "quantity_min": b["quantity_range"][0],
            "quantity_max": b["quantity_range"][1],
            "min_quality": b["min_quality"],
            "contract_price_multiplier": b["contract_price_multiplier"],
            "deadline_days": b["deadline_days"],
            "penalty_rate": b["penalty_rate"],
        }
        for b in BUYERS
    ]
    return {
        "items": items,
        "crops": crops,
        "upgrades": upgrades,
        "recipes": recipes,
        "channels": channels,
        "buyers": buyers,
        "fertilizer": FERTILIZER_CONFIG,
        "watering": WATERING_SETTINGS,
        "soil_dynamics": SOIL_CONFIG["dynamics"],
        "plot_regen": SOIL_CONFIG["regen_per_day"],
        "markets": {
            "minimum_supply_multiplier": MARKET_CONFIG["minimum_supply_multiplier"],
            "supply_decay": MARKET_CONFIG["supply_decay"],
        },
        "storage": STORAGE_CONFIG,
        "contracts_config": CONTRACTS_CONFIG,
    }


# --- player construction / serialization ------------------------------------


def make_player(money=200.0, slots_total=3, day=0):
    player = PlayerState(money=money, slots_total=slots_total, day=day)
    player.highest_money = money
    player.soil_dynamics = DYNAMICS
    return player


def serialize_planted(p: PlantedCrop):
    return {
        "crop_index": ITEM_INDEX[p.crop_id],
        "day_planted": p.day_planted,
        "growth_days_required": p.growth_days_required,
        "last_watered_day": p.last_watered_day,
        "neglect_days": p.neglect_days,
        "fertilized": p.fertilized,
        "plot_index": p.plot_index if p.plot_index is not None else -1,
        "water_stress": hexf(p.water_stress),
        "nutrient_stress": hexf(p.nutrient_stress),
        "temperature_stress": hexf(p.temperature_stress),
        "pest_stress": hexf(p.pest_stress),
        "disease_stress": hexf(p.disease_stress),
        "accrued_cost": hexf(p.accrued_cost),
    }


def serialize_plot(plot):
    return {
        "moisture": hexf(plot.moisture),
        "nitrogen": hexf(plot.nitrogen),
        "phosphorus": hexf(plot.phosphorus),
        "potassium": hexf(plot.potassium),
        "ph": hexf(plot.ph),
        "soil_health": hexf(plot.soil_health),
        "pest_pressure": hexf(plot.pest_pressure),
        "disease_pressure": hexf(plot.disease_pressure),
        "previous_crop_family": plot.previous_crop_family,
        "crop": serialize_planted(plot.crop) if plot.crop is not None else None,
    }


def serialize_lot(lot: InventoryLot):
    return {
        "item_index": ITEM_INDEX[lot.item_id],
        "quantity": lot.quantity,
        "quality": lot.quality,
        "produced_day": lot.produced_day,
        "age_days": lot.age_days,
        "shelf_life_days": lot.shelf_life_days,
        "effective_shelf_life_days": lot.effective_shelf_life_days
        if lot.effective_shelf_life_days is not None
        else 0,
        "unit_cost": hexf(lot.unit_cost),
        "item_type": lot.item_type,
    }


def serialize_job(job: ProcessingJob):
    return {
        "recipe_index": RECIPE_INDEX[job.recipe_id],
        "output_item_index": ITEM_INDEX[job.output_item_id],
        "output_quantity": job.output_quantity,
        "completion_day": job.completion_day,
        "shelf_life_days": job.shelf_life_days,
        "unit_cost": hexf(job.unit_cost),
    }


def serialize_contract(c: ContractState):
    return {
        "id": c.id,
        "buyer_index": BUYER_INDEX[c.buyer_id],
        "item_index": ITEM_INDEX[c.item_id],
        "quantity": c.quantity,
        "delivered": c.delivered,
        "min_quality": c.min_quality,
        "unit_price": hexf(c.unit_price),
        "penalty_rate": hexf(c.penalty_rate),
        "offered_day": c.offered_day,
        "deadline_day": c.deadline_day,
        "accepted": c.accepted,
        "resolved": c.resolved,
    }


def serialize_player(player: PlayerState):
    return {
        "money": hexf(player.money),
        "slots_total": player.slots_total,
        "day": player.day,
        "seed_inventory": {ITEM_INDEX[k]: v for k, v in player.seed_inventory.items()},
        "fertilizer_inventory": player.fertilizer_inventory,
        "total_expenses": hexf(player.total_expenses),
        "expenses_by_category": {k: hexf(v) for k, v in player.expenses_by_category.items()},
        "total_planted": player.total_planted,
        "crop_plant_counts": {ITEM_INDEX[k]: v for k, v in player.crop_plant_counts.items()},
        "planted": [serialize_planted(p) for p in player.planted],
        "plots": [serialize_plot(p) for p in player.plots],
        "inventory_lots": [serialize_lot(lot) for lot in player.inventory_lots],
        "processing_jobs": [serialize_job(j) for j in player.processing_jobs],
        "active_contracts": [serialize_contract(c) for c in player.active_contracts],
        "contract_offers": [serialize_contract(c) for c in player.contract_offers],
        "buyer_relationships": {
            BUYER_INDEX[k]: hexf(v) for k, v in player.buyer_relationships.items()
        },
        "reputation": hexf(player.reputation),
        "market_prices": {ITEM_INDEX[k]: hexf(v) for k, v in player.market_prices.items()},
        "market_supply": {ITEM_INDEX[k]: hexf(v) for k, v in player.market_supply.items()},
        "channel_capacity_used": {
            CHANNEL_INDEX[k]: v for k, v in player.channel_capacity_used.items()
        },
        "revenue_by_channel": {k: hexf(v) for k, v in player.revenue_by_channel.items()},
        "total_revenue": hexf(player.total_revenue),
        "total_sold": player.total_sold,
        "idle_days": player.idle_days,
        "total_waterings": player.total_waterings,
        "total_harvest_events": player.total_harvest_events,
        "total_harvested": player.total_harvested,
        "total_crops_lost": player.total_crops_lost,
        "total_fertilizer_bought": player.total_fertilizer_bought,
        "total_fertilizer_applied": player.total_fertilizer_applied,
        "total_spoiled": player.total_spoiled,
        "total_processed": player.total_processed,
        "processing_revenue": hexf(player.processing_revenue),
        "contracts_completed": player.contracts_completed,
        "contracts_failed": player.contracts_failed,
        "contract_penalties": hexf(player.contract_penalties),
        "quality_harvested": dict(player.quality_harvested),
        "losses_by_cause": dict(player.losses_by_cause),
        "highest_money": hexf(player.highest_money) if player.highest_money is not None else None,
        "upgrades_owned": sorted(UPGRADE_INDEX[u] for u in player.upgrades_owned),
        "upgrade_purchase_days": {
            UPGRADE_INDEX[k]: v for k, v in player.upgrade_purchase_days.items()
        },
    }


def snapshot_case(name, player, run):
    before = serialize_player(player)
    result = run(player)
    after = serialize_player(player)
    case = {"name": name, "before": before, "after": after}
    if result is not None:
        case["result"] = result
    return case


# --- scenario builders per function -----------------------------------------


def build_buy_seeds_cases():
    cases = []

    def run(qty):
        def _run(player):
            ok = actions.buy_seeds(player, WHEAT, quantity=qty)
            return {"crop_index": ITEM_INDEX["wheat"], "quantity": qty, "ok": ok}

        return _run

    cases.append(snapshot_case("simple_buy", make_player(money=100), run(2)))
    cases.append(snapshot_case("too_expensive", make_player(money=5), run(1)))
    cases.append(snapshot_case("nonpositive_quantity", make_player(money=100), run(0)))
    return cases


def build_plant_seed_cases():
    cases = []

    def run(crop, growth_days, fertilized):
        def _run(player):
            ok = actions.plant_seed(
                player,
                crop,
                growth_days,
                fertilized=fertilized,
                fertilizer_config=FERTILIZER_CONFIG,
            )
            return {
                "crop_index": ITEM_INDEX[crop["id"]],
                "growth_days": growth_days,
                "fertilized": fertilized,
                "ok": ok,
            }

        return _run

    p1 = make_player(money=100, slots_total=2)
    actions.buy_seeds(p1, WHEAT, 1)
    cases.append(snapshot_case("plant_unfertilized", p1, run(WHEAT, 4, False)))

    p2 = make_player(money=100, slots_total=2)
    actions.buy_seeds(p2, WHEAT, 1)
    actions.buy_fertilizer(p2, FERTILIZER_CONFIG, 1)
    cases.append(snapshot_case("plant_fertilized", p2, run(WHEAT, 4, True)))

    p3 = make_player(money=100, slots_total=1)
    actions.buy_seeds(p3, WHEAT, 1)
    actions.buy_seeds(p3, BERRY, 1)
    actions.plant_seed(p3, WHEAT, 4)
    cases.append(snapshot_case("no_open_slot", p3, run(BERRY, 8, False)))

    p4 = make_player(money=100, slots_total=2)
    cases.append(snapshot_case("no_seed_in_inventory", p4, run(WHEAT, 4, False)))
    return cases


def build_water_crop_cases():
    cases = []

    def run(index):
        def _run(player):
            ok = actions.water_crop(player, player.planted[index], WATERING_SETTINGS)
            return {"planted_index": index, "ok": ok}

        return _run

    p1 = make_player(money=100, slots_total=2)
    actions.buy_seeds(p1, WHEAT, 1)
    actions.plant_seed(p1, WHEAT, 4)
    p1.day = 2
    p1.planted[0].neglect_days = 3
    cases.append(snapshot_case("waters_and_clears_neglect", p1, run(0)))

    p2 = make_player(money=0.1, slots_total=2)
    actions.buy_seeds(make_player(money=100), WHEAT, 1)  # unused, keeps parity of call shape
    p2.planted.append(PlantedCrop("wheat", 0, 4, plot_index=0))
    p2.plots[0].crop = p2.planted[0]
    cases.append(snapshot_case("cannot_afford", p2, run(0)))
    return cases


def build_buy_fertilizer_cases():
    cases = []

    def run(qty):
        def _run(player):
            return {"quantity": qty, "ok": actions.buy_fertilizer(player, FERTILIZER_CONFIG, qty)}

        return _run

    cases.append(snapshot_case("simple_buy", make_player(money=100), run(2)))
    cases.append(snapshot_case("too_expensive", make_player(money=1), run(3)))
    return cases


def build_fertilize_crop_cases():
    cases = []

    def run(index):
        def _run(player):
            ok = actions.fertilize_crop(player, player.planted[index], FERTILIZER_CONFIG)
            return {"planted_index": index, "ok": ok}

        return _run

    p1 = make_player(money=100, slots_total=2)
    actions.buy_seeds(p1, WHEAT, 1)
    actions.plant_seed(p1, WHEAT, 4)
    actions.buy_fertilizer(p1, FERTILIZER_CONFIG, 1)
    cases.append(snapshot_case("fertilizes_unfertilized_crop", p1, run(0)))

    p2 = make_player(money=100, slots_total=2)
    actions.buy_seeds(p2, WHEAT, 1)
    actions.buy_fertilizer(p2, FERTILIZER_CONFIG, 1)
    actions.plant_seed(p2, WHEAT, 4, fertilized=True, fertilizer_config=FERTILIZER_CONFIG)
    cases.append(snapshot_case("already_fertilized", p2, run(0)))
    return cases


def build_harvest_mature_cases():
    cases = []

    def run(seed, watering=WATERING_SETTINGS, fertilizer=FERTILIZER_CONFIG):
        def _run(player):
            rng = RandomEvents(seed)
            harvested = actions.harvest_mature(player, CROPS_BY_ID, rng, watering, fertilizer)
            return {"seed": seed, "harvested": harvested}

        return _run

    p1 = make_player(money=100, slots_total=3, day=0)
    actions.buy_seeds(p1, WHEAT, 1)
    actions.plant_seed(p1, WHEAT, 4)
    actions.buy_seeds(p1, BERRY, 1)
    actions.plant_seed(p1, BERRY, 8)
    p1.day = 4  # wheat mature, berry not
    cases.append(snapshot_case("one_mature_one_growing", p1, run(1)))

    p2 = make_player(money=100, slots_total=2, day=0)
    actions.buy_seeds(p2, WHEAT, 1)
    actions.plant_seed(p2, WHEAT, 4)
    p2.day = 4
    p2.planted[0].neglect_days = 20  # push loss chance high
    cases.append(snapshot_case("heavy_neglect_may_lose", p2, run(777)))

    p3 = make_player(money=100, slots_total=2, day=0)
    actions.buy_seeds(p3, WHEAT, 2)
    actions.plant_seed(p3, WHEAT, 4)
    actions.plant_seed(p3, WHEAT, 4)
    p3.day = 4
    cases.append(snapshot_case("two_mature_same_crop", p3, run(42)))
    return cases


def build_buy_upgrade_cases():
    cases = []

    def run(upgrade):
        def _run(player):
            ok = actions.buy_upgrade(player, upgrade)
            return {"upgrade_index": UPGRADE_INDEX[upgrade["id"]], "ok": ok}

        return _run

    cases.append(
        snapshot_case("buys_capacity_upgrade", make_player(money=200), run(CAPACITY_UPGRADE))
    )
    cases.append(snapshot_case("buys_growth_upgrade", make_player(money=200), run(GROWTH_UPGRADE)))
    p3 = make_player(money=200)
    p3.upgrades_owned.add("capacity_1")
    cases.append(snapshot_case("already_owned", p3, run(CAPACITY_UPGRADE)))
    cases.append(snapshot_case("too_expensive", make_player(money=10), run(CAPACITY_UPGRADE)))
    return cases


def build_do_nothing_cases():
    def run(player):
        actions.do_nothing(player)
        return {}

    return [snapshot_case("increments_idle_days", make_player(), run)]


def build_consume_cases():
    cases = []

    def run(item_id, qty, min_quality):
        def _run(player):
            consumed, cost = inventory.consume(player, item_id, qty, min_quality)
            return {
                "item_index": ITEM_INDEX[item_id],
                "quantity": qty,
                "min_quality": min_quality,
                "consumed": consumed,
                "cost": hexf(cost),
            }

        return _run

    p1 = make_player()
    p1.inventory_lots.append(InventoryLot("wheat", 4, "standard", shelf_life_days=6, unit_cost=1.5))
    p1.inventory_lots.append(InventoryLot("wheat", 3, "premium", shelf_life_days=2, unit_cost=2.0))
    cases.append(snapshot_case("fefo_prefers_soonest_expiry", p1, run("wheat", 5, "rejected")))

    p2 = make_player()
    p2.inventory_lots.append(InventoryLot("wheat", 2, "standard", shelf_life_days=6, unit_cost=1.0))
    cases.append(snapshot_case("insufficient_quantity", p2, run("wheat", 5, "rejected")))

    p3 = make_player()
    p3.inventory_lots.append(
        InventoryLot("wheat", 5, "processing", shelf_life_days=6, unit_cost=1.0)
    )
    cases.append(snapshot_case("min_quality_excludes_all", p3, run("wheat", 3, "standard")))
    return cases


def build_capture_storage_liability_cases():
    cases = []

    def run(storage_cfg):
        def _run(player):
            return {"liability": hexf(inventory.capture_storage_liability(player, storage_cfg))}

        return _run

    p1 = make_player()
    p1.inventory_lots.append(InventoryLot("wheat", 1, "standard"))
    cases.append(snapshot_case("has_inventory", p1, run(STORAGE_CONFIG)))
    cases.append(snapshot_case("empty_inventory", make_player(), run(STORAGE_CONFIG)))
    return cases


def build_collect_storage_liability_cases():
    cases = []

    def run(liability):
        def _run(player):
            charged = inventory.collect_storage_liability(player, liability)
            return {"liability": hexf(liability), "charged": hexf(charged)}

        return _run

    cases.append(snapshot_case("full_charge", make_player(money=10), run(0.3)))
    cases.append(snapshot_case("clamped_to_available_cash", make_player(money=0.1), run(5.0)))
    return cases


def build_enforce_storage_capacity_cases():
    cases = []

    def run(capacity):
        def _run(player):
            return {
                "capacity": capacity,
                "spoiled": inventory.enforce_storage_capacity(player, capacity),
            }

        return _run

    p1 = make_player()
    p1.inventory_lots.append(InventoryLot("wheat", 5, "standard", shelf_life_days=10))
    p1.inventory_lots.append(InventoryLot("wheat", 5, "premium", shelf_life_days=3))
    cases.append(snapshot_case("trims_soonest_expiry_first", p1, run(6)))

    p2 = make_player()
    p2.inventory_lots.append(InventoryLot("wheat", 3, "standard", shelf_life_days=10))
    cases.append(snapshot_case("under_capacity_noop", p2, run(10)))
    return cases


def build_age_and_spoil_cases():
    cases = []

    def run(storage_cfg, charge):
        def _run(player):
            spoiled = inventory.age_and_spoil(player, storage_cfg, charge_storage=charge)
            return {"charge_storage": charge, "spoiled": spoiled}

        return _run

    p1 = make_player(day=1)
    p1.inventory_lots.append(InventoryLot("wheat", 4, "premium", shelf_life_days=2, produced_day=0))
    cases.append(snapshot_case("downgrades_premium", p1, run(STORAGE_CONFIG, True)))

    p2 = make_player(day=3)
    p2.inventory_lots.append(
        InventoryLot("wheat", 4, "standard", shelf_life_days=2, produced_day=0, age_days=2)
    )
    cases.append(snapshot_case("fully_spoils", p2, run(STORAGE_CONFIG, True)))

    p3 = make_player(day=1, money=0.0)
    p3.inventory_lots.append(
        InventoryLot("wheat", 2, "standard", shelf_life_days=10, produced_day=0)
    )
    cases.append(snapshot_case("no_charge_when_disabled", p3, run(STORAGE_CONFIG, False)))
    return cases


def build_update_daily_prices_cases():
    cases = []

    def run(season, seed):
        def _run(player):
            prices = markets.update_daily_prices(
                player, ITEMS_BY_ID, MARKET_CONFIG, RandomEvents(seed)
            )
            return {
                "season": season,
                "seed": seed,
                "prices": {ITEM_INDEX[k]: hexf(v) for k, v in prices.items()},
            }

        return _run

    p1 = make_player()
    p1.current_weather = {"season": "spring"}
    p1.market_supply["wheat"] = 40.0
    cases.append(snapshot_case("spring_seed1", p1, run("spring", 1)))

    p2 = make_player()
    p2.current_weather = {"season": "winter"}
    cases.append(snapshot_case("winter_seed42", p2, run("winter", 42)))
    return cases


def build_sell_cases():
    cases = []

    def run(item_id, qty, channel, quality=None, min_quality=None):
        def _run(player):
            revenue, sold = markets.sell(
                player, item_id, qty, channel, quality=quality, min_quality=min_quality
            )
            return {
                "item_index": ITEM_INDEX[item_id],
                "quantity": qty,
                "channel_index": CHANNEL_INDEX[channel["id"]],
                "quality": quality,
                "min_quality": min_quality,
                "revenue": hexf(revenue),
                "sold": sold,
            }

        return _run

    p1 = make_player()
    p1.market_prices["wheat"] = 8.0
    p1.inventory_lots.append(
        InventoryLot("wheat", 6, "standard", shelf_life_days=10, unit_cost=1.0)
    )
    p1.inventory_lots.append(InventoryLot("wheat", 4, "premium", shelf_life_days=3, unit_cost=1.5))
    cases.append(
        snapshot_case("sells_across_lots_fefo_desc_quality", p1, run("wheat", 8, SPOT_CHANNEL))
    )

    p2 = make_player()
    p2.reputation = 20.0
    p2.market_prices["wheat"] = 8.0
    p2.inventory_lots.append(
        InventoryLot("wheat", 3, "standard", shelf_life_days=10, unit_cost=1.0)
    )
    cases.append(snapshot_case("respects_channel_capacity", p2, run("wheat", 10, PREMIUM_CHANNEL)))

    p3 = make_player()
    p3.market_prices["wheat"] = 8.0
    p3.inventory_lots.append(InventoryLot("wheat", 2, "premium", shelf_life_days=10, unit_cost=1.0))
    cases.append(
        snapshot_case("exact_quality_match", p3, run("wheat", 2, SPOT_CHANNEL, quality="premium"))
    )

    p4 = make_player()
    p4.inventory_lots.append(InventoryLot("wheat", 2, "standard", unit_cost=1.0))
    cases.append(snapshot_case("unknown_price_rejects", p4, run("wheat", 2, SPOT_CHANNEL)))
    return cases


def build_start_job_cases():
    cases = []

    def run(recipe, batches, capacity):
        def _run(player):
            ok = processing.start_job(player, recipe, batches, capacity)
            return {
                "recipe_index": RECIPE_INDEX[recipe["id"]],
                "batches": batches,
                "capacity": capacity,
                "ok": ok,
            }

        return _run

    p1 = make_player(money=100)
    p1.inventory_lots.append(
        InventoryLot("wheat", 9, "processing", shelf_life_days=10, unit_cost=1.0)
    )
    cases.append(snapshot_case("starts_two_batches", p1, run(WHEAT_RECIPE, 2, 5)))

    p2 = make_player(money=100)
    p2.inventory_lots.append(
        InventoryLot("wheat", 2, "processing", shelf_life_days=10, unit_cost=1.0)
    )
    cases.append(snapshot_case("insufficient_input", p2, run(WHEAT_RECIPE, 1, 5)))

    p3 = make_player(money=100)
    p3.inventory_lots.append(
        InventoryLot("wheat", 9, "processing", shelf_life_days=10, unit_cost=1.0)
    )
    cases.append(snapshot_case("exceeds_capacity", p3, run(WHEAT_RECIPE, 3, 2)))
    return cases


def build_complete_jobs_cases():
    cases = []

    def run(player):
        return {"completed": processing.complete_jobs(player)}

    p1 = make_player(day=3)
    p1.processing_jobs.append(
        ProcessingJob("mill_wheat", "flour", 2, completion_day=3, shelf_life_days=20, unit_cost=5.0)
    )
    p1.processing_jobs.append(
        ProcessingJob("mill_wheat", "flour", 1, completion_day=5, shelf_life_days=20, unit_cost=6.0)
    )
    cases.append(snapshot_case("one_done_one_pending", p1, run))
    return cases


def build_visible_offers_cases():
    cases = []

    def run(player):
        offers = contracts.visible_offers(player)
        return {"offers": [BUYER_INDEX[o.buyer_id] for o in offers]}

    p1 = make_player(day=5)
    p1.contract_offers.append(
        ContractState(
            "o1",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=2,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    p1.contract_offers.append(
        ContractState(
            "o2",
            "bakery",
            "berry",
            3,
            "processing",
            15.0,
            offered_day=1,
            deadline_day=20,
            penalty_rate=0.4,
            resolved=True,
        )
    )
    p1.contract_offers.append(
        ContractState(
            "o3",
            "mill",
            "wheat",
            4,
            "standard",
            9.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    # o3 offered_day=0, expiry_days=3 -> expires after day 3; day=5 so expired.
    cases.append(snapshot_case("filters_resolved_and_expired", p1, run))
    return cases


def build_generate_offers_cases():
    cases = []

    def run(seed):
        def _run(player):
            offers = contracts.generate_offers(
                player, CONTRACTS_CONFIG, BUYERS, ITEMS_BY_ID, RandomEvents(seed)
            )
            return {"seed": seed, "new_offer_count": len(offers)}

        return _run

    p1 = make_player(day=5)  # multiple of offer_interval_days=5
    cases.append(snapshot_case("generates_on_interval_day", p1, run(1)))

    p2 = make_player(day=6)  # not a multiple of 5
    cases.append(snapshot_case("skips_off_interval_day", p2, run(1)))

    p3 = make_player(day=10)
    p3.reputation = 50.0  # meets bakery's min_reputation=10
    cases.append(snapshot_case("both_buyers_eligible", p3, run(99)))

    p4 = make_player(day=15)
    p4.buyer_relationships["mill"] = 40.0
    cases.append(snapshot_case("relationship_bonus_applied", p4, run(7)))
    return cases


def build_accept_cases():
    cases = []

    def run(contract_id):
        def _run(player):
            return {"contract_id_target": contract_id, "ok": contracts.accept(player, contract_id)}

        return _run

    p1 = make_player(day=2)
    p1.contract_offers.append(
        ContractState(
            "o1",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=1,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    cases.append(snapshot_case("accepts_valid_offer", p1, run("o1")))

    p2 = make_player(day=10)
    p2.contract_offers.append(
        ContractState(
            "o2",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=1,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    cases.append(snapshot_case("rejects_expired_offer", p2, run("o2")))

    p3 = make_player(day=2)
    cases.append(snapshot_case("rejects_missing_offer", p3, run("does-not-exist")))
    return cases


def build_deliver_cases():
    cases = []

    def run(contract_id, qty):
        def _run(player):
            revenue, delivered = contracts.deliver(player, contract_id, qty)
            return {
                "contract_id_target": contract_id,
                "quantity": qty,
                "revenue": hexf(revenue),
                "delivered": delivered,
            }

        return _run

    p1 = make_player(day=2)
    p1.active_contracts.append(
        ContractState(
            "c1",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    p1.inventory_lots.append(
        InventoryLot("wheat", 5, "standard", shelf_life_days=10, unit_cost=2.0)
    )
    cases.append(snapshot_case("partial_delivery", p1, run("c1", 3)))

    p2 = make_player(day=2)
    p2.active_contracts.append(
        ContractState(
            "c2",
            "mill",
            "wheat",
            3,
            "standard",
            10.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    p2.inventory_lots.append(
        InventoryLot("wheat", 5, "standard", shelf_life_days=10, unit_cost=2.0)
    )
    cases.append(snapshot_case("completes_contract", p2, run("c2", 3)))

    p3 = make_player(day=25)
    p3.active_contracts.append(
        ContractState(
            "c3",
            "mill",
            "wheat",
            3,
            "standard",
            10.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.3,
        )
    )
    p3.inventory_lots.append(
        InventoryLot("wheat", 5, "standard", shelf_life_days=10, unit_cost=2.0)
    )
    cases.append(snapshot_case("past_deadline_rejects", p3, run("c3", 3)))
    return cases


def build_resolve_expired_cases():
    cases = []

    def run(player):
        contracts.resolve_expired(player)
        return {}

    p1 = make_player(day=25, money=100)
    p1.active_contracts.append(
        ContractState(
            "c1",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.3,
            delivered=2,
        )
    )
    p1.contract_offers.append(
        ContractState(
            "o1",
            "bakery",
            "berry",
            2,
            "processing",
            12.0,
            offered_day=24,
            deadline_day=40,
            penalty_rate=0.4,
        )
    )
    cases.append(snapshot_case("penalizes_and_drops_failed", p1, run))

    p2 = make_player(day=25, money=1.0)
    p2.active_contracts.append(
        ContractState(
            "c2",
            "mill",
            "wheat",
            5,
            "standard",
            10.0,
            offered_day=0,
            deadline_day=20,
            penalty_rate=0.9,
        )
    )
    cases.append(snapshot_case("penalty_clamped_to_cash", p2, run))
    return cases


def main():
    fixtures = {
        "config": serialize_config(),
        "buy_seeds_cases": build_buy_seeds_cases(),
        "plant_seed_cases": build_plant_seed_cases(),
        "water_crop_cases": build_water_crop_cases(),
        "buy_fertilizer_cases": build_buy_fertilizer_cases(),
        "fertilize_crop_cases": build_fertilize_crop_cases(),
        "harvest_mature_cases": build_harvest_mature_cases(),
        "buy_upgrade_cases": build_buy_upgrade_cases(),
        "do_nothing_cases": build_do_nothing_cases(),
        "consume_cases": build_consume_cases(),
        "capture_storage_liability_cases": build_capture_storage_liability_cases(),
        "collect_storage_liability_cases": build_collect_storage_liability_cases(),
        "enforce_storage_capacity_cases": build_enforce_storage_capacity_cases(),
        "age_and_spoil_cases": build_age_and_spoil_cases(),
        "update_daily_prices_cases": build_update_daily_prices_cases(),
        "sell_cases": build_sell_cases(),
        "start_job_cases": build_start_job_cases(),
        "complete_jobs_cases": build_complete_jobs_cases(),
        "visible_offers_cases": build_visible_offers_cases(),
        "generate_offers_cases": build_generate_offers_cases(),
        "accept_cases": build_accept_cases(),
        "deliver_cases": build_deliver_cases(),
        "resolve_expired_cases": build_resolve_expired_cases(),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(fixtures, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
