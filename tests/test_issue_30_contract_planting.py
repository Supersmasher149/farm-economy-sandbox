"""Regression tests for issue #30: active-contract crop selection in
profit_optimizer.py and progression_player.py must target forecast unmet
quantity (inventory + in-flight crops + processing output deducted) and
must not plant crops that can't mature by the contract deadline, the final
simulated day, or influence planting once expired but unresolved.
"""

from agents.no_upgrade_player import NoUpgradePlayer
from agents.profit_optimizer import ProfitOptimizer
from agents.progression_player import ProgressionPlayer
from simulation import contracts
from simulation.state import ContractState, InventoryLot, PlantedCrop, PlayerState


def _crops():
    contracted = {
        "id": "contracted",
        "seed_cost": 5,
        "growth_days": 3,
        "min_yield": 4,
        "max_yield": 4,
        "base_price": 5,
        "loss_chance": 0.0,
        "water_interval_days": 2,
        "unlock_requirement": None,
        "role": "fast",
    }
    # Deliberately much higher EV/day than "contracted", and a shorter
    # growth window, so once the contract override steps aside, normal
    # ranking picks this crop unambiguously -- proving the override actually
    # stepped aside rather than "contracted" winning on its own merits
    # either way -- and so it alone survives a final-simulation-day filter
    # tight enough to exclude "contracted".
    other = {
        "id": "other",
        "seed_cost": 5,
        "growth_days": 1,
        "min_yield": 4,
        "max_yield": 4,
        "base_price": 50,
        "loss_chance": 0.0,
        "water_interval_days": 2,
        "unlock_requirement": None,
        "role": "standard",
    }
    crops = [contracted, other]
    return crops, {c["id"]: c for c in crops}


def _player(day=0, total_days=None, money=200):
    player = PlayerState(money=money, slots_total=3, day=day, total_days=total_days)
    player.highest_money = player.money
    _crops_list, crops_by_id = _crops()
    player.crop_catalog = crops_by_id
    player.upgrades_catalog = {}
    player.contract_config = {}
    return player


# -- simulation.contracts.forecast_committed_supply --------------------------


def test_forecast_committed_supply_counts_inventory_processing_and_planted_crop():
    player = _player()
    contract = ContractState("c", "buyer", "contracted", 10, "standard", 5, 0, 10, 0.1)

    assert contracts.forecast_committed_supply(player, contract) == 0

    player.inventory_lots.append(InventoryLot("contracted", 3, "standard"))
    assert contracts.forecast_committed_supply(player, contract) == 3

    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))
    # Already-planted "contracted" matures well within the deadline: adds its
    # expected (safety-factor-discounted) yield on top of the 3 in inventory.
    assert contracts.forecast_committed_supply(player, contract) > 3


def test_forecast_committed_supply_excludes_unplanted_open_slot_capacity():
    """Open slots that *could* still be planted with the contracted crop must
    not count as already-committed supply -- that's exactly the decision a
    caller is trying to make, not a fact already true about the farm.
    """
    player = _player()
    contract = ContractState("c", "buyer", "contracted", 10, "standard", 5, 0, 10, 0.1)
    player.seed_inventory["contracted"] = 5  # plenty of seed on hand, nothing planted yet
    assert contracts.forecast_committed_supply(player, contract) == 0


# -- premium-quality contracts -----------------------------------------------


def test_forecast_committed_supply_counts_planted_crop_for_premium_contract():
    """A crop already in the ground that can still reach premium (no stress
    has accumulated yet) is committed supply toward a premium contract -- the
    #30 over-planting fix must apply to premium buyers too, not just standard.
    """
    player = _player()
    contract = ContractState("c", "buyer", "contracted", 1, "premium", 5, 0, 10, 0.1)
    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))

    assert contracts.forecast_committed_supply(player, contract) > 0


def test_forecast_committed_supply_excludes_stressed_crop_from_premium_contract():
    """A planted crop whose accumulated stress already caps it below premium
    must not count toward a premium contract -- only grades it can actually
    still reach are committed.
    """
    player = _player()
    contract = ContractState("c", "buyer", "contracted", 1, "premium", 5, 0, 10, 0.1)
    stressed = PlantedCrop("contracted", day_planted=0, growth_days_required=3)
    stressed.water_stress = 50.0
    stressed.nutrient_stress = 50.0
    stressed.temperature_stress = 50.0
    stressed.pest_stress = 50.0
    stressed.disease_stress = 50.0
    player.planted.append(stressed)

    assert contracts.forecast_committed_supply(player, contract) == 0


def test_forecast_committed_supply_excludes_future_plantings_from_premium_contract():
    """Open slots are not committed supply toward a premium contract either:
    the grade of a not-yet-planted crop is unknowable.
    """
    player = _player()
    contract = ContractState("c", "buyer", "contracted", 1, "premium", 5, 0, 10, 0.1)
    player.seed_inventory["contracted"] = 5
    assert contracts.forecast_committed_supply(player, contract) == 0


# -- profit_optimizer.choose_crop --------------------------------------------


def test_profit_optimizer_stops_planting_once_forecast_covers_remaining():
    """The exact bug scenario: a small contract (remaining=1) already
    guaranteed by an already-planted crop (forecast yield well over 1) must
    not keep forcing more of the contracted crop into every open slot.
    """
    crops, crops_by_id = _crops()
    player = _player()
    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


def test_profit_optimizer_still_plants_contracted_crop_when_forecast_is_short():
    """Sanity counterpart: with nothing already committed, a still-open
    contract must still steer planting toward the contracted crop.
    """
    crops, crops_by_id = _crops()
    player = _player()
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "contracted"


def test_profit_optimizer_stops_overplanting_premium_contract():
    """The #30 scenario for a premium buyer: a planted crop that can still
    reach premium already guarantees the remaining quantity, so the agent must
    not keep forcing the contracted crop into every open slot.
    """
    crops, crops_by_id = _crops()
    player = _player()
    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "premium", 5, 0, 10, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


def test_profit_optimizer_still_plants_contracted_crop_for_short_premium_contract():
    """Sanity counterpart: a premium contract with nothing committed yet
    must still steer planting toward the contracted crop.
    """
    crops, crops_by_id = _crops()
    player = _player()
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "premium", 5, 0, 10, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "contracted"


def test_profit_optimizer_skips_contracted_crop_that_misses_the_deadline():
    crops, crops_by_id = _crops()
    player = _player(day=8)
    # Deadline is 2 days out; "contracted" needs 3 days to mature.
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


def test_profit_optimizer_skips_contracted_crop_that_misses_final_simulation_day():
    crops, crops_by_id = _crops()
    # Contract deadline is comfortably far off, but the run itself ends in 2
    # days -- "contracted" (3-day growth) still can't be sold before then.
    player = _player(day=8, total_days=10)
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 50, 0.1)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


def test_profit_optimizer_ignores_expired_unresolved_contract():
    """resolve_expired() runs at end of day, after crop decisions -- a
    contract already past its deadline but not yet marked resolved must not
    still force planting.
    """
    crops, crops_by_id = _crops()
    player = _player(day=11)
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1, resolved=False)
    )

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


def test_inherited_optimizer_control_agent_also_stops_overplanting():
    """NoUpgradePlayer inherits choose_crop from ProfitOptimizer unchanged
    (see tests/test_strategy_controls.py); the fix must carry through.
    """
    crops, crops_by_id = _crops()
    player = _player()
    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = NoUpgradePlayer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] == "other"


# -- progression_player.choose_crop ------------------------------------------


def test_progression_player_stops_planting_once_forecast_covers_remaining():
    crops, crops_by_id = _crops()
    player = _player()
    player.planted.append(PlantedCrop("contracted", day_planted=0, growth_days_required=3))
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = ProgressionPlayer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] != "contracted"


def test_progression_player_skips_contracted_crop_that_misses_the_deadline():
    crops, crops_by_id = _crops()
    player = _player(day=8)
    player.active_contracts.append(
        ContractState("c", "buyer", "contracted", 1, "standard", 5, 0, 10, 0.1)
    )

    chosen = ProgressionPlayer().choose_crop(player, crops, crops_by_id, {})
    assert chosen["id"] != "contracted"
