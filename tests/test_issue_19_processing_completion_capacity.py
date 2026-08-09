"""Regression tests for issue #19: storage capacity must be enforced again
right after processing jobs complete, not just once at end-of-day aging.
age_and_spoil runs before processing.complete_jobs each day
(simulation/engine.py), so a completing job's output is new inventory added
after that check already ran -- without a second check, overflow it causes
wouldn't spoil until the following day, letting same-day agent actions
(sales, deliveries) use inventory that should already be gone.
"""

from main import load_config
from simulation import engine, inventory, processing
from simulation.random_events import RandomEvents
from simulation.state import InventoryLot, PlayerState, ProcessingJob


def make_player(money=100):
    player = PlayerState(money=money, slots_total=1)
    player.lowest_money = money
    player.highest_money = money
    return player


# -- direct unit coverage of the new helper ----------------------------------


def test_completing_output_into_full_storage_spoils_the_whole_output():
    player = make_player()
    player.inventory_lots.append(
        InventoryLot("greenleaf", 10, "standard", shelf_life_days=30, unit_cost=1.0)
    )
    player.processing_jobs.append(
        ProcessingJob(
            "dry", "dried_greenleaf", 4, completion_day=0, shelf_life_days=30, unit_cost=1.0
        )
    )
    player.day = 0

    completed = processing.complete_jobs(player)
    assert completed == 4
    assert sum(lot.quantity for lot in player.inventory_lots) == 14  # not yet trimmed

    # Which specific lot absorbs the trim is a FEFO ordering detail (#22),
    # not what #19 is about -- what matters here is that the four units of
    # overflow the completing job caused are gone and counted, immediately.
    spoiled = inventory.enforce_storage_capacity(player, capacity=10)

    assert spoiled == 4
    assert sum(lot.quantity for lot in player.inventory_lots) == 10
    assert player.total_spoiled == 4


def test_completing_output_into_partially_full_storage_spoils_only_the_overflow():
    player = make_player()
    player.inventory_lots.append(
        InventoryLot("greenleaf", 8, "standard", shelf_life_days=30, unit_cost=1.0)
    )
    player.processing_jobs.append(
        ProcessingJob(
            "dry", "dried_greenleaf", 4, completion_day=0, shelf_life_days=30, unit_cost=1.0
        )
    )
    player.day = 0

    processing.complete_jobs(player)
    spoiled = inventory.enforce_storage_capacity(player, capacity=10)

    # 8 + 4 == 12 against a capacity of 10 -- only the 2-unit excess spoils,
    # not the whole completing job's output.
    assert spoiled == 2
    assert sum(lot.quantity for lot in player.inventory_lots) == 10
    assert player.total_spoiled == 2


def test_completing_output_under_capacity_spoils_nothing():
    player = make_player()
    player.inventory_lots.append(
        InventoryLot("greenleaf", 2, "standard", shelf_life_days=30, unit_cost=1.0)
    )
    player.processing_jobs.append(
        ProcessingJob(
            "dry", "dried_greenleaf", 4, completion_day=0, shelf_life_days=30, unit_cost=1.0
        )
    )
    player.day = 0

    processing.complete_jobs(player)
    spoiled = inventory.enforce_storage_capacity(player, capacity=10)

    assert spoiled == 0
    assert sum(lot.quantity for lot in player.inventory_lots) == 6
    assert player.total_spoiled == 0


# -- engine-level: the real run_day wiring, not just the helper -------------


class _DoesNothing:
    """Stub agent that takes no action, so a day's outcome is driven purely
    by mechanics (weather, harvest, processing completion, storage), not by
    any decision this test would otherwise have to also account for.
    """

    watering_diligence = 0.0

    def choose_crop(self, *_args):
        return None

    def should_buy_upgrade(self, *_args):
        return False

    def should_water(self, *_args):
        return False

    def should_fertilize(self, *_args):
        return False

    def choose_contracts(self, *_args):
        return []

    def choose_contract_deliveries(self, *_args):
        return []

    def choose_processing(self, *_args):
        return []

    def choose_sales(self, *_args):
        return []

    def should_use_fertilizer(self, *_args):
        return False


def test_engine_enforces_capacity_the_same_day_a_processing_job_completes():
    """The issue's own example: a farm at capacity 10 with a completing
    four-unit job must not end the day holding 14 units with no spoilage.
    """
    crops, upgrades, _config, world = load_config()
    small_storage_world = dict(world, storage=dict(world["storage"], capacity=10))

    player = make_player(money=0)
    player.slots_total = 0
    player.plots = []
    player.day = 5
    player.inventory_lots.append(
        InventoryLot("greenleaf", 8, "standard", produced_day=0, shelf_life_days=30, unit_cost=1.0)
    )
    player.processing_jobs.append(
        ProcessingJob(
            "dry_greenleaf",
            "dried_greenleaf",
            4,
            completion_day=5,
            shelf_life_days=30,
            unit_cost=1.0,
        )
    )

    engine.run_day(
        player,
        _DoesNothing(),
        crops,
        {crop["id"]: crop for crop in crops},
        upgrades,
        {upgrade["id"]: upgrade for upgrade in upgrades},
        small_storage_world["watering"],
        small_storage_world["fertilizer"],
        RandomEvents(1),
        world=small_storage_world,
    )

    # 8 + 4 = 12 into a capacity-10 farm: 2 units must already be spoiled
    # and gone by the end of this same day, not still sitting in inventory
    # for tomorrow's aging pass to catch.
    assert sum(lot.quantity for lot in player.inventory_lots) <= 10
    assert player.total_spoiled >= 2
