from agents.fast_seller import FastSeller
from agents.profit_optimizer import ProfitOptimizer
from agents.progression_player import ProgressionPlayer
from main import load_config
from metrics.aggregate_results import aggregate
from runner.batch_run import run_batch
from simulation import contracts
from simulation.state import ContractState, PlayerState


def make_crop_set():
    return [
        {
            "id": "quickweed", "role": "fast", "seed_cost": 5,
            "growth_days": 3, "min_yield": 1, "max_yield": 2,
            "base_price": 5, "loss_chance": 0.03,
            "water_interval_days": 2, "unlock_requirement": None,
        },
        {
            "id": "greenleaf", "role": "standard", "seed_cost": 18,
            "growth_days": 7, "min_yield": 4, "max_yield": 6,
            "base_price": 7, "loss_chance": 0.05,
            "water_interval_days": 3, "unlock_requirement": None,
        },
    ]


def make_player(money=100, reserve=0):
    player = PlayerState(money=money, slots_total=3, operating_reserve=reserve)
    player.lowest_money = money
    player.highest_money = money
    return player


def test_optimizer_falls_back_to_fast_crop_below_operating_reserve():
    player = make_player(money=60, reserve=100)
    crops = make_crop_set()
    crops_by_id = {crop["id"]: crop for crop in crops}

    chosen = ProfitOptimizer().choose_crop(player, crops, crops_by_id, {})

    assert chosen["id"] == "quickweed"


def test_optimizer_keeps_reserve_after_upgrade_and_fertilizer_spend(fertilizer_config):
    agent = ProfitOptimizer()
    upgrade = {"id": "capacity_1", "cost": 120}
    player = make_player(money=125, reserve=100)
    standard = make_crop_set()[1]

    assert not agent.should_buy_upgrade(player, upgrade)
    assert not agent.should_use_fertilizer(player, standard, fertilizer_config)

    # Bump highest_money alongside money: should_buy_upgrade_within_budget
    # caps cumulative upgrade spend against the farm's peak cash, so a
    # fixture that only mutates money (never updates the peak) would
    # otherwise trip that cap rather than exercise the reserve check this
    # test is about.
    player.money = 350
    player.highest_money = 350
    assert agent.should_buy_upgrade(player, upgrade)
    assert agent.should_use_fertilizer(player, standard, fertilizer_config)


def test_contract_filter_uses_market_opportunity_and_capacity():
    player = make_player(money=100, reserve=0)
    player.market_prices = {"greenleaf": 7}
    player.market_channels = [{
        "id": "farm_stand", "min_quality": "standard", "price_multiplier": 1.45,
        "daily_capacity": 8, "flat_fee": 1,
    }]
    player.crop_catalog = {crop["id"]: crop for crop in make_crop_set()}
    player.upgrades_catalog = {}

    dominated = ContractState(
        "dominated", "local", "greenleaf", 6, "standard", 8.4, 0, 14, 0.12
    )
    attractive = ContractState(
        "attractive", "regional", "greenleaf", 6, "standard", 11.0, 0, 14, 0.1
    )

    assert not contracts.is_offer_profitable(player, dominated)
    assert contracts.is_offer_profitable(player, attractive)
    assert contracts.is_offer_feasible(player, attractive)

    oversized = ContractState(
        "oversized", "regional", "greenleaf", 20, "standard", 11.0, 0, 14, 0.1
    )
    assert not contracts.is_offer_feasible(player, oversized)


def test_progression_player_uses_reserve_as_recovery_threshold():
    player = make_player(money=60, reserve=100)
    crops = make_crop_set()
    crops_by_id = {crop["id"]: crop for crop in crops}

    chosen = ProgressionPlayer().choose_crop(player, crops, crops_by_id, {})

    assert chosen["id"] == "quickweed"


def test_full_world_optimizer_beats_fast_seller_without_extra_ruin():
    crops, upgrades, config, world = load_config()
    results = run_batch(
        config,
        [FastSeller(), ProfitOptimizer()],
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        num_runs=100,
        base_seed=20260804,
        workers=1,
        world=world,
    )
    summary = aggregate(results)

    assert summary["profit_optimizer"]["avg_final_money"] > summary["fast_seller"]["avg_final_money"]
    assert summary["profit_optimizer"]["bankruptcy_rate"] <= summary["fast_seller"]["bankruptcy_rate"]
    assert summary["profit_optimizer"]["avg_contracts_failed"] == 0

# NOTE on the 200-day/$1000-start stress scenario that motivated this file's
# soil-aware crop/fertilizer policy (see docs/superpowers/specs/2026-08-04-
# balance-fix-design.md's "Correction" section): no regression test is
# pinned for it. Investigation there confirmed the collapse is structural to
# the simulation's soil economy (plot nitrogen/phosphorus/potassium never
# regenerate except via fertilizer) rather than an agent-decision-policy
# gap -- several escalating crop-ranking and fertilizer-policy changes
# measurably improved individual-run survival time and the short-horizon
# default scenario (this file's tests above), but left the long-horizon
# aggregate bankruptcy rate and average bankruptcy day statistically
# unchanged from the unfixed baseline. A real fix there needs shared
# simulation mechanics (e.g. some form of soil regeneration), which is out
# of scope for agent policy alone.
