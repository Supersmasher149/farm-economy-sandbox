"""Regression test for issue #22: inventory consumption is FEFO (nearest
expiry first), not FIFO (insertion order) -- and that is the intentional,
economically-correct policy (it minimizes spoilage loss), not a bug. The
module docstring in simulation/inventory.py used to claim FIFO; #22 was
resolved by correcting that docstring rather than the behavior. This test
pins the actual (FEFO) consumption order so a future refactor can't silently
flip it back to FIFO without a test noticing.
"""

from simulation import inventory
from simulation.state import InventoryLot, PlayerState


def test_consume_draws_the_nearest_to_expiry_lot_first_even_if_produced_later():
    player = PlayerState(money=0, slots_total=1)
    # Older lot (produced day 0), but with more shelf life remaining than
    # the newer one -- a plain FIFO policy would drain this one first.
    older_longer_lived = InventoryLot(
        "greenleaf", quantity=5, produced_day=0, shelf_life_days=30, unit_cost=1.0
    )
    # Newer lot (produced day 5), but expiring sooner.
    newer_soon_to_expire = InventoryLot(
        "greenleaf", quantity=5, produced_day=5, shelf_life_days=3, unit_cost=2.0
    )
    player.inventory_lots = [older_longer_lived, newer_soon_to_expire]

    consumed, cost = inventory.consume(player, "greenleaf", 5, "rejected")

    assert consumed == 5
    # FEFO: the soon-to-expire (newer) lot is drained first, at its own
    # unit cost -- not the older, longer-lived lot FIFO would have picked.
    assert cost == 5 * 2.0
    remaining = [lot for lot in player.inventory_lots if lot.item_id == "greenleaf"]
    assert len(remaining) == 1
    assert remaining[0] is older_longer_lived
    assert remaining[0].quantity == 5


def test_consume_spans_lots_in_expiry_order_once_the_first_is_exhausted():
    player = PlayerState(money=0, slots_total=1)
    soonest = InventoryLot(
        "greenleaf", quantity=2, produced_day=0, shelf_life_days=2, unit_cost=1.0
    )
    middle = InventoryLot("greenleaf", quantity=2, produced_day=1, shelf_life_days=5, unit_cost=2.0)
    latest = InventoryLot("greenleaf", quantity=2, produced_day=2, shelf_life_days=9, unit_cost=3.0)
    # Insertion order deliberately does not match expiry order.
    player.inventory_lots = [latest, soonest, middle]

    consumed, cost = inventory.consume(player, "greenleaf", 3, "rejected")

    assert consumed == 3
    # 2 units at cost 1.0 (soonest) + 1 unit at cost 2.0 (middle).
    assert cost == 2 * 1.0 + 1 * 2.0
