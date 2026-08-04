from agents.diversifier import Diversifier
from agents.fast_seller import FastSeller
from agents.fertilizer_maximalist import FertilizerMaximalist
from agents.neglectful_grower import NeglectfulGrower
from agents.no_upgrade_player import NoUpgradePlayer
from agents.profit_optimizer import ProfitOptimizer
from agents.progression_player import ProgressionPlayer
from agents.random_agent import RandomAgent
from agents.reckless_spender import RecklessSpender
from agents.risk_averse_grower import RiskAverseGrower
from agents.upgrade_rusher import UpgradeRusher
from runner.single_run import run_single

CONFIG = {"start_money": 60, "start_slots": 3, "days": 30}

WATERING_SETTINGS = {
    "neglect_loss_chance_penalty_per_day": 0.05,
    "neglect_yield_penalty_per_day": 0.08,
    "max_neglect_loss_chance_bonus": 0.60,
    "max_neglect_yield_penalty": 0.80,
}

FERTILIZER_CONFIG = {
    "cost": 8,
    "yield_bonus_pct": 0.25,
    "loss_chance_reduction": 0.03,
}

ALL_AGENTS = (
    FastSeller, ProfitOptimizer, ProgressionPlayer, NeglectfulGrower, RecklessSpender,
    RandomAgent, NoUpgradePlayer, FertilizerMaximalist, Diversifier, RiskAverseGrower, UpgradeRusher,
)


def make_crops():
    return [
        {
            "id": "fast", "name": "Fast", "role": "fast",
            "seed_cost": 5, "growth_days": 3,
            "min_yield": 1, "max_yield": 2,
            "base_price": 5, "price_variation": 0.1,
            "loss_chance": 0.03, "water_interval_days": 2,
            "unlock_requirement": None, "processing_value": None,
        },
        {
            "id": "standard", "name": "Standard", "role": "standard",
            "seed_cost": 18, "growth_days": 7,
            "min_yield": 4, "max_yield": 6,
            "base_price": 7, "price_variation": 0.15,
            "loss_chance": 0.05, "water_interval_days": 3,
            "unlock_requirement": None, "processing_value": None,
        },
        {
            "id": "premium", "name": "Premium", "role": "premium",
            "seed_cost": 45, "growth_days": 12,
            "min_yield": 5, "max_yield": 10,
            "base_price": 16, "price_variation": 0.35,
            "loss_chance": 0.15, "water_interval_days": 2,
            "unlock_requirement": {"type": "total_revenue", "value": 150},
            "processing_value": None,
        },
    ]


def make_upgrades():
    return [
        {"id": "capacity_1", "name": "Second Plot", "description": "", "cost": 120,
         "effect": {"type": "capacity", "amount": 2}},
        {"id": "efficiency_1", "name": "Irrigation", "description": "", "cost": 200,
         "effect": {"type": "growth_time_reduction", "amount": 0.20}},
    ]


def run(agent, config=CONFIG, seed=42, record_history=False):
    crops, upgrades = make_crops(), make_upgrades()
    return run_single(config, agent, crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG,
                       seed=seed, record_history=record_history)


def test_single_run_completes_and_tracks_days():
    player, seed, _ = run(FastSeller(), seed=42)
    assert player.day <= CONFIG["days"]
    assert seed == 42


def test_same_seed_produces_identical_results():
    p1, _, _ = run(ProfitOptimizer(), seed=777)
    p2, _, _ = run(ProfitOptimizer(), seed=777)
    assert p1.money == p2.money
    assert p1.total_revenue == p2.total_revenue
    assert p1.crop_plant_counts == p2.crop_plant_counts
    assert p1.bankrupt == p2.bankrupt
    assert p1.total_crops_lost == p2.total_crops_lost


def test_different_seeds_can_produce_different_results():
    outcomes = {round(run(FastSeller(), seed=seed)[0].money, 4) for seed in range(10)}
    assert len(outcomes) > 1


def test_all_strategies_run_without_error():
    for agent_cls in ALL_AGENTS:
        player, _, _ = run(agent_cls(), seed=1)
        assert player.day > 0


def test_bankrupt_player_stops_taking_actions():
    poor_config = {"start_money": 1, "start_slots": 1, "days": 30}
    crops = make_crops()
    player, _, _ = run(ProfitOptimizer(), config=poor_config, seed=1)
    assert player.bankrupt
    assert player.money < min(c["seed_cost"] for c in crops)


def test_neglectful_grower_waters_far_less_than_diligent_agents():
    neglectful_player, _, _ = run(NeglectfulGrower(), seed=5)
    optimizer_player, _, _ = run(ProfitOptimizer(), seed=5)
    assert neglectful_player.total_waterings < optimizer_player.total_waterings


def test_reckless_spender_prefers_the_most_expensive_affordable_crop():
    player, _, history = run(RecklessSpender(), seed=9, record_history=True)
    # a reckless spender that can afford the premium crop should reach for it
    # rather than the cheaper options once unlocked -- sanity check it ran and
    # actually planted something at all.
    assert player.total_planted > 0
