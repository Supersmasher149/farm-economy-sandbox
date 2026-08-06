"""Regression tests for issue #12's reopened follow-up: the revised contract
feasibility estimator still accepted impossible contracts and rejected
feasible ones. Five residual defects in simulation/contracts.py, each
covered here:

- Processing forecasts ignored `processing_days`, counting output that
  would complete after the deadline.
- Processing capacity was treated as only currently-free slots, never
  accounting for a slot turning over (a job completing) before the
  deadline and being reused.
- Multiple recipes producing the same item independently "saw" the full,
  unreserved input inventory, double-counting a shared input.
- An already-planted crop stressed enough that even its best possible
  grade is "rejected" was still counted toward standard-quality contracts.
- Future production was forecast past `player.total_days`, so a contract
  could look feasible even though the run ends before it could be
  produced.
"""

import pytest

from simulation import contracts
from simulation.state import ContractState, InventoryLot, PlantedCrop, PlayerState, ProcessingJob


def base_player(day=0, total_days=None, money=1000, slots_total=1):
    player = PlayerState(money=money, slots_total=slots_total, day=day, total_days=total_days)
    player.crop_catalog = {}
    player.upgrades_catalog = {}
    player.contract_config = {}
    player.processing_recipes = []
    player.processing_capacity = 0
    return player


def offer(item_id="product", quantity=1, deadline_day=10, min_quality="standard"):
    return ContractState("c", "buyer", item_id, quantity, min_quality, 10, 0, deadline_day, 0.1)


# -- processing_days must gate future output by completion time --------------


def test_recipe_output_excluded_when_it_cannot_complete_by_deadline():
    player = base_player(day=0)
    player.inventory_lots.append(InventoryLot("grain", 100, "standard"))
    player.processing_capacity = 5
    player.processing_recipes = [
        {
            "id": "slow",
            "input_item_id": "grain",
            "input_quantity": 1,
            "output_item_id": "bread",
            "output_quantity": 1,
            "processing_days": 5,
            "cost": 0,
        }
    ]

    tight = offer("bread", quantity=1, deadline_day=2)  # only 2 days -- a 5-day job can't finish
    assert contracts.producible_quantity(player, tight) == 0

    generous = offer("bread", quantity=1, deadline_day=10)
    assert contracts.producible_quantity(player, generous) > 0


# -- capacity must account for slots freed by jobs completing before deadline


def test_capacity_reuses_a_slot_freed_by_an_existing_job_before_deadline():
    player = base_player(day=0)
    player.inventory_lots.append(InventoryLot("grain", 100, "standard"))
    player.processing_capacity = 1
    player.processing_recipes = [
        {
            "id": "quick",
            "input_item_id": "grain",
            "input_quantity": 1,
            "output_item_id": "bread",
            "output_quantity": 1,
            "processing_days": 2,
            "cost": 0,
        }
    ]
    # The single slot is occupied by an existing job, but that job
    # completes on day 2 -- with 10 days until the deadline, the freed
    # slot should still be able to run further 2-day batches afterward.
    player.processing_jobs.append(
        ProcessingJob("other", "flour", 1, completion_day=2, shelf_life_days=10, unit_cost=0)
    )

    contract = offer("bread", quantity=1, deadline_day=10)
    # Previously: free_capacity = capacity(1) - len(jobs)(1) = 0 forever,
    # so this would report 0 no matter how far out the deadline was.
    assert contracts.producible_quantity(player, contract) > 0


# -- multiple recipes must not double-count a shared input -------------------


def test_two_recipes_sharing_one_input_do_not_double_count_it():
    player = base_player(day=0)
    player.inventory_lots.append(InventoryLot("grain", 4, "standard"))
    player.processing_capacity = 5
    # Both recipes need all 4 available grain for a single batch; only one
    # of them can actually run.
    player.processing_recipes = [
        {
            "id": "recipe_a",
            "input_item_id": "grain",
            "input_quantity": 4,
            "output_item_id": "bread",
            "output_quantity": 2,
            "processing_days": 1,
            "cost": 0,
        },
        {
            "id": "recipe_b",
            "input_item_id": "grain",
            "input_quantity": 4,
            "output_item_id": "bread",
            "output_quantity": 3,
            "processing_days": 1,
            "cost": 0,
        },
    ]

    contract = offer("bread", quantity=1, deadline_day=10)
    # recipe_a (first in config order) claims all 4 grain -> 2 bread;
    # recipe_b then sees none left -> 0 more. Previously each recipe's
    # batch count was computed against the same unreserved 4 grain, so this
    # would report 2 + 3 = 5 -- output from a job that could never actually
    # start alongside the other.
    assert contracts.producible_quantity(player, contract) == 2


# -- an already-doomed planted crop must not count toward quality contracts --


def test_already_rejected_grade_planted_crop_excluded_from_standard_forecast():
    player = base_player(day=0, slots_total=1)
    crop = {
        "id": "crop",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 4,
        "max_yield": 4,
        "base_price": 5,
        "loss_chance": 0.0,
    }
    player.crop_catalog = {"crop": crop}
    planted = PlantedCrop("crop", day_planted=0, growth_days_required=3, plot_index=0)
    # Stress accumulated so far already guarantees a "rejected" grade
    # (crop_growth.harvest_multipliers clamps quality to 0 well before
    # this), and stress only ever grows between now and harvest.
    planted.water_stress = 50.0
    planted.nutrient_stress = 50.0
    planted.temperature_stress = 50.0
    planted.pest_stress = 50.0
    planted.disease_stress = 50.0
    player.planted.append(planted)

    contract = offer("crop", quantity=1, deadline_day=5, min_quality="standard")
    # Previously: any already-planted matching crop maturing in time counted
    # its full expected yield regardless of its actual quality trajectory.
    assert contracts.forecast_committed_supply(player, contract) == 0


def test_already_rejected_grade_planted_crop_still_counts_for_processing_quality():
    """The same doomed crop should still be forecast for a contract whose
    min_quality is 'processing' or lower -- only quality tiers it genuinely
    cannot reach are excluded.
    """
    player = base_player(day=0, slots_total=1)
    crop = {
        "id": "crop",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 4,
        "max_yield": 4,
        "base_price": 5,
        "loss_chance": 0.0,
    }
    player.crop_catalog = {"crop": crop}
    planted = PlantedCrop("crop", day_planted=0, growth_days_required=3, plot_index=0)
    planted.water_stress = 50.0
    planted.nutrient_stress = 50.0
    planted.temperature_stress = 50.0
    planted.pest_stress = 50.0
    planted.disease_stress = 50.0
    player.planted.append(planted)

    contract = offer("crop", quantity=1, deadline_day=5, min_quality="rejected")
    assert contracts.forecast_committed_supply(player, contract) > 0


# -- future production must be capped at player.total_days -------------------


def test_future_production_is_capped_at_total_days_not_contract_deadline():
    crop = {
        "id": "crop",
        "seed_cost": 1,
        "growth_days": 3,
        "min_yield": 2,
        "max_yield": 2,
        "base_price": 5,
        "loss_chance": 0.0,
    }
    # Deadline is far beyond the run itself (total_days=5): without capping,
    # 50 days of runway lets one open slot cycle through a 3-day crop
    # roughly 16 times; the run only actually has 5 days to give it.
    player = base_player(day=0, total_days=5, slots_total=1, money=1000)
    player.crop_catalog = {"crop": crop}
    contract = offer("crop", quantity=1, deadline_day=50)

    uncapped_days_available = 50
    capped_days_available = 5
    expected_yield = (2 + 2) / 2 * 1.0 * contracts.PRODUCTION_SAFETY_FACTOR
    uncapped_cycles = uncapped_days_available // 3
    capped_cycles = capped_days_available // 3
    assert uncapped_cycles > capped_cycles  # sanity: the two really do differ

    produced = contracts.producible_quantity(player, contract)
    assert produced == pytest.approx(capped_cycles * expected_yield)
    assert produced < uncapped_cycles * expected_yield
