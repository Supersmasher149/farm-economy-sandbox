"""Regression tests for issue #24: upgrade-budget gating must see a same-day
cash peak (a sale, a contract delivery, ...) immediately, not only after
engine._finish_day's once-per-day highest_money update -- and whatever peak
gets reported must be the same one the gate used.
"""
import pytest

from simulation import contracts, economy_rules, markets
from simulation.actions import sell_all
from simulation.random_events import RandomEvents
from simulation.state import ContractState, InventoryLot, PlayerState


def make_player(money=100, highest_money=None):
    player = PlayerState(money=money, slots_total=1)
    player.lowest_money = money
    player.highest_money = money if highest_money is None else highest_money
    return player


# -- PlayerState.track_peak_cash ---------------------------------------------

def test_track_peak_cash_raises_recorded_peak():
    player = make_player(money=100, highest_money=100)
    player.money = 500
    player.track_peak_cash()
    assert player.highest_money == 500


def test_track_peak_cash_never_lowers_recorded_peak():
    player = make_player(money=500, highest_money=500)
    player.money = 100
    player.track_peak_cash()
    assert player.highest_money == 500


def test_track_peak_cash_initializes_from_none():
    player = PlayerState(money=42, slots_total=1)  # highest_money left at its None default
    player.track_peak_cash()
    assert player.highest_money == 42


# -- revenue sites update the peak live --------------------------------------

def test_market_sale_updates_peak_immediately():
    player = make_player(money=10, highest_money=10)
    player.market_prices = {"crop": 100}
    player.inventory_lots.append(InventoryLot("crop", 5, "standard"))
    channel = {"id": "spot", "min_quality": "processing", "price_multiplier": 1, "daily_capacity": 10}

    markets.sell(player, "crop", 5, channel)

    assert player.money == pytest.approx(510)
    assert player.highest_money == pytest.approx(510)


def test_legacy_sell_all_updates_peak_immediately():
    player = make_player(money=10, highest_money=10)
    crop = {"id": "crop", "base_price": 100, "price_variation": 0.0}
    player.crop_inventory["crop"] = 3
    player.inventory_lots.append(InventoryLot("crop", 3, "standard"))

    sell_all(player, {"crop": crop}, RandomEvents(1))

    assert player.money > 10
    assert player.highest_money == pytest.approx(player.money)


def test_contract_delivery_updates_peak_immediately():
    player = make_player(money=10, highest_money=10)
    player.inventory_lots.append(InventoryLot("crop", 5, "standard"))
    player.rebuild_crop_inventory()
    contract = ContractState("c", "buyer", "crop", 5, "standard", 100, 0, 10, 0.1)
    player.active_contracts.append(contract)

    contracts.deliver(player, "c", 5)

    assert player.money == pytest.approx(510)
    assert player.highest_money == pytest.approx(510)


# -- the gate itself ----------------------------------------------------------

def test_upgrade_gate_uses_same_day_sale_peak_not_stale_day_start_peak():
    """The issue's exact reproduction: current money 500, recorded highest
    money 100 -- rejected under the old gate, accepted once the peak is
    correct. Here the peak is stale because it was set directly (as a
    hand-built unit test does) rather than through a revenue call, so this
    also exercises the gate's own max() floor, not just track_peak_cash.
    """
    player = make_player(money=500, highest_money=100)
    upgrade = {"id": "u", "cost": 250, "effect": {"type": "capacity", "amount": 1}}

    assert economy_rules.should_buy_upgrade_within_budget(player, upgrade)


def test_upgrade_gate_sees_peak_from_a_sale_made_earlier_the_same_day():
    """End-to-end version of the same scenario: the peak comes from an
    actual same-day sale (via track_peak_cash), checked before any day-end
    bookkeeping (engine._finish_day) runs.
    """
    player = make_player(money=100, highest_money=100)
    player.market_prices = {"crop": 100}
    player.inventory_lots.append(InventoryLot("crop", 4, "standard"))
    channel = {"id": "spot", "min_quality": "processing", "price_multiplier": 1, "daily_capacity": 10}
    markets.sell(player, "crop", 4, channel)  # money: 100 -> 500, mid-day

    upgrade = {"id": "u", "cost": 250, "effect": {"type": "capacity", "amount": 1}}
    assert economy_rules.should_buy_upgrade_within_budget(player, upgrade)


def test_upgrade_gate_still_rejects_when_genuinely_over_cap():
    player = make_player(money=100, highest_money=100)
    upgrade = {"id": "u", "cost": 250, "effect": {"type": "capacity", "amount": 1}}

    assert not economy_rules.should_buy_upgrade_within_budget(player, upgrade)
