import copy

import pytest

from agents.fertilizer_maximalist import FertilizerMaximalist
from agents.neglectful_grower import NeglectfulGrower
from agents.no_upgrade_player import NoUpgradePlayer
from agents.profit_optimizer import ProfitOptimizer
from simulation.state import ContractState, InventoryLot, PlantedCrop, PlayerState


def policy_inputs(fertilizer_config):
    crops = [
        {
            "id": "quickweed",
            "seed_cost": 5,
            "growth_days": 3,
            "min_yield": 1,
            "max_yield": 2,
            "base_price": 5,
            "loss_chance": 0.03,
            "water_interval_days": 2,
            "unlock_requirement": None,
        },
        {
            "id": "greenleaf",
            "seed_cost": 18,
            "growth_days": 7,
            "min_yield": 4,
            "max_yield": 6,
            "base_price": 7,
            "loss_chance": 0.05,
            "water_interval_days": 3,
            "unlock_requirement": None,
        },
    ]
    crops_by_id = {crop["id"]: crop for crop in crops}
    player = PlayerState(money=100, slots_total=3, day=4, total_days=30, operating_reserve=10)
    player.highest_money = player.money
    player.market_prices = {"quickweed": 5, "greenleaf": 7, "flour": 20}
    player.market_channels = [
        {
            "id": "farm_stand",
            "min_quality": "standard",
            "price_multiplier": 1.45,
            "daily_capacity": 20,
            "flat_fee": 1,
        }
    ]
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = {}
    player.contract_config = {}
    player.inventory_lots = [
        InventoryLot("greenleaf", 4, "standard"),
        InventoryLot("flour", 1, "standard", item_type="processed"),
    ]
    player.active_contracts = [
        ContractState("active", "local", "greenleaf", 2, "standard", 11, 0, 14, 0.1)
    ]
    planted = PlantedCrop("greenleaf", day_planted=0, growth_days_required=7)
    recipes = [
        {
            "id": "mill_greenleaf",
            "input_item_id": "greenleaf",
            "input_quantity": 2,
            "output_item_id": "flour",
            "output_quantity": 1,
            "cost": 1,
            "min_quality": "processing",
        }
    ]
    offers = [ContractState("offer", "regional", "greenleaf", 6, "standard", 11, 4, 18, 0.1)]
    upgrade = {"id": "capacity_1", "cost": 20, "effect": {"type": "capacity", "amount": 1}}
    return player, crops, crops_by_id, planted, recipes, offers, upgrade, fertilizer_config


@pytest.mark.parametrize(
    "control_cls, intentional_methods",
    [
        (NeglectfulGrower, set()),
        (NoUpgradePlayer, {"should_buy_upgrade"}),
        (FertilizerMaximalist, {"should_use_fertilizer", "should_fertilize"}),
    ],
)
def test_control_agents_match_optimizer_for_non_control_decisions(
    control_cls, intentional_methods, fertilizer_config
):
    (
        player,
        crops,
        crops_by_id,
        planted,
        recipes,
        offers,
        upgrade,
        fertilizer_config,
    ) = policy_inputs(fertilizer_config)
    optimizer = ProfitOptimizer()
    control = control_cls()
    contract_player = copy.deepcopy(player)
    contract_player.active_contracts = []

    decisions = {
        "choose_crop": (player, crops, crops_by_id, {}),
        "should_water": (player, planted, crops_by_id["greenleaf"]),
        "should_fertilize": (player, planted, crops_by_id["greenleaf"], fertilizer_config),
        "choose_contracts": (contract_player, offers),
        "choose_contract_deliveries": (player,),
        "choose_processing": (player, recipes, {"greenleaf": {}, "flour": {}}),
        "choose_sales": (player, player.market_channels, {}),
        "should_buy_upgrade": (player, upgrade),
        "should_use_fertilizer": (player, crops_by_id["greenleaf"], fertilizer_config),
    }

    for method_name, args in decisions.items():
        if method_name not in intentional_methods:
            assert getattr(control, method_name)(*args) == getattr(optimizer, method_name)(*args)


def test_neglectful_grower_only_changes_watering_diligence():
    assert NeglectfulGrower().watering_diligence == pytest.approx(0.15)
    assert NeglectfulGrower.choose_crop is ProfitOptimizer.choose_crop
    assert NeglectfulGrower.should_buy_upgrade is ProfitOptimizer.should_buy_upgrade


def test_no_upgrade_player_only_rejects_optimizer_upgrade_decisions(fertilizer_config):
    player, *_rest = policy_inputs(fertilizer_config)
    upgrade = {"id": "capacity_1", "cost": 20, "effect": {"type": "capacity", "amount": 1}}

    assert NoUpgradePlayer().should_buy_upgrade(player, upgrade) is False
    assert NoUpgradePlayer.choose_crop is ProfitOptimizer.choose_crop
    assert NoUpgradePlayer.should_use_fertilizer is ProfitOptimizer.should_use_fertilizer


def test_fertilizer_maximalist_only_changes_fertilizer_decisions(fertilizer_config):
    player, crops, crops_by_id, planted, *_rest = policy_inputs(fertilizer_config)
    crop = crops_by_id["quickweed"]

    assert FertilizerMaximalist().should_use_fertilizer(player, crop, fertilizer_config) is True
    assert FertilizerMaximalist().should_fertilize(player, planted, crop, fertilizer_config) is True
    assert FertilizerMaximalist.choose_crop is ProfitOptimizer.choose_crop
    assert FertilizerMaximalist.should_buy_upgrade is ProfitOptimizer.should_buy_upgrade
