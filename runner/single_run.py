"""Run one agent through one simulation, optionally recording daily history.

Every run is fully reproducible: pass the same `seed` back in to replay it
exactly. If `seed` is None, a fresh seed is generated and returned.
"""

from simulation import engine
from simulation.random_events import RandomEvents
from simulation.state import PlayerState


def run_single(
    config: dict,
    agent,
    crops: list,
    upgrades: list,
    watering_settings=None,
    fertilizer_config=None,
    seed=None,
    record_history: bool = False,
    world=None,
):
    crops_by_id = {c["id"]: c for c in crops}
    upgrades_by_id = {u["id"]: u for u in upgrades}

    rng = RandomEvents(seed)
    player = PlayerState(
        money=config["start_money"],
        slots_total=config["start_slots"],
        operating_reserve=config.get("operating_reserve", 0.0),
        total_days=config.get("days"),
    )
    if world:
        initial_soil = world.get("soil", {}).get("initial", {})
        for plot in player.plots:
            for name, value in initial_soil.items():
                if hasattr(plot, name):
                    setattr(plot, name, value)
    player.lowest_money = player.money
    player.highest_money = player.money

    history = [] if record_history else None

    for _ in range(config["days"]):
        if player.bankrupt:
            break
        engine.run_day(
            player,
            agent,
            crops,
            crops_by_id,
            upgrades,
            upgrades_by_id,
            watering_settings or {},
            fertilizer_config or {},
            rng,
            world=world,
        )
        if record_history:
            history.append(
                {
                    "day": player.day,
                    "money": round(player.money, 2),
                    "slots_total": player.slots_total,
                    "planted_count": len(player.planted),
                    "crop_inventory": dict(player.crop_inventory),
                    "upgrades_owned": sorted(player.upgrades_owned),
                    "total_revenue": round(player.total_revenue, 2),
                    "total_expenses": round(player.total_expenses, 2),
                    "expenses_by_category": {
                        key: round(value, 2) for key, value in player.expenses_by_category.items()
                    },
                    "bankrupt": player.bankrupt,
                    "bankruptcy_day": player.bankruptcy_day,
                    "fertilizer_inventory": player.fertilizer_inventory,
                    "weather": dict(player.current_weather),
                    "market_prices": {
                        key: round(value, 2) for key, value in player.market_prices.items()
                    },
                    "inventory_lots": [
                        {
                            "item_id": lot.item_id,
                            "quantity": lot.quantity,
                            "quality": lot.quality,
                            "age": lot.age_days,
                        }
                        for lot in player.inventory_lots
                    ],
                    "reputation": round(player.reputation, 2),
                    "occupied_slot_days": player.occupied_slot_days,
                }
            )

    return player, rng.seed, history
