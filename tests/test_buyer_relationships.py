"""Per-buyer relationship: repeat business with one buyer earns better
terms with that buyer specifically, independent of the global reputation
gate and of every other buyer -- see PlayerState.buyer_relationships and
simulation.contracts._relationship_price_multiplier.
"""

from simulation import contracts
from simulation.random_events import RandomEvents
from simulation.state import ContractState, InventoryLot, PlayerState

BUYER = {
    "id": "coop",
    "items": ["crop"],
    "quantity_range": [5, 5],
    "min_quality": "standard",
    "contract_price_multiplier": 2.0,
    "deadline_days": 10,
    "penalty_rate": 0.2,
    "relationship_bonus_rate": 0.05,
}
ITEMS_BY_ID = {"crop": {"base_price": 10.0}}


def make_player(money=100):
    player = PlayerState(money=money, slots_total=1)
    player.lowest_money = money
    player.highest_money = money
    player.crop_catalog = {}
    return player


def test_first_offer_from_a_buyer_carries_no_relationship_bonus():
    player = make_player()
    player.day = 7  # generate_offers only fires on offer_interval_days boundaries, never day 0
    offers = contracts.generate_offers(player, {}, [BUYER], ITEMS_BY_ID, RandomEvents(seed=1))
    assert offers[0].unit_price == 10.0 * 2.0


def test_completed_contract_builds_that_buyers_relationship_only():
    player = make_player()
    player.inventory_lots.append(InventoryLot("crop", 5, "premium"))
    offer = ContractState("c1", "coop", "crop", 5, "standard", 20, 0, 4, 0.25)
    player.contract_offers.append(offer)
    assert contracts.accept(player, "c1")

    contracts.deliver(player, "c1", 5)

    assert player.buyer_relationships["coop"] == contracts.DEFAULT_RELATIONSHIP_GAIN
    assert "other_buyer" not in player.buyer_relationships


def test_relationship_raises_the_next_offer_price_from_that_buyer():
    player = make_player()
    player.day = 7
    player.buyer_relationships["coop"] = 3.0  # 3 * 0.05 bonus_rate = 0.15, under the 0.25 cap

    offers = contracts.generate_offers(player, {}, [BUYER], ITEMS_BY_ID, RandomEvents(seed=1))

    assert offers[0].unit_price == 10.0 * 2.0 * 1.15


def test_relationship_price_bonus_is_capped():
    player = make_player()
    player.day = 7
    player.buyer_relationships["coop"] = 1000.0  # far past any reasonable cap

    offers = contracts.generate_offers(
        player, {"relationship_bonus_cap": 0.1}, [BUYER], ITEMS_BY_ID, RandomEvents(seed=1)
    )

    assert offers[0].unit_price == 10.0 * 2.0 * 1.1


def test_expired_contract_erodes_that_buyers_relationship():
    player = make_player()
    player.buyer_relationships["coop"] = 8.0
    contract = ContractState(
        "c1", "coop", "crop", 5, "standard", 20, 0, deadline_day=0, penalty_rate=0.2
    )
    player.active_contracts.append(contract)
    player.day = 1  # past the deadline

    contracts.resolve_expired(player)

    assert player.buyer_relationships["coop"] == 8.0 - contracts.DEFAULT_RELATIONSHIP_LOSS


def test_relationship_gain_and_loss_are_configurable():
    player = make_player()
    player.inventory_lots.append(InventoryLot("crop", 5, "premium"))
    offer = ContractState("c1", "coop", "crop", 5, "standard", 20, 0, 4, 0.25)
    player.contract_offers.append(offer)
    player.contract_config = {"relationship_gain_per_delivery": 1.5}
    assert contracts.accept(player, "c1")

    contracts.deliver(player, "c1", 5)

    assert player.buyer_relationships["coop"] == 1.5


def test_relationship_never_goes_negative():
    player = make_player()
    player.buyer_relationships["coop"] = 2.0
    player.contract_config = {"relationship_loss_per_failure": 5.0}
    contract = ContractState(
        "c1", "coop", "crop", 5, "standard", 20, 0, deadline_day=0, penalty_rate=0.2
    )
    player.active_contracts.append(contract)
    player.day = 1

    contracts.resolve_expired(player)

    assert player.buyer_relationships["coop"] == 0.0
