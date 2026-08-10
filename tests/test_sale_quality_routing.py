"""Regression tests for CQ-04: the engine discarded the quality that sales
routing had selected.

`Agent.route_sales_by_best_price` prices and capacity-plans each route for
one exact grade -- walking premium stock into the premium-paying channel
first is the whole point of the tiered channels. `engine.run_day` passed
only item, quantity and channel to `markets.sell`, so execution fell back to
"anything at or above the channel's minimum" and drew FEFO instead: a route
quoted for premium stock could be filled from a standard lot, and vice
versa. `markets.sell` already supported exact-quality execution.
"""

from agents.base import Agent
from main import load_config
from simulation import engine, markets
from simulation.random_events import RandomEvents
from simulation.state import InventoryLot, PlayerState

SPOT = {
    "id": "spot",
    "price_multiplier": 1.0,
    "min_quality": "processing",
    "daily_capacity": 20,
    "fee_rate": 0.03,
}


def _stocked_player(money=100):
    """A farm that only sells: no plots, one premium and one standard lot.

    The standard lot has the shorter shelf life, so FEFO reaches for it
    first -- which is exactly what makes a dropped `quality` observable.
    """
    player = PlayerState(money=money, slots_total=0, day=0)
    player.plots = []
    player.lowest_money = money
    player.highest_money = money
    player.inventory_lots.extend(
        [
            InventoryLot("quickweed", 2, "premium", produced_day=0, shelf_life_days=20),
            InventoryLot("quickweed", 2, "standard", produced_day=0, shelf_life_days=3),
        ]
    )
    return player


def _quantities_by_quality(player, item_id="quickweed"):
    totals = {}
    for lot in player.inventory_lots:
        if lot.item_id == item_id and lot.quantity > 0:
            totals[lot.quality] = totals.get(lot.quality, 0) + lot.quantity
    return totals


# -- markets.sell already distinguishes the two; pin that first --------------


def test_sell_without_quality_draws_fefo_across_grades():
    player = _stocked_player()
    player.market_prices = {"quickweed": 10}

    _revenue, sold = markets.sell(player, "quickweed", 2, SPOT)

    assert sold == 2
    # The shorter-dated standard lot went, leaving the premium stock.
    assert _quantities_by_quality(player) == {"premium": 2}


def test_sell_with_quality_takes_exactly_that_grade():
    player = _stocked_player()
    player.market_prices = {"quickweed": 10}

    _revenue, sold = markets.sell(player, "quickweed", 2, SPOT, quality="premium")

    assert sold == 2
    assert _quantities_by_quality(player) == {"standard": 2}


# -- the engine must carry the agent's choice through -------------------------


class _FixedSale(Agent):
    """Sells one fixed, quality-tagged route and does nothing else."""

    name = "fixed_sale"
    watering_diligence = 0.0

    def __init__(self, quality):
        self.quality = quality

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        return None

    def should_buy_upgrade(self, player, upgrade):
        return False

    def choose_contract_deliveries(self, player):
        return []

    def choose_sales(self, player, channels, items_by_id):
        decision = {"item_id": "quickweed", "quantity": 2, "channel_id": "spot"}
        if self.quality is not None:
            decision["quality"] = self.quality
        return [decision]


def _run_one_day(agent, player):
    crops, upgrades, _config, world = load_config()
    engine.run_day(
        player,
        agent,
        crops,
        {crop["id"]: crop for crop in crops},
        upgrades,
        {upgrade["id"]: upgrade for upgrade in upgrades},
        world["watering"],
        world["fertilizer"],
        RandomEvents(7),
        world=world,
    )
    return player


def test_engine_sells_the_grade_the_agent_selected():
    player = _run_one_day(_FixedSale("premium"), _stocked_player())
    # Without the fix the engine dropped "premium" and FEFO sold the
    # standard lot instead, leaving the premium stock unsold.
    assert _quantities_by_quality(player) == {"standard": 2}


def test_engine_still_defaults_to_channel_minimum_when_no_grade_is_given():
    """The default `Agent.choose_sales` names no grade; that must keep
    behaving as it always has rather than crashing or forcing a grade."""
    player = _run_one_day(_FixedSale(None), _stocked_player())
    assert _quantities_by_quality(player) == {"premium": 2}


# -- mixed-grade, capacity-limited routing round-trip ------------------------


class _RoutingSeller(_FixedSale):
    """Routes by best price and records the plan it handed the engine."""

    name = "routing_seller"

    def __init__(self, honor_quality=True):
        super().__init__(None)
        self.honor_quality = honor_quality
        self.plan = []

    def choose_sales(self, player, channels, items_by_id):
        self.plan = self.route_sales_by_best_price(player, channels)
        if self.honor_quality:
            return self.plan
        # Reproduces the pre-fix engine: the routing still picks a grade,
        # the execution just never hears about it.
        return [
            {key: value for key, value in decision.items() if key != "quality"}
            for decision in self.plan
        ]


def _mixed_player():
    player = _stocked_player(money=0)
    player.inventory_lots.clear()
    player.inventory_lots.extend(
        [
            InventoryLot("quickweed", 15, "premium", produced_day=0, shelf_life_days=20),
            InventoryLot("quickweed", 15, "standard", produced_day=0, shelf_life_days=3),
        ]
    )
    return player


def test_routing_plan_is_executed_grade_for_grade():
    agent = _RoutingSeller(honor_quality=True)
    player = _run_one_day(agent, _mixed_player())

    planned = {}
    for decision in agent.plan:
        planned[decision["quality"]] = planned.get(decision["quality"], 0) + decision["quantity"]
    assert planned  # the routing really did split across grades and channels
    assert len({decision["channel_id"] for decision in agent.plan}) > 1

    sold_by_quality = {
        quality: 15 - _quantities_by_quality(player).get(quality, 0)
        for quality in ("premium", "standard")
    }
    assert sold_by_quality == planned


def test_honoring_quality_beats_dropping_it_on_a_capacity_limited_day():
    """The premium-paying channel has 8 units of capacity a day. Routing
    spends it on premium stock; the pre-fix execution let the shorter-dated
    standard lot take those slots at the same premium multiplier's expense.
    """
    honored = _run_one_day(_RoutingSeller(honor_quality=True), _mixed_player())
    dropped = _run_one_day(_RoutingSeller(honor_quality=False), _mixed_player())

    assert honored.total_revenue > dropped.total_revenue
