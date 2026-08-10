"""Exact-boundary tests for the run horizon (CQ-02, CQ-08).

`runner.single_run` calls `engine.run_day` exactly `total_days` times
starting at `player.day == 0`, so the last day the simulator ever executes is
`total_days - 1`. Production forecasts used to cap at `total_days` instead,
counting a harvest or a processing completion on a day that never runs, and
`ProgressionPlayer` re-derived the same boundary one day too permissively.

`simulation.economy_rules.last_executable_day` is now the single authority.
These tests pin both sides of the boundary -- `total_days - 1` counts,
`total_days` does not -- for the helper itself, for crop and processing
forecasts, and for the agents that plant against it.
"""

import pytest

from agents.progression_player import ProgressionPlayer
from simulation import contracts, economy_rules, processing
from simulation.state import ContractState, PlantedCrop, PlayerState, ProcessingJob

CROP = {
    "id": "crop",
    "seed_cost": 1,
    "growth_days": 5,
    "min_yield": 2,
    "max_yield": 2,
    "base_price": 5,
    "loss_chance": 0.0,
    "water_interval_days": 3,
    "unlock_requirement": None,
}


def _player(day=0, total_days=None, money=1000, slots_total=0):
    player = PlayerState(money=money, slots_total=slots_total, day=day, total_days=total_days)
    player.crop_catalog = {}
    player.upgrades_catalog = {}
    player.contract_config = {}
    player.processing_recipes = []
    player.processing_capacity = 0
    return player


def _offer(item_id="crop", quantity=1, deadline_day=50, min_quality="standard"):
    return ContractState("c", "buyer", item_id, quantity, min_quality, 10, 0, deadline_day, 0.1)


# -- the helper itself -------------------------------------------------------


def test_last_executable_day_is_one_before_total_days():
    assert economy_rules.last_executable_day(_player(total_days=10)) == 9


def test_last_executable_day_is_none_for_an_open_ended_run():
    assert economy_rules.last_executable_day(_player(total_days=None)) is None


def test_effective_deadline_clamps_to_the_run_horizon():
    player = _player(total_days=10)
    assert economy_rules.effective_deadline(player, 50) == 9
    assert economy_rules.effective_deadline(player, 4) == 4
    # Exactly on the boundary, and one day past it.
    assert economy_rules.effective_deadline(player, 9) == 9
    assert economy_rules.effective_deadline(player, 10) == 9


def test_effective_deadline_is_a_no_op_for_an_open_ended_run():
    assert economy_rules.effective_deadline(_player(total_days=None), 50) == 50


@pytest.mark.parametrize(
    ("day", "growth_days", "expected"),
    [
        (0, 9, True),  # harvest lands exactly on day 9, the last executed day
        (0, 10, False),  # harvest would land on day 10, which never runs
        (6, 3, True),  # boundary again, from mid-run
        (7, 3, False),
        (9, 0, True),  # a zero-day crop on the final day still harvests
    ],
)
def test_matures_within_run_boundary(day, growth_days, expected):
    assert (
        economy_rules.matures_within_run(growth_days, _player(day=day, total_days=10)) is expected
    )


def test_matures_within_run_never_blocks_an_open_ended_run():
    assert economy_rules.matures_within_run(9999, _player(day=9999, total_days=None)) is True


# -- crop harvest forecasts --------------------------------------------------


def _planted_player(total_days):
    """A player with one crop planted on day 0 that matures on day 5."""
    player = _player(day=0, total_days=total_days)
    player.crop_catalog = {"crop": CROP}
    player.planted.append(PlantedCrop(crop_id="crop", day_planted=0, growth_days_required=5))
    return player


def test_planted_crop_counts_when_it_matures_on_the_last_executable_day():
    # total_days=6 -> last executed day is 5, exactly when this crop matures.
    supply = contracts.forecast_committed_supply(_planted_player(total_days=6), _offer())
    assert supply > 0


def test_planted_crop_is_excluded_when_it_matures_one_day_past_the_run():
    # total_days=5 -> last executed day is 4; the day-5 harvest never happens.
    # Capping at total_days (the old behavior) would have counted it.
    supply = contracts.forecast_committed_supply(_planted_player(total_days=5), _offer())
    assert supply == 0


def test_producible_quantity_excludes_a_harvest_one_day_past_the_run():
    assert contracts.producible_quantity(_planted_player(total_days=5), _offer()) == 0
    assert contracts.producible_quantity(_planted_player(total_days=6), _offer()) > 0


# -- processing completion forecasts -----------------------------------------


def _job_player(total_days, completion_day):
    player = _player(day=0, total_days=total_days)
    player.processing_jobs.append(
        ProcessingJob(
            recipe_id="r",
            output_item_id="crop",
            output_quantity=4,
            completion_day=completion_day,
            shelf_life_days=30,
            unit_cost=1.0,
        )
    )
    return player


def test_processing_job_counts_when_it_completes_on_the_last_executable_day():
    player = _job_player(total_days=10, completion_day=9)
    assert contracts.forecast_committed_supply(player, _offer()) == 4
    assert contracts.producible_quantity(player, _offer()) == 4


def test_processing_job_is_excluded_when_it_completes_one_day_past_the_run():
    # completion_day=10 with total_days=10: `complete_jobs` is never called
    # again with player.day >= 10, so this output never exists.
    player = _job_player(total_days=10, completion_day=10)
    assert contracts.forecast_committed_supply(player, _offer()) == 0
    assert contracts.producible_quantity(player, _offer()) == 0


def test_complete_jobs_confirms_the_forecast_boundary():
    """The forecast above is only right because this is how jobs really land."""
    on_boundary = _job_player(total_days=10, completion_day=9)
    on_boundary.day = 9
    assert processing.complete_jobs(on_boundary) == 4

    past_boundary = _job_player(total_days=10, completion_day=10)
    past_boundary.day = 9  # the last day the run ever executes
    assert processing.complete_jobs(past_boundary) == 0
    assert past_boundary.processing_jobs  # still pending, and always will be


# -- agents planting against the boundary ------------------------------------

# The contracted crop deliberately is NOT what this agent's ordinary ranking
# would pick (that prefers `role: standard`), so these tests isolate the
# contract branch's own maturity check rather than the fallback behind it.
FAST = dict(CROP, id="fast", role="standard", growth_days=2, seed_cost=2)


def _contracted_player(day, total_days):
    player = PlayerState(money=500, slots_total=1, day=day, total_days=total_days)
    player.highest_money = player.money
    player.crop_catalog = {"crop": CROP, "fast": FAST}
    player.upgrades_catalog = {}
    player.contract_config = {}
    player.processing_recipes = []
    player.processing_capacity = 0
    player.active_contracts.append(_offer(quantity=99, deadline_day=total_days + 50))
    return player


def _progression_choice(day, total_days):
    player = _contracted_player(day=day, total_days=total_days)
    return ProgressionPlayer().choose_crop(player, [CROP, FAST], player.crop_catalog, {})


def test_progression_player_plants_a_contracted_crop_that_matures_in_time():
    # day 4, total_days=10 -> last executed day 9; a 5-day crop lands on 9,
    # so the contract branch overrides the ordinary `standard`-role pick.
    assert _progression_choice(day=4, total_days=10) == CROP


def test_progression_player_rejects_a_contracted_crop_one_day_past_the_run():
    # day 5, total_days=10 -> the crop would mature on day 10, never executed.
    # The old `growth_days <= total_days - player.day` check admitted it, so
    # the agent bought a seed for a harvest that could not happen; now the
    # contract branch declines and the ordinary ranking picks instead.
    assert _progression_choice(day=5, total_days=10) == FAST
