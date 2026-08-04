"""Pure economic calculations shared by the engine and agents.

Kept free of RNG and of PlayerState mutation so agents can call these to
evaluate options without side effects.
"""


def is_crop_unlocked(crop: dict, player) -> bool:
    req = crop.get("unlock_requirement")
    if not req:
        return True
    if req["type"] == "total_revenue":
        return player.total_revenue >= req["value"]
    if req["type"] == "upgrade":
        return req["id"] in player.upgrades_owned
    raise ValueError(f"Unknown unlock_requirement type: {req['type']}")


def effective_growth_days(crop: dict, player, upgrades_by_id: dict) -> int:
    """Growth duration if planted right now, given currently owned upgrades."""
    days = crop["growth_days"]
    for upgrade_id in player.upgrades_owned:
        effect = upgrades_by_id[upgrade_id]["effect"]
        if effect["type"] == "growth_time_reduction":
            days = max(1, round(days * (1 - effect["amount"])))
    return days


def expected_profit_per_day(crop: dict, player, upgrades_by_id: dict) -> float:
    """Rough expected profit per growing slot per day, used by agents to rank crops.

    Assumes the farm is watered on schedule (no neglect) -- an agent with
    imperfect watering_diligence will realize less than this in practice.
    """
    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    avg_revenue = avg_yield * crop["base_price"] * (1 - crop["loss_chance"])
    days = effective_growth_days(crop, player, upgrades_by_id)
    profit = avg_revenue - crop["seed_cost"]
    return profit / days


def fertilizer_expected_marginal_profit(crop: dict, fertilizer_config: dict) -> float:
    """Expected extra profit from fertilizing one planting of this crop, ignoring
    watering neglect. Positive means fertilizer is worth its cost on average.
    """
    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    original_loss_chance = crop["loss_chance"]
    reduced_loss_chance = max(0.0, original_loss_chance - fertilizer_config["loss_chance_reduction"])

    yield_bonus = avg_yield * fertilizer_config["yield_bonus_pct"]
    revenue_from_yield_bonus = yield_bonus * crop["base_price"] * (1 - original_loss_chance)
    revenue_from_loss_reduction = (original_loss_chance - reduced_loss_chance) * avg_yield * crop["base_price"]

    return revenue_from_yield_bonus + revenue_from_loss_reduction - fertilizer_config["cost"]


def fertilizer_safety_value(crop: dict, fertilizer_config: dict) -> float:
    """Expected value from fertilizing for its loss-chance reduction alone,
    ignoring the yield-bonus component. Used by agents that fertilize for
    safety rather than yield.
    """
    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    original_loss_chance = crop["loss_chance"]
    reduced_loss_chance = max(0.0, original_loss_chance - fertilizer_config["loss_chance_reduction"])
    revenue_from_loss_reduction = (original_loss_chance - reduced_loss_chance) * avg_yield * crop["base_price"]
    return revenue_from_loss_reduction - fertilizer_config["cost"]
