from agents.profit_optimizer import ProfitOptimizer
from agents.random_agent import RandomAgent
from main import load_config
from simulation import contracts, engine, markets
from simulation.random_events import RandomEvents
from simulation.state import ContractState, InventoryLot, PlantedCrop, PlayerState, ProcessingJob


def offer(offer_id="offer", item_id="crop", quantity=1, min_quality="standard"):
    return ContractState(offer_id, "buyer", item_id, quantity, min_quality, 10, 0, 10, 0.1)


def test_retained_offers_are_visible_until_expiry_and_acceptance_rechecks_expiry():
    player = PlayerState(money=100, slots_total=1)
    retained = offer()
    player.contract_offers.append(retained)

    player.day = 3
    assert contracts.visible_offers(player) == [retained]
    player.day = 4
    assert contracts.visible_offers(player) == []
    assert not contracts.accept(player, retained.id)
    assert player.contract_offers == []


def test_engine_passes_retained_offers_to_agent_hook():
    crops, upgrades, _config, world = load_config()
    player = PlayerState(money=100, slots_total=0)
    player.day = 1
    player.lowest_money = player.money
    player.highest_money = player.money
    retained = offer("retained", "quickweed", quantity=1)
    retained.offered_day = 1
    player.contract_offers.append(retained)
    seen = []

    class RecordsOffers:
        watering_diligence = 0.0

        def choose_contracts(self, _player, offers):
            seen.extend(offers)
            return []

        def choose_contract_deliveries(self, _player):
            return []

        def choose_processing(self, _player, _recipes, _items):
            return []

        def choose_sales(self, _player, _channels, _items):
            return []

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
        RecordsOffers(),
        crops,
        {crop["id"]: crop for crop in crops},
        upgrades,
        {upgrade["id"]: upgrade for upgrade in upgrades},
        world["watering"],
        world["fertilizer"],
        RandomEvents(1),
        world=world,
    )

    assert [contract.id for contract in seen] == ["retained"]


def test_contract_feasibility_uses_quality_age_and_existing_inventory():
    player = PlayerState(money=0, slots_total=1)
    player.inventory_lots.extend(
        [
            InventoryLot("crop", 4, "standard", shelf_life_days=5),
            InventoryLot("crop", 4, "premium", shelf_life_days=5, age_days=5),
        ]
    )
    assert contracts.available_quantity(player, "crop", "standard") == 4
    assert contracts.is_offer_feasible(player, offer(quantity=4))
    assert not contracts.is_offer_feasible(player, offer(quantity=5))
    assert not contracts.is_offer_feasible(player, offer(quantity=1, min_quality="premium"))


def test_contract_feasibility_uses_seed_inventory_and_processing_capacity():
    player = PlayerState(money=0, slots_total=1)
    crop = {
        "id": "crop",
        "seed_cost": 10,
        "growth_days": 2,
        "min_yield": 4,
        "max_yield": 4,
        "loss_chance": 0.0,
    }
    player.crop_catalog = {"crop": crop}
    player.seed_inventory["crop"] = 1
    player.contract_config = {"production_safety_factor": 1.0}
    assert contracts.producible_quantity(player, offer(quantity=4)) >= 4

    player.planted.append(PlantedCrop("crop", day_planted=0, growth_days_required=2))
    player.seed_inventory.clear()
    assert contracts.is_offer_feasible(player, offer("planted-offer", "crop", 4))

    player = PlayerState(money=0, slots_total=1)
    player.inventory_lots.append(InventoryLot("crop", 4, "standard"))
    player.processing_capacity = 1
    player.processing_recipes = [
        {
            "id": "process",
            "input_item_id": "crop",
            "input_quantity": 4,
            "min_quality": "processing",
            "output_item_id": "product",
            "output_quantity": 1,
            "cost": 0,
        }
    ]
    assert contracts.is_offer_feasible(player, offer("product-offer", "product"))

    player.processing_jobs.append(ProcessingJob("process", "product", 1, 5, 10, 0))
    player.day = 1
    assert contracts.is_offer_feasible(player, offer("job-offer", "product"))


def test_random_agent_uses_run_seed_without_consuming_event_rng():
    crops = [
        {"id": "a", "seed_cost": 1, "unlock_requirement": None},
        {"id": "b", "seed_cost": 1, "unlock_requirement": None},
    ]
    agent = RandomAgent()
    first = PlayerState(money=10, slots_total=1, run_seed=1)
    second = PlayerState(money=10, slots_total=1, run_seed=2)
    assert agent.choose_crop(first, crops, {}, {}) != agent.choose_crop(second, crops, {}, {})

    rng = RandomEvents(123)
    expected = RandomEvents(123).roll_yield(1, 100)
    agent.should_buy_upgrade(first, {"id": "upgrade"})
    assert rng.roll_yield(1, 100) == expected


def test_quality_constrained_market_sale_consumes_only_requested_tier():
    player = PlayerState(money=0, slots_total=1)
    player.market_prices = {"crop": 10}
    player.inventory_lots.extend(
        [
            InventoryLot("crop", 2, "standard"),
            InventoryLot("crop", 2, "premium"),
        ]
    )
    channel = {"id": "spot", "min_quality": "processing", "daily_capacity": 10}
    _revenue, sold = markets.sell(player, "crop", 1, channel, quality="premium")
    assert sold == 1
    assert sum(lot.quantity for lot in player.inventory_lots if lot.quality == "premium") == 1
    assert sum(lot.quantity for lot in player.inventory_lots if lot.quality == "standard") == 2


def test_optimizer_plans_quality_tiers_across_capacity_and_fallback_channels():
    player = PlayerState(money=0, slots_total=1)
    player.market_prices = {"crop": 10}
    player.inventory_lots = [InventoryLot("crop", 5, "premium")]
    channels = [
        {"id": "premium", "min_quality": "premium", "price_multiplier": 2, "daily_capacity": 2},
        {"id": "spot", "min_quality": "processing", "price_multiplier": 1, "daily_capacity": 10},
    ]
    decisions = ProfitOptimizer().choose_sales(player, channels, {})
    assert {(d["channel_id"], d["quality"], d["quantity"]) for d in decisions} == {
        ("premium", "premium", 2),
        ("spot", "premium", 3),
    }
