import pytest

from simulation.state import PlayerState


@pytest.fixture
def fast_crop():
    return {
        "id": "fast", "name": "Fast", "role": "fast",
        "seed_cost": 5, "growth_days": 3,
        "min_yield": 1, "max_yield": 2,
        "base_price": 5, "price_variation": 0.1,
        "loss_chance": 0.0, "water_interval_days": 2,
        "unlock_requirement": None, "processing_value": None,
    }


@pytest.fixture
def standard_crop():
    return {
        "id": "standard", "name": "Standard", "role": "standard",
        "seed_cost": 18, "growth_days": 7,
        "min_yield": 4, "max_yield": 6,
        "base_price": 7, "price_variation": 0.15,
        "loss_chance": 0.0, "water_interval_days": 3,
        "unlock_requirement": None, "processing_value": None,
    }


@pytest.fixture
def premium_crop():
    return {
        "id": "premium", "name": "Premium", "role": "premium",
        "seed_cost": 45, "growth_days": 12,
        "min_yield": 5, "max_yield": 10,
        "base_price": 16, "price_variation": 0.35,
        "loss_chance": 0.15, "water_interval_days": 2,
        "unlock_requirement": {"type": "total_revenue", "value": 150},
        "processing_value": None,
    }


@pytest.fixture
def watering_settings():
    return {
        "neglect_loss_chance_penalty_per_day": 0.05,
        "neglect_yield_penalty_per_day": 0.08,
        "max_neglect_loss_chance_bonus": 0.60,
        "max_neglect_yield_penalty": 0.80,
    }


@pytest.fixture
def fertilizer_config():
    return {
        "cost": 8,
        "yield_bonus_pct": 0.25,
        "loss_chance_reduction": 0.03,
    }


@pytest.fixture
def capacity_upgrade():
    return {
        "id": "capacity_1", "name": "Second Plot", "description": "",
        "cost": 120, "effect": {"type": "capacity", "amount": 2},
    }


@pytest.fixture
def efficiency_upgrade():
    return {
        "id": "efficiency_1", "name": "Irrigation", "description": "",
        "cost": 200, "effect": {"type": "growth_time_reduction", "amount": 0.20},
    }


@pytest.fixture
def player():
    p = PlayerState(money=100, slots_total=2)
    p.lowest_money = p.money
    p.highest_money = p.money
    return p
