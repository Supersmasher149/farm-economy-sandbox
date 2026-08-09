"""Regression tests for issue #21: every public transaction boundary that
accepts a caller-supplied quantity must reject fractional, non-finite
(NaN/inf), boolean, zero, and negative values *before* mutating any state --
not just quietly misbehave (fractional inventory, NaN money, `True` treated
as quantity 1).

Each case below asserts both the reject-shaped return value AND that no
state was touched, since "returns falsy but still corrupted something" would
still be the bug.
"""

import math

import pytest

from simulation import actions, contracts, inventory, markets
from simulation.state import ContractState, InventoryLot, PlayerState

INVALID_QUANTITIES = [1.5, float("nan"), float("inf"), float("-inf"), 0, -1, -1.5, True]


def _ids(value):
    if isinstance(value, bool):
        return f"bool-{value}"
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return repr(value)


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_buy_seeds_rejects_invalid_quantity(quantity):
    player = PlayerState(money=100, slots_total=1)
    crop = {"id": "quickweed", "seed_cost": 5}

    assert actions.buy_seeds(player, crop, quantity) is False
    assert player.money == 100
    assert player.seed_inventory == {}
    assert player.total_expenses == 0.0


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_buy_fertilizer_rejects_invalid_quantity(quantity):
    player = PlayerState(money=100, slots_total=1)
    fertilizer_config = {"cost": 8}

    assert actions.buy_fertilizer(player, fertilizer_config, quantity) is False
    assert player.money == 100
    assert player.fertilizer_inventory == 0
    assert player.total_fertilizer_bought == 0


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_inventory_consume_rejects_invalid_quantity(quantity):
    player = PlayerState(money=0, slots_total=1)
    lot = InventoryLot("greenleaf", 5, "standard", unit_cost=1.0)
    player.inventory_lots = [lot]

    consumed, cost = inventory.consume(player, "greenleaf", quantity)

    assert (consumed, cost) == (0, 0.0)
    assert lot.quantity == 5
    assert player.inventory_lots == [lot]


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_market_quote_rejects_invalid_quantity(quantity):
    player = PlayerState(money=0, slots_total=1)
    player.market_prices = {"greenleaf": 5.0}
    channel = {"id": "spot"}

    assert markets.quote(player, "greenleaf", "standard", channel, quantity) is None


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_market_sell_rejects_invalid_quantity(quantity):
    player = PlayerState(money=0, slots_total=1)
    player.market_prices = {"greenleaf": 5.0}
    lot = InventoryLot("greenleaf", 5, "standard", unit_cost=1.0)
    player.inventory_lots = [lot]
    channel = {"id": "spot"}

    revenue, sold = markets.sell(player, "greenleaf", quantity, channel)

    assert (revenue, sold) == (0.0, 0)
    assert lot.quantity == 5
    assert player.money == 0
    assert player.total_revenue == 0.0


@pytest.mark.parametrize("quantity", INVALID_QUANTITIES, ids=_ids)
def test_contract_deliver_rejects_invalid_quantity(quantity):
    player = PlayerState(money=0, slots_total=1)
    contract = ContractState("c1", "local", "greenleaf", 5, "standard", 10.0, 0, 10, 0.1)
    player.active_contracts = [contract]
    lot = InventoryLot("greenleaf", 5, "standard", unit_cost=1.0)
    player.inventory_lots = [lot]

    revenue, delivered = contracts.deliver(player, "c1", quantity)

    assert (revenue, delivered) == (0.0, 0)
    assert contract.delivered == 0
    assert lot.quantity == 5
    assert player.money == 0


# -- sanity: a genuinely valid quantity still works through each API -------


def test_buy_seeds_accepts_a_valid_quantity():
    player = PlayerState(money=100, slots_total=1)
    crop = {"id": "quickweed", "seed_cost": 5}
    assert actions.buy_seeds(player, crop, 2) is True
    assert player.seed_inventory == {"quickweed": 2}


def test_inventory_consume_accepts_a_valid_quantity():
    player = PlayerState(money=0, slots_total=1)
    player.inventory_lots = [InventoryLot("greenleaf", 5, "standard", unit_cost=1.0)]
    consumed, cost = inventory.consume(player, "greenleaf", 3)
    assert (consumed, cost) == (3, 3.0)
