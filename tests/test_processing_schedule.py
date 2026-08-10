"""Regression tests for CQ-03: processed-product feasibility ignored when
future inputs actually arrive.

`_item_capacity` used to pool current inventory with every crop harvest
expected before the deadline, then divide a whole window's worth of
processing slot-days by the recipe duration. That threw away the day each
input lands, so capacity from the *start* of the window could be spent on a
crop harvested at the *end* of it -- approving a contract that cannot be
scheduled in the real daily order.

Forecasting is now slot-level: a batch starts on the later of "its inputs
have arrived" and "a slot is free", and only counts if it still finishes by
the effective deadline.
"""

from simulation import contracts
from simulation.state import ContractState, InventoryLot, PlantedCrop, PlayerState, ProcessingJob

GRAIN = {
    "id": "grain",
    "seed_cost": 1,
    "growth_days": 8,
    "min_yield": 100,
    "max_yield": 100,
    "base_price": 5,
    "loss_chance": 0.0,
    "water_interval_days": 3,
    "unlock_requirement": None,
}

# 100 units, no loss, times the 0.45 production safety factor.
HARVEST_YIELD = 100 * contracts.PRODUCTION_SAFETY_FACTOR


def _recipe(processing_days=3, input_quantity=1, output_quantity=1, cost=0.0, recipe_id="bake"):
    return {
        "id": recipe_id,
        "input_item_id": "grain",
        "input_quantity": input_quantity,
        "output_item_id": "bread",
        "output_quantity": output_quantity,
        "processing_days": processing_days,
        "cost": cost,
    }


def _player(capacity=1, recipes=None, money=10_000):
    player = PlayerState(money=money, slots_total=1, day=0, total_days=None)
    player.crop_catalog = {"grain": GRAIN}
    player.upgrades_catalog = {}
    player.contract_config = {}
    player.processing_recipes = recipes if recipes is not None else [_recipe()]
    player.processing_capacity = capacity
    return player


def _with_pending_harvest(player, matures_on=8):
    """One grain crop already in the ground, harvested on `matures_on`."""
    player.planted.append(
        PlantedCrop(crop_id="grain", day_planted=0, growth_days_required=matures_on)
    )
    return player


def _offer(deadline_day, quantity=1):
    return ContractState("c", "buyer", "bread", quantity, "standard", 10, 0, deadline_day, 0.1)


# -- inputs that arrive too late to be processed -----------------------------


def test_harvest_arriving_too_late_to_finish_a_batch_is_not_forecast():
    # Grain lands on day 8; a 3-day bake started then finishes on day 11,
    # past a day-10 deadline. The old pooled slot-day model saw 10 slot-days
    # and ~45 units of eventual grain and forecast three batches.
    player = _with_pending_harvest(_player())
    assert contracts.producible_quantity(player, _offer(deadline_day=10)) == 0


def test_harvest_arriving_exactly_in_time_is_still_forecast():
    # Same setup, one more day of deadline: day 8 + 3 == 11, which fits.
    # Guards against over-correcting into rejecting feasible contracts.
    player = _with_pending_harvest(_player())
    assert contracts.producible_quantity(player, _offer(deadline_day=11)) == 1


def test_inventory_on_hand_is_still_processed_immediately():
    # Nothing to wait for: grain is already in store, so the batch starts
    # today and the window comfortably fits three back-to-back bakes.
    player = _player()
    player.inventory_lots.append(InventoryLot("grain", 10, "standard"))
    assert contracts.producible_quantity(player, _offer(deadline_day=9)) == 3


def test_late_harvest_does_not_inflate_what_on_hand_stock_can_cover():
    # One unit on hand (one batch, finishing day 3) plus a day-8 harvest that
    # cannot be baked in time. Pooling the two made the late harvest look
    # like extra capacity for the early window.
    player = _with_pending_harvest(_player())
    player.inventory_lots.append(InventoryLot("grain", 1, "standard"))
    assert contracts.producible_quantity(player, _offer(deadline_day=10)) == 1


# -- slot occupancy over time ------------------------------------------------


def test_single_slot_serializes_batches_rather_than_pooling_slot_days():
    # 9 days of window, one slot, 5-day recipe: batch one runs days 0-5,
    # batch two would finish on day 10. Only one fits. A slot-day pool
    # (9 // 5) happened to agree here; the point is the schedule now says so
    # for the right reason, and keeps saying so as arrivals move.
    player = _player(recipes=[_recipe(processing_days=5)])
    player.inventory_lots.append(InventoryLot("grain", 10, "standard"))
    assert contracts.producible_quantity(player, _offer(deadline_day=9)) == 1


def test_two_slots_run_batches_in_parallel():
    player = _player(capacity=2, recipes=[_recipe(processing_days=5)])
    player.inventory_lots.append(InventoryLot("grain", 10, "standard"))
    assert contracts.producible_quantity(player, _offer(deadline_day=9)) == 2


def test_a_running_job_delays_the_slot_it_occupies():
    # The only slot is busy until day 6, so a 3-day bake cannot start before
    # then and finishes on day 9 -- exactly at the deadline.
    player = _player()
    player.inventory_lots.append(InventoryLot("grain", 10, "standard"))
    player.processing_jobs.append(
        ProcessingJob(
            recipe_id="other",
            output_item_id="jam",
            output_quantity=1,
            completion_day=6,
            shelf_life_days=30,
            unit_cost=1.0,
        )
    )
    assert contracts.producible_quantity(player, _offer(deadline_day=9)) == 1
    assert contracts.producible_quantity(player, _offer(deadline_day=8)) == 0


# -- recipes sharing one input still cannot double-spend it ------------------


def test_recipes_sharing_an_input_consume_one_shared_timeline():
    # Two recipes, two slots, but only enough grain on hand for one batch.
    player = _player(
        capacity=2,
        recipes=[_recipe(recipe_id="bake"), _recipe(recipe_id="toast")],
    )
    player.inventory_lots.append(InventoryLot("grain", 1, "standard"))
    assert contracts.producible_quantity(player, _offer(deadline_day=9)) == 1


def test_feasibility_rejects_a_contract_whose_inputs_land_too_late():
    player = _with_pending_harvest(_player())
    assert not contracts.is_offer_feasible(player, _offer(deadline_day=10, quantity=1))
    assert contracts.is_offer_feasible(player, _offer(deadline_day=11, quantity=1))


# -- the harvest schedule itself ---------------------------------------------


def test_future_crop_arrivals_reports_the_day_each_harvest_lands():
    player = _with_pending_harvest(_player())
    guaranteed, seeded, per_harvest, _funding = contracts._future_crop_arrivals(
        player, GRAIN, deadline=30, min_quality="processing"
    )
    # One crop in the ground maturing on day 8, then that slot replanted for
    # further 8-day cycles landing on days 16 and 24 within a 30-day window.
    assert guaranteed == [8]
    assert seeded == [16, 24]
    assert per_harvest == HARVEST_YIELD


def test_future_crop_capacity_total_matches_the_arrival_schedule():
    player = _with_pending_harvest(_player())
    guaranteed, seeded, per_harvest, _funding = contracts._future_crop_arrivals(
        player, GRAIN, deadline=30, min_quality="processing"
    )
    total, _funding, free = contracts._future_crop_capacity(
        player, GRAIN, deadline=30, min_quality="processing"
    )
    assert total == (len(guaranteed) + len(seeded)) * per_harvest
    assert free == len(guaranteed) * per_harvest
