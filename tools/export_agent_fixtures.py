"""Export golden fixtures for farm-c's agent port (farm-c/tests/test_agents.c).

Builds a small synthetic world plus a handful of PlayerState scenarios in
the same directly-constructed style as tests/test_strategy_controls.py's
`policy_inputs()`, runs every one of the 11 real agent classes' full
decision surface against each scenario, and writes the world + scenarios +
recorded (agent, method, scenario) -> expected-output cases to
farm-c/tests/fixtures/agents.json.

Python is the oracle here: farm-c has no engine of its own yet (see
docs/c-port-plan.md and farm-c/README.md), so this is what farm-c/tests/
test_agents.c verifies the C port against, instead of a golden-replay run.

Usage: python3 tools/export_agent_fixtures.py
(also invoked by `make fixtures` in farm-c/Makefile)
"""

import copy
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from agents.diversifier import Diversifier  # noqa: E402
from agents.fast_seller import FastSeller  # noqa: E402
from agents.fertilizer_maximalist import FertilizerMaximalist  # noqa: E402
from agents.neglectful_grower import NeglectfulGrower  # noqa: E402
from agents.no_upgrade_player import NoUpgradePlayer  # noqa: E402
from agents.profit_optimizer import ProfitOptimizer  # noqa: E402
from agents.progression_player import ProgressionPlayer  # noqa: E402
from agents.random_agent import RandomAgent  # noqa: E402
from agents.reckless_spender import RecklessSpender  # noqa: E402
from agents.risk_averse_grower import RiskAverseGrower  # noqa: E402
from agents.upgrade_rusher import UpgradeRusher  # noqa: E402
from simulation.state import (  # noqa: E402
    QUALITY_ORDER,
    ContractState,
    InventoryLot,
    PlantedCrop,
    PlayerState,
)

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "farm-c",
    "tests",
    "fixtures",
    "agents.json",
)

AGENT_CLASSES = [
    FastSeller,
    NoUpgradePlayer,
    NeglectfulGrower,
    RecklessSpender,
    RiskAverseGrower,
    Diversifier,
    UpgradeRusher,
    ProgressionPlayer,
    ProfitOptimizer,
    FertilizerMaximalist,
    RandomAgent,
]


# --- Shared world -----------------------------------------------------------


def build_world():
    crops = [
        {
            "id": "cornsilk",
            "role": "fast",
            "family": "grain",
            "seed_cost": 5.0,
            "growth_days": 3,
            "min_yield": 2,
            "max_yield": 4,
            "base_price": 3.0,
            "price_variation": 0.1,
            "loss_chance": 0.05,
            "water_interval_days": 1,
            "nutrient_demand": {"nitrogen": 0.01, "phosphorus": 0.01, "potassium": 0.01},
            "unlock_requirement": None,
        },
        {
            "id": "purplehaze",
            "role": "standard",
            "family": "flower",
            "seed_cost": 20.0,
            "growth_days": 6,
            "min_yield": 2,
            "max_yield": 4,
            "base_price": 8.0,
            "price_variation": 0.1,
            "loss_chance": 0.1,
            "water_interval_days": 2,
            "nutrient_demand": {"nitrogen": 0.03, "phosphorus": 0.02, "potassium": 0.02},
            "unlock_requirement": None,
        },
        {
            "id": "goldenwheat",
            "role": "standard",
            "family": "grain",
            "seed_cost": 40.0,
            "growth_days": 10,
            "min_yield": 5,
            "max_yield": 9,
            "base_price": 6.0,
            "price_variation": 0.1,
            "loss_chance": 0.08,
            "water_interval_days": 3,
            "nutrient_demand": {"nitrogen": 0.02, "phosphorus": 0.02, "potassium": 0.02},
            "unlock_requirement": {"type": "total_revenue", "value": 500},
        },
    ]
    crops_by_id = {c["id"]: c for c in crops}

    products = [
        {"id": "flour", "base_price": 15.0, "price_variation": 0.1},
    ]
    items_by_id = dict(crops_by_id)
    items_by_id.update({p["id"]: p for p in products})

    upgrades = [
        {
            "id": "irrigation_system",
            "cost": 60.0,
            "effect": {"type": "growth_time_reduction", "amount": 0.2},
        },
        {
            "id": "storage_shed",
            "cost": 40.0,
            "effect": {"type": "capacity", "amount": 2},
        },
    ]
    upgrades_by_id = {u["id"]: u for u in upgrades}

    recipes = [
        {
            "id": "mill_cornsilk",
            "input_item_id": "cornsilk",
            "input_quantity": 3,
            "output_item_id": "flour",
            "output_quantity": 1,
            "processing_days": 2,
            "min_quality": "processing",
            "cost": 2.0,
        },
    ]

    channels = [
        {
            "id": "spot",
            "min_quality": "rejected",
            "min_reputation": 0,
            "price_multiplier": 1.0,
            "reputation_bonus": 0.002,
            "flat_fee": 0.0,
            "fee_rate": 0.0,
        },
        {
            "id": "farm_stand",
            "min_quality": "standard",
            "min_reputation": 0,
            "daily_capacity": 20,
            "price_multiplier": 1.4,
            "reputation_bonus": 0.002,
            "flat_fee": 1.0,
            "fee_rate": 0.02,
        },
    ]

    buyers = [
        {"id": "local_buyer"},
        {"id": "regional_buyer"},
    ]

    fertilizer_config = {"cost": 4.0, "loss_chance_reduction": 0.04, "yield_bonus_pct": 0.15}

    return {
        "crops": crops,
        "crops_by_id": crops_by_id,
        "products": products,
        "items_by_id": items_by_id,
        "upgrades": upgrades,
        "upgrades_by_id": upgrades_by_id,
        "recipes": recipes,
        "channels": channels,
        "buyers": buyers,
        "fertilizer_config": fertilizer_config,
    }


# --- Scenarios ---------------------------------------------------------------


def build_scenarios(world):
    crops_by_id = world["crops_by_id"]

    scenarios = []

    # A: base happy path -- an active under-delivered contract to exercise
    # choose_crop's contract-steering branch, inventory + offers + a
    # profitable processing recipe, goldenwheat unlocked.
    player = PlayerState(money=200.0, slots_total=4, day=10, total_days=60, operating_reserve=20.0)
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = world["upgrades_by_id"]
    player.contract_config = {}
    player.processing_recipes = world["recipes"]
    player.processing_capacity = 2
    player.market_channels = world["channels"]
    player.reputation = 50.0
    player.market_prices = {"cornsilk": 3.2, "purplehaze": 8.5, "flour": 15.5}
    player.crop_plant_counts = {"purplehaze": 3, "cornsilk": 1}
    player.total_revenue = 1200.0
    player.highest_money = 220.0
    player.total_planted = 15
    player.run_seed = 42
    player.planted = [
        PlantedCrop(
            "purplehaze", day_planted=4, growth_days_required=6, last_watered_day=9, plot_index=0
        )
    ]
    player.inventory_lots = [
        InventoryLot("purplehaze", 5, "standard"),
        InventoryLot("cornsilk", 6, "standard"),
        InventoryLot("flour", 2, "standard", item_type="product"),
    ]
    player.active_contracts = [
        ContractState(
            "active1", "local_buyer", "purplehaze", 6, "standard", 12.0, 0, 20, 0.15, delivered=2
        )
    ]
    player.contract_offers = [
        ContractState("offer1", "regional_buyer", "cornsilk", 5, "standard", 4.5, 8, 25, 0.1),
        ContractState("offer2", "local_buyer", "purplehaze", 8, "standard", 9.0, 8, 40, 0.15),
    ]
    scenarios.append(
        {
            "name": "base_happy_path",
            "player": player,
            "planted_index": 0,
            "upgrade_id": "irrigation_system",
            "crop_id": "purplehaze",
        }
    )

    # B: recovery mode -- low cash, nothing unlocked beyond the base two crops.
    player = PlayerState(money=12.0, slots_total=3, day=2, total_days=45, operating_reserve=10.0)
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = world["upgrades_by_id"]
    player.contract_config = {}
    player.processing_recipes = world["recipes"]
    player.processing_capacity = 1
    player.market_channels = world["channels"]
    player.reputation = 10.0
    player.market_prices = {"cornsilk": 3.0, "purplehaze": 8.0, "flour": 14.0}
    player.total_revenue = 0.0
    player.highest_money = 12.0
    player.total_planted = 0
    player.run_seed = 7
    player.planted = [
        PlantedCrop("cornsilk", day_planted=0, growth_days_required=3, last_watered_day=0)
    ]
    scenarios.append(
        {
            "name": "recovery_low_money",
            "player": player,
            "planted_index": 0,
            "upgrade_id": "irrigation_system",
            "crop_id": "cornsilk",
        }
    )

    # C: soil health critical -- forces economy_rules' triage branches
    # (best_crop_by_expected_profit's nutrient-demand fallback,
    # should_fertilize's maintenance-floor relaxation, soil_quality_risk's
    # same-family-replant penalty).
    player = PlayerState(money=150.0, slots_total=3, day=20, total_days=60, operating_reserve=15.0)
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = world["upgrades_by_id"]
    player.contract_config = {}
    player.processing_recipes = world["recipes"]
    player.processing_capacity = 1
    player.market_channels = world["channels"]
    player.reputation = 30.0
    player.market_prices = {"cornsilk": 3.1, "purplehaze": 8.2, "flour": 14.5}
    player.total_revenue = 600.0
    player.highest_money = 150.0
    player.total_planted = 8
    player.run_seed = 2024
    for plot in player.plots:
        plot.nitrogen = 0.1
        plot.phosphorus = 0.1
        plot.potassium = 0.1
    player.plots[0].previous_crop_family = "flower"
    player.planted = [
        PlantedCrop(
            "purplehaze", day_planted=18, growth_days_required=6, last_watered_day=19, plot_index=0
        )
    ]
    scenarios.append(
        {
            "name": "low_soil_health_critical",
            "player": player,
            "planted_index": 0,
            "upgrade_id": "storage_shed",
            "crop_id": "purplehaze",
        }
    )

    # D: upgrade cooldown + cumulative-spend cap both active.
    player = PlayerState(money=100.0, slots_total=3, day=15, total_days=60, operating_reserve=10.0)
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = world["upgrades_by_id"]
    player.contract_config = {}
    player.processing_recipes = world["recipes"]
    player.processing_capacity = 1
    player.market_channels = world["channels"]
    player.reputation = 20.0
    player.market_prices = {"cornsilk": 3.0, "purplehaze": 8.0, "flour": 14.0}
    player.upgrades_owned = {"irrigation_system"}
    player.upgrade_purchase_days = {"irrigation_system": 13}
    player.expenses_by_category = {"upgrades": 55.0}
    player.total_revenue = 300.0
    player.highest_money = 100.0
    player.total_planted = 5
    player.run_seed = 99
    player.planted = [
        PlantedCrop("cornsilk", day_planted=13, growth_days_required=3, last_watered_day=13)
    ]
    scenarios.append(
        {
            "name": "upgrade_cooldown_and_spend_cap",
            "player": player,
            "planted_index": 0,
            "upgrade_id": "storage_shed",
            "crop_id": "cornsilk",
        }
    )

    return scenarios


# --- Serialization helpers ---------------------------------------------------


def serialize_world(world):
    item_index = {}
    items = []
    for i, crop in enumerate(world["crops"]):
        item_index[crop["id"]] = i
        items.append(
            {
                "id": i,
                "type": "crop",
                "external_id": crop["id"],
                "base_price": crop["base_price"],
                "price_variation": crop["price_variation"],
            }
        )
    for product in world["products"]:
        i = len(items)
        item_index[product["id"]] = i
        items.append(
            {
                "id": i,
                "type": "product",
                "external_id": product["id"],
                "base_price": product["base_price"],
                "price_variation": product["price_variation"],
            }
        )

    crops = []
    for crop in world["crops"]:
        unlock = crop["unlock_requirement"]
        if unlock is None:
            unlock_json = {"type": "none"}
        elif unlock["type"] == "total_revenue":
            unlock_json = {"type": "total_revenue", "revenue_threshold": unlock["value"]}
        else:
            raise ValueError(f"unsupported unlock type in fixture world: {unlock['type']}")
        crops.append(
            {
                "item_id": item_index[crop["id"]],
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
                "unlock_requirement": unlock_json,
            }
        )

    upgrade_index = {u["id"]: i for i, u in enumerate(world["upgrades"])}
    upgrades = [
        {
            "id": upgrade_index[u["id"]],
            "external_id": u["id"],
            "cost": u["cost"],
            "effect": u["effect"],
        }
        for u in world["upgrades"]
    ]

    recipe_index = {r["id"]: i for i, r in enumerate(world["recipes"])}
    recipes = [
        {
            "id": recipe_index[r["id"]],
            "external_id": r["id"],
            "input_item_id": item_index[r["input_item_id"]],
            "output_item_id": item_index[r["output_item_id"]],
            "input_quantity": r["input_quantity"],
            "output_quantity": r["output_quantity"],
            "processing_days": r["processing_days"],
            "min_quality": r["min_quality"],
            "cost": r["cost"],
        }
        for r in world["recipes"]
    ]

    channel_index = {c["id"]: i for i, c in enumerate(world["channels"])}
    channels = [
        {
            "channel_id": channel_index[c["id"]],
            "external_id": c["id"],
            "min_quality": c.get("min_quality", "rejected"),
            "min_reputation": c.get("min_reputation", 0),
            "daily_capacity": c.get("daily_capacity"),
            "price_multiplier": c.get("price_multiplier", 1.0),
            "reputation_bonus": c.get("reputation_bonus", 0.002),
            "flat_fee": c.get("flat_fee", 0.0),
            "fee_rate": c.get("fee_rate", 0.0),
        }
        for c in world["channels"]
    ]

    buyer_index = {b["id"]: i for i, b in enumerate(world["buyers"])}
    buyers = [{"id": buyer_index[b["id"]], "external_id": b["id"]} for b in world["buyers"]]

    fert = world["fertilizer_config"]
    fertilizer = {
        "cost": fert["cost"],
        "loss_chance_reduction": fert["loss_chance_reduction"],
        "yield_bonus_pct": fert["yield_bonus_pct"],
    }
    contracts_config = {
        "fallback_price_multiplier": 1.15,
        "production_safety_factor": 0.45,
        "offer_expiry_days": 3,
    }

    indexes = {
        "item": item_index,
        "upgrade": upgrade_index,
        "recipe": recipe_index,
        "channel": channel_index,
        "buyer": buyer_index,
    }
    config_json = {
        "items": items,
        "crops": crops,
        "upgrades": upgrades,
        "recipes": recipes,
        "channels": channels,
        "buyers": buyers,
        "fertilizer": fertilizer,
        "contracts_config": contracts_config,
    }
    return config_json, indexes


def serialize_planted(planted, indexes):
    return {
        "crop_item_id": indexes["item"][planted.crop_id],
        "day_planted": planted.day_planted,
        "growth_days_required": planted.growth_days_required,
        "last_watered_day": planted.last_watered_day,
        "neglect_days": planted.neglect_days,
        "fertilized": planted.fertilized,
        "plot_index": planted.plot_index if planted.plot_index is not None else -1,
        "water_stress": planted.water_stress,
        "nutrient_stress": planted.nutrient_stress,
        "temperature_stress": planted.temperature_stress,
        "pest_stress": planted.pest_stress,
        "disease_stress": planted.disease_stress,
        "accrued_cost": planted.accrued_cost,
    }


def serialize_lot(lot, indexes):
    return {
        "item_id": indexes["item"][lot.item_id],
        "quantity": lot.quantity,
        "quality": lot.quality,
        "produced_day": lot.produced_day,
        "age_days": lot.age_days,
        "shelf_life_days": lot.shelf_life_days,
        "effective_shelf_life_days": lot.effective_shelf_life_days,
        "unit_cost": lot.unit_cost,
        "item_type": lot.item_type,
    }


def serialize_contract(contract, indexes):
    return {
        "buyer_id": indexes["buyer"][contract.buyer_id],
        "item_id": indexes["item"][contract.item_id],
        "quantity": contract.quantity,
        "delivered": contract.delivered,
        "min_quality": contract.min_quality,
        "unit_price": contract.unit_price,
        "penalty_rate": contract.penalty_rate,
        "offered_day": contract.offered_day,
        "deadline_day": contract.deadline_day,
        "accepted": contract.accepted,
        "resolved": contract.resolved,
    }


def serialize_plot(plot):
    return {
        "moisture": plot.moisture,
        "nitrogen": plot.nitrogen,
        "phosphorus": plot.phosphorus,
        "potassium": plot.potassium,
        "ph": plot.ph,
        "soil_health": plot.soil_health,
        "pest_pressure": plot.pest_pressure,
        "disease_pressure": plot.disease_pressure,
        "previous_crop_family": plot.previous_crop_family,
    }


def serialize_scenario(scenario, indexes):
    player = scenario["player"]
    return {
        "name": scenario["name"],
        "money": player.money,
        "operating_reserve": player.operating_reserve,
        "day": player.day,
        "total_days": player.total_days,
        "slots_total": player.slots_total,
        "plots": [serialize_plot(plot) for plot in player.plots],
        "planted": [serialize_planted(p, indexes) for p in player.planted],
        "inventory_lots": [serialize_lot(lot, indexes) for lot in player.inventory_lots],
        "processing_jobs": [],
        "seed_inventory": {
            str(indexes["item"][crop_id]): qty for crop_id, qty in player.seed_inventory.items()
        },
        "crop_plant_counts": {
            str(indexes["item"][crop_id]): count
            for crop_id, count in player.crop_plant_counts.items()
        },
        "upgrades_owned": [indexes["upgrade"][u] for u in sorted(player.upgrades_owned)],
        "upgrade_purchase_days": {
            str(indexes["upgrade"][u]): day for u, day in player.upgrade_purchase_days.items()
        },
        "active_contracts": [serialize_contract(c, indexes) for c in player.active_contracts],
        "contract_offers": [serialize_contract(c, indexes) for c in player.contract_offers],
        "buyer_relationships": {
            str(indexes["buyer"][b]): value for b, value in player.buyer_relationships.items()
        },
        "reputation": player.reputation,
        "market_prices": {
            str(indexes["item"][item_id]): price for item_id, price in player.market_prices.items()
        },
        "channel_capacity_used": {},
        "total_revenue": player.total_revenue,
        "expenses_by_category": dict(player.expenses_by_category),
        "highest_money": player.highest_money,
        "has_highest_money": player.highest_money is not None,
        "total_planted": player.total_planted,
        "run_seed": player.run_seed,
        "has_run_seed": player.run_seed is not None,
        "has_processing_capacity": player.processing_capacity is not None,
        "processing_capacity": player.processing_capacity
        if player.processing_capacity is not None
        else 0,
        "focus": {
            "planted_index": scenario["planted_index"],
            "upgrade_id": indexes["upgrade"][scenario["upgrade_id"]],
            "crop_item_id": indexes["item"][scenario["crop_id"]],
        },
    }


def quality_rank(quality):
    return QUALITY_ORDER[quality]


def build_cases(world, scenarios, indexes):
    cases = []
    fertilizer_config = world["fertilizer_config"]
    channels = world["channels"]
    items_by_id = world["items_by_id"]

    for scenario in scenarios:
        player = scenario["player"]
        crops = world["crops"]
        crops_by_id = world["crops_by_id"]
        upgrades_by_id = world["upgrades_by_id"]
        planted = player.planted[scenario["planted_index"]]
        crop_for_planted = crops_by_id[planted.crop_id]
        upgrade = upgrades_by_id[scenario["upgrade_id"]]
        crop_for_fertilizer = crops_by_id[scenario["crop_id"]]
        offer_index_by_id = {offer.id: i for i, offer in enumerate(player.contract_offers)}
        active_index_by_id = {contract.id: i for i, contract in enumerate(player.active_contracts)}
        recipe_index = indexes["recipe"]

        for agent_cls in AGENT_CLASSES:
            agent = agent_cls()
            recorded = {}

            crop = agent.choose_crop(copy.deepcopy(player), crops, crops_by_id, upgrades_by_id)
            recorded["choose_crop"] = None if crop is None else indexes["item"][crop["id"]]

            recorded["should_buy_upgrade"] = bool(
                agent.should_buy_upgrade(copy.deepcopy(player), upgrade)
            )

            recorded["should_water"] = bool(
                agent.should_water(copy.deepcopy(player), planted, crop_for_planted)
            )
            recorded["should_fertilize"] = bool(
                agent.should_fertilize(
                    copy.deepcopy(player), planted, crop_for_planted, fertilizer_config
                )
            )
            recorded["should_use_fertilizer"] = bool(
                agent.should_use_fertilizer(
                    copy.deepcopy(player), crop_for_fertilizer, fertilizer_config
                )
            )

            contract_player = copy.deepcopy(player)
            accepted_ids = agent.choose_contracts(contract_player, contract_player.contract_offers)
            recorded["choose_contracts"] = [offer_index_by_id[cid] for cid in accepted_ids]

            deliveries = agent.choose_contract_deliveries(copy.deepcopy(player))
            recorded["choose_contract_deliveries"] = [
                {"contract_id": active_index_by_id[d["contract_id"]], "quantity": d["quantity"]}
                for d in deliveries
            ]

            processing = agent.choose_processing(
                copy.deepcopy(player), world["recipes"], items_by_id
            )
            recorded["choose_processing"] = [
                {"recipe_id": recipe_index[d["recipe_id"]], "batches": d["batches"]}
                for d in processing
            ]

            sales = agent.choose_sales(copy.deepcopy(player), channels, items_by_id)
            recorded["choose_sales"] = sorted(
                (
                    {
                        "item_id": indexes["item"][s["item_id"]],
                        "channel_id": indexes["channel"][s["channel_id"]],
                        "quality": quality_rank(s["quality"]) if "quality" in s else None,
                        "quantity": s["quantity"],
                    }
                    for s in sales
                ),
                key=lambda s: (
                    s["item_id"],
                    s["channel_id"],
                    -1 if s["quality"] is None else s["quality"],
                ),
            )

            for method, expected in recorded.items():
                cases.append(
                    {
                        "scenario": scenario["name"],
                        "agent": agent_cls.name,
                        "method": method,
                        "expected": expected,
                    }
                )

    return cases


def main():
    world = build_world()
    scenarios = build_scenarios(world)
    config_json, indexes = serialize_world(world)
    scenarios_json = [serialize_scenario(s, indexes) for s in scenarios]
    cases = build_cases(world, scenarios, indexes)

    output = {"config": config_json, "scenarios": scenarios_json, "cases": cases}
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"Wrote {len(scenarios_json)} scenarios, {len(cases)} cases to {OUT_PATH}")


if __name__ == "__main__":
    main()
