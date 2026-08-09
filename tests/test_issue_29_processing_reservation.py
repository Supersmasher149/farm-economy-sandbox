"""Regression tests for issue #29: ProfitOptimizer.choose_processing() must
plan against reserved cash/input/capacity across its own emitted decisions,
not the unchanged starting player state, and every decision it emits must
actually be executable in sequence by simulation.processing.start_job.
"""

from agents.profit_optimizer import ProfitOptimizer
from simulation import processing
from simulation.state import InventoryLot, PlayerState, ProcessingJob

MILL_GREENLEAF = {
    "id": "mill_greenleaf",
    "input_item_id": "greenleaf",
    "input_quantity": 2,
    "output_item_id": "flour",
    "output_quantity": 1,
    "cost": 1,
    "min_quality": "processing",
}
PRESS_GREENLEAF = {
    "id": "press_greenleaf",
    "input_item_id": "greenleaf",
    "input_quantity": 1,
    "output_item_id": "oil",
    "output_quantity": 1,
    "cost": 0,
    "min_quality": "processing",
}


def _player(money, processing_capacity, greenleaf_units, processing_jobs=None):
    player = PlayerState(money=money, slots_total=1)
    player.processing_capacity = processing_capacity
    player.processing_jobs = processing_jobs or []
    player.inventory_lots = (
        [InventoryLot("greenleaf", greenleaf_units, "standard")] if greenleaf_units else []
    )
    player.market_prices = {"greenleaf": 7, "flour": 20, "oil": 10}
    return player


def _decisions_by_recipe(decisions):
    return {d["recipe_id"]: d["batches"] for d in decisions}


def test_shared_input_is_reserved_across_recipes():
    # mill_greenleaf: margin 20 - 7*2 - 1 = 5/batch, uses 2 greenleaf.
    # press_greenleaf: margin 10 - 7*1 - 0 = 3/batch, uses 1 greenleaf.
    # 5 greenleaf total: the higher-margin recipe must be planned first and
    # reserve its input before the second recipe sees what's left, or both
    # would independently claim the same units against the unchanged
    # starting inventory.
    player = _player(money=100, processing_capacity=10, greenleaf_units=5)
    decisions = ProfitOptimizer().choose_processing(player, [MILL_GREENLEAF, PRESS_GREENLEAF], {})
    by_recipe = _decisions_by_recipe(decisions)
    assert by_recipe == {"mill_greenleaf": 2, "press_greenleaf": 1}

    # And every emitted decision must actually execute in sequence.
    fresh = _player(money=100, processing_capacity=10, greenleaf_units=5)
    for decision in decisions:
        recipe = MILL_GREENLEAF if decision["recipe_id"] == "mill_greenleaf" else PRESS_GREENLEAF
        assert processing.start_job(fresh, recipe, decision["batches"], fresh.processing_capacity)
    assert fresh.inventory_lots == []  # exactly consumed, nothing left over


def test_insufficient_cash_caps_batches():
    priced_recipe = {**MILL_GREENLEAF, "cost": 3}
    player = _player(money=7, processing_capacity=10, greenleaf_units=100)
    decisions = ProfitOptimizer().choose_processing(player, [priced_recipe], {})
    # floor(7 / 3) == 2 batches, not the 50 the input supply would allow.
    assert _decisions_by_recipe(decisions) == {"mill_greenleaf": 2}


def test_unaffordable_recipe_gets_no_decision():
    # Still profitable (20 - 14 - 5 == 1 > 0) but the bare $3 on hand can't
    # cover even one $5 batch. The old implementation never checked cash at
    # all, so it emitted {"batches": 1} here regardless of affordability --
    # a decision start_job would then reject outright.
    priced_recipe = {**MILL_GREENLEAF, "cost": 5}
    player = _player(money=3, processing_capacity=10, greenleaf_units=100)
    decisions = ProfitOptimizer().choose_processing(player, [priced_recipe], {})
    assert decisions == []


def test_full_capacity_stops_lower_priority_recipes():
    player = _player(money=100, processing_capacity=1, greenleaf_units=100)
    decisions = ProfitOptimizer().choose_processing(player, [MILL_GREENLEAF, PRESS_GREENLEAF], {})
    # Only 1 capacity slot total, shared across both recipes -- the
    # higher-margin recipe (mill_greenleaf) claims it, and the lower-margin
    # one gets nothing rather than a doomed decision.
    assert _decisions_by_recipe(decisions) == {"mill_greenleaf": 1}


def test_existing_jobs_count_against_capacity():
    existing = [
        ProcessingJob("other", "flour", 1, completion_day=5, shelf_life_days=10, unit_cost=1.0)
    ]
    player = _player(
        money=100, processing_capacity=2, greenleaf_units=100, processing_jobs=existing
    )
    decisions = ProfitOptimizer().choose_processing(player, [MILL_GREENLEAF, PRESS_GREENLEAF], {})
    # capacity=2, 1 already in flight -> only 1 slot free for new decisions.
    assert _decisions_by_recipe(decisions) == {"mill_greenleaf": 1}


def test_zero_remaining_capacity_returns_no_decisions():
    existing = [
        ProcessingJob("other", "flour", 1, completion_day=5, shelf_life_days=10, unit_cost=1.0)
    ]
    player = _player(
        money=100, processing_capacity=1, greenleaf_units=100, processing_jobs=existing
    )
    decisions = ProfitOptimizer().choose_processing(player, [MILL_GREENLEAF, PRESS_GREENLEAF], {})
    assert decisions == []


def test_decisions_are_order_independent_when_resources_are_ample():
    player_ab = _player(money=100, processing_capacity=10, greenleaf_units=100)
    player_ba = _player(money=100, processing_capacity=10, greenleaf_units=100)

    decisions_ab = ProfitOptimizer().choose_processing(
        player_ab, [MILL_GREENLEAF, PRESS_GREENLEAF], {}
    )
    decisions_ba = ProfitOptimizer().choose_processing(
        player_ba, [PRESS_GREENLEAF, MILL_GREENLEAF], {}
    )

    assert _decisions_by_recipe(decisions_ab) == _decisions_by_recipe(decisions_ba)


def test_unprofitable_recipe_is_never_planned():
    losing_recipe = {**MILL_GREENLEAF, "cost": 100}
    player = _player(money=1000, processing_capacity=10, greenleaf_units=100)
    decisions = ProfitOptimizer().choose_processing(player, [losing_recipe], {})
    assert decisions == []
