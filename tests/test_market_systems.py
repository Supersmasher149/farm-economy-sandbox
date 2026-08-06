import pytest

from agents.profit_optimizer import ProfitOptimizer
from main import load_config
from runner.single_run import run_single
from simulation import contracts, engine, inventory, markets, processing
from simulation.random_events import RandomEvents
from simulation.state import ContractState, InventoryLot, PlayerState


def make_player(money=100):
    player = PlayerState(money=money, slots_total=1)
    player.lowest_money = money
    player.highest_money = money
    return player


def test_inventory_downgrades_then_spoils():
    player = make_player()
    player.inventory_lots.append(InventoryLot("crop", 4, "premium", shelf_life_days=2))
    player.day = 1
    assert inventory.age_and_spoil(player, {"capacity": 10, "shelf_life_multiplier": 1}) == 0
    assert player.inventory_lots[0].quality == "standard"
    assert inventory.age_and_spoil(player, {"capacity": 10, "shelf_life_multiplier": 1}) == 4
    assert player.total_spoiled == 4


def test_storage_liability_is_captured_before_inventory_is_sold():
    player = make_player(money=0)
    player.inventory_lots.append(InventoryLot("crop", 1, "standard"))
    liability = inventory.capture_storage_liability(player, {"daily_cost": 0.25})
    player.inventory_lots.clear()
    player.money = 10

    assert liability == 0.25
    assert inventory.collect_storage_liability(player, liability) == 0.25
    assert player.money == 9.75
    assert player.expenses_by_category["storage"] == 0.25


def test_engine_collects_storage_after_same_day_market_revenue():
    crops, upgrades, _config, world = load_config()
    player = make_player(money=0)
    player.slots_total = 0
    player.plots = []
    player.inventory_lots.append(InventoryLot("quickweed", 1, "standard"))

    class SellsOnly:
        watering_diligence = 0.0

        def choose_contracts(self, _player, _offers):
            return []

        def choose_contract_deliveries(self, _player):
            return []

        def choose_processing(self, _player, _recipes, _items):
            return []

        def choose_sales(self, _player, _channels, _items):
            return [{"item_id": "quickweed", "quantity": 1, "channel_id": "spot"}]

        def should_buy_upgrade(self, _player, _upgrade):
            return False

        def should_water(self, _player, _planted, _crop):
            return False

        def should_fertilize(self, _player, _planted, _crop, _fertilizer):
            return False

        def choose_crop(self, _player, _crops, _crops_by_id, _upgrades_by_id):
            return None

    engine.run_day(
        player,
        SellsOnly(),
        crops,
        {crop["id"]: crop for crop in crops},
        upgrades,
        {upgrade["id"]: upgrade for upgrade in upgrades},
        world["watering"],
        world["fertilizer"],
        RandomEvents(1),
        world=world,
    )

    assert player.total_revenue > 0
    assert player.expenses_by_category["storage"] == 0.25


def test_storage_overflow_is_recomputed_after_expiration():
    player = make_player()
    player.day = 2
    player.inventory_lots.extend(
        [
            InventoryLot("old", 20, shelf_life_days=1, produced_day=0),
            InventoryLot("fresh", 90, shelf_life_days=10, produced_day=1),
        ]
    )
    assert inventory.age_and_spoil(player, {"capacity": 100}) == 20
    assert inventory.available_quantity(player, "fresh") == 90


def test_specialty_channel_requires_quality_and_reputation():
    player = make_player()
    player.market_prices = {"crop": 10}
    specialty = {
        "id": "specialty",
        "min_quality": "premium",
        "min_reputation": 20,
        "price_multiplier": 1.5,
        "daily_capacity": 10,
    }
    assert markets.quote(player, "crop", "premium", specialty, 2) is None
    player.reputation = 20
    assert markets.quote(player, "crop", "standard", specialty, 2) is None
    assert markets.quote(player, "crop", "premium", specialty, 2)["net"] == pytest.approx(42.12)


def test_market_sale_consumes_inventory_and_records_channel():
    player = make_player()
    player.market_prices = {"crop": 10}
    player.inventory_lots.append(InventoryLot("crop", 5, "standard"))
    channel = {
        "id": "spot",
        "min_quality": "processing",
        "price_multiplier": 1,
        "daily_capacity": 10,
    }
    revenue, sold = markets.sell(player, "crop", 3, channel)
    assert (revenue, sold) == (30, 3)
    assert inventory.available_quantity(player, "crop") == 2
    assert player.revenue_by_channel == {"spot": 30}


def test_market_flat_fee_is_charged_once_for_multiple_lots():
    player = make_player()
    player.market_prices = {"crop": 10}
    player.inventory_lots.extend(
        [
            InventoryLot("crop", 1, "standard"),
            InventoryLot("crop", 1, "standard"),
        ]
    )
    channel = {
        "id": "wholesale",
        "min_quality": "standard",
        "price_multiplier": 1,
        "daily_capacity": 10,
        "flat_fee": 2,
    }
    assert markets.sell(player, "crop", 2, channel) == (18, 2)


def test_market_rejects_sale_when_fee_exceeds_revenue():
    player = make_player()
    player.market_prices = {"crop": 1}
    player.inventory_lots.append(InventoryLot("crop", 1, "standard"))
    channel = {
        "id": "wholesale",
        "min_quality": "standard",
        "price_multiplier": 1,
        "daily_capacity": 10,
        "flat_fee": 2,
    }
    assert markets.sell(player, "crop", 1, channel) == (0, 0)
    assert inventory.available_quantity(player, "crop") == 1


def test_contract_delivery_completes_and_builds_reputation():
    player = make_player()
    player.inventory_lots.append(InventoryLot("crop", 5, "premium"))
    offer = ContractState("c1", "buyer", "crop", 5, "standard", 12, 0, 4, 0.25)
    player.contract_offers.append(offer)
    assert contracts.accept(player, "c1")
    revenue, delivered = contracts.deliver(player, "c1", 5)
    assert (revenue, delivered) == (60, 5)
    assert player.contracts_completed == 1
    assert player.reputation == 5


def test_processing_consumes_inputs_and_completes_later():
    player = make_player()
    player.inventory_lots.append(InventoryLot("crop", 4, "standard", unit_cost=2))
    recipe = {
        "id": "dry",
        "input_item_id": "crop",
        "input_quantity": 4,
        "min_quality": "processing",
        "output_item_id": "dried",
        "output_quantity": 1,
        "processing_days": 2,
        "cost": 3,
        "shelf_life_days": 20,
    }
    assert processing.start_job(player, recipe, 1, capacity=1)
    assert inventory.available_quantity(player, "crop") == 0
    player.day = 1
    assert processing.complete_jobs(player) == 0
    player.day = 2
    assert processing.complete_jobs(player) == 1
    assert player.inventory_lots[0].item_id == "dried"


def test_full_world_run_is_deterministic_and_uses_market_channels():
    crops, upgrades, config, world = load_config()
    p1, _, _ = run_single(
        config,
        ProfitOptimizer(),
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        seed=901,
        world=world,
    )
    p2, _, _ = run_single(
        config,
        ProfitOptimizer(),
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        seed=901,
        world=world,
    )
    assert p1.money == p2.money
    assert p1.quality_harvested == p2.quality_harvested
    assert p1.revenue_by_channel == p2.revenue_by_channel
    assert p1.revenue_by_channel
