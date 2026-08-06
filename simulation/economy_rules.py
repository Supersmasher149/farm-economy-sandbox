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
    # The owned-upgrade set has no stable iteration order. Fold in the order
    # supplied by the configuration list so each rounded intermediate result
    # is reproducible across processes and Python runs.
    for upgrade_id, upgrade in upgrades_by_id.items():
        if upgrade_id not in player.upgrades_owned:
            continue
        effect = upgrade["effect"]
        if effect["type"] == "growth_time_reduction":
            days = max(1, round(days * (1 - effect["amount"])))
    return days


def expected_profit_per_day(crop: dict, player, upgrades_by_id: dict) -> float:
    """Nominal expected profit per growing slot per day, used to rank crops.

    Assumes the farm is watered on schedule (no neglect) -- an agent with
    imperfect watering_diligence will realize less than this in practice. It
    also does not price environmental quality risk, so callers must enforce a
    working-capital floor before treating this as an actionable choice.
    """
    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    avg_revenue = avg_yield * crop["base_price"] * (1 - crop["loss_chance"])
    days = effective_growth_days(crop, player, upgrades_by_id)
    profit = avg_revenue - crop["seed_cost"]
    return profit / days


def operating_reserve(player) -> float:
    return max(0.0, getattr(player, "operating_reserve", 0.0))


def can_spend_with_reserve(player, amount: float) -> bool:
    return player.money >= amount + operating_reserve(player)


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


# How sharply a crop's realized revenue gets discounted as soil nutrients
# run low, scaled by that crop's own nutrient demand -- see soil_quality_risk.
NUTRIENT_RISK_SENSITIVITY = 18.0

# Matches crop_growth.harvest_multipliers' same-family replant penalty
# (quality_multiplier *= 0.9) closely enough to bias crop selection away
# from monocropping without trying to be an exact model of it.
SAME_FAMILY_REPLANT_DISCOUNT = 0.1


def soil_health_factor(player) -> float:
    """0..1 signal for how depleted the farm's plots are on average, using
    the scarcest of nitrogen/phosphorus/potassium per plot (a crop draws on
    all three; running low on any one is enough to hurt quality). 1.0 means
    healthy soil (or a player with no simulated plots, e.g. a direct
    unit-test call); 0.0 means fully depleted.
    """
    if not player.plots:
        return 1.0
    levels = [min(plot.nitrogen, plot.phosphorus, plot.potassium) for plot in player.plots]
    return sum(levels) / len(levels)


def soil_quality_risk(crop: dict, player) -> float:
    """Fraction (0..~0.95) of a crop's nominal revenue that's at risk from
    growing it in the farm's current soil conditions -- a risk
    expected_profit_per_day cannot see at all, because it treats every
    planting as if it always sells at full nominal yield and price. Plots
    never regenerate nitrogen/phosphorus/potassium naturally (only
    fertilizer restores them; see simulation/crop_growth.py), so repeatedly
    planting nutrient-hungry crops (higher crop["nutrient_demand"]) into
    depleted soil is what pushes real harvests into a discounted or
    outright-rejected quality grade despite a great-looking nominal EV.
    Scales with both how depleted the soil currently is and how
    nutrient-hungry this specific crop is, so an undemanding crop like
    quickweed stays nearly unaffected even in badly depleted soil, while a
    demanding crop like purplehaze is discounted hard -- the same asymmetry
    that lets a "dumb" always-plant-quickweed strategy quietly outlast a
    nominal-EV-only one over a long run.
    """
    health = soil_health_factor(player)
    demand = crop.get("nutrient_demand", {"nitrogen": 0.02, "phosphorus": 0.01, "potassium": 0.01})
    demand_weight = sum(demand.values())
    nutrient_risk = (1.0 - health) * demand_weight * NUTRIENT_RISK_SENSITIVITY

    family = crop.get("family")
    same_family_fraction = (
        sum(1 for plot in player.plots if family and plot.previous_crop_family == family) / len(player.plots)
        if player.plots else 0.0
    )
    family_risk = same_family_fraction * SAME_FAMILY_REPLANT_DISCOUNT

    return min(0.95, nutrient_risk + family_risk)


def quality_adjusted_profit_per_day(crop: dict, player, upgrades_by_id: dict) -> float:
    """Like expected_profit_per_day, but discounts revenue for the
    quality-rejection risk implied by the farm's current soil conditions
    (see soil_quality_risk). A crop whose nominal EV looks great but whose
    harvest would land in a discounted or rejected quality grade given
    today's plot nutrient levels realizes little of that nominal revenue --
    this is the gap that let a nominal-EV-only ranking walk a farm into
    depleted soil and near-zero real returns over a long run.
    """
    avg_yield = (crop["min_yield"] + crop["max_yield"]) / 2
    avg_revenue = avg_yield * crop["base_price"] * (1 - crop["loss_chance"])
    realized_revenue = avg_revenue * (1.0 - soil_quality_risk(crop, player))

    days = effective_growth_days(crop, player, upgrades_by_id)
    profit = realized_revenue - crop["seed_cost"]
    return profit / days


def crop_seed_reserve_gate(crop: dict, player, reserve_fraction: float = 1.0) -> bool:
    """Like can_spend_with_reserve, but scaled by a fraction of the reserve.

    Used to build a graduated fallback ladder for crop selection instead of
    an all-or-nothing gate: try the full reserve first, then relax it in
    steps, rather than collapsing straight to a zero-reserve fallback the
    moment any single crop fails the full check.
    """
    return player.money >= crop["seed_cost"] + operating_reserve(player) * reserve_fraction


# Below this soil_health_factor, stop ranking by (even quality-adjusted)
# profit-per-day entirely and switch to pure triage: plant whichever
# candidate is gentlest on the soil. A smooth EV discount still leaves room
# for a high-nominal-EV crop to out-rank a low-demand one on paper right up
# until the soil is nearly gone; once the farm is in a genuine crisis, the
# only thing that matters is stopping further depletion, which nominal
# profit -- discounted or not -- doesn't reliably capture on its own.
CRITICAL_SOIL_HEALTH = 0.35


def best_crop_by_expected_profit(candidates: list, player, upgrades_by_id: dict) -> dict | None:
    """Highest quality-adjusted-profit-per-day crop among candidates, or
    None -- except below CRITICAL_SOIL_HEALTH, where the lowest-nutrient-
    demand candidate is chosen outright regardless of EV, matching what an
    always-plant-the-gentlest-crop strategy would do to stop the bleeding.
    """
    if not candidates:
        return None
    if soil_health_factor(player) < CRITICAL_SOIL_HEALTH:
        return min(candidates, key=lambda c: sum(c.get("nutrient_demand", {}).values()))
    return max(candidates, key=lambda c: quality_adjusted_profit_per_day(c, player, upgrades_by_id))


def choose_crop_with_relaxed_reserve(
    candidates: list, player, upgrades_by_id: dict,
    reserve_fractions: tuple = (1.0, 0.5),
) -> dict | None:
    """Rank affordable candidates by EV/day at the full reserve; if none
    clear it, retry at each progressively relaxed fraction. Never relaxes to
    0% of the reserve, so a cash floor is always enforced whenever anything
    can meet it. Returns None only if nothing clears even the loosest
    fraction -- callers should apply their own last-resort fallback then.
    """
    for fraction in reserve_fractions:
        safe = [c for c in candidates if crop_seed_reserve_gate(c, player, fraction)]
        if safe:
            return best_crop_by_expected_profit(safe, player, upgrades_by_id)
    return None


def upgrade_payback_days(upgrade: dict, player, crops_by_id: dict, upgrades_by_id: dict) -> float | None:
    """Days of incremental profit needed to recoup this upgrade's cost,
    priced off the single best-EV crop the player could plant today.

    Returns None whenever the upgrade can't be priced this way: an empty
    crop catalog (e.g. a direct unit-test call bypassing the engine), no
    unlocked crop, non-positive best EV, or an effect type with no pricing
    model here (storage, or any future unmodeled type). Callers must treat
    None as "unknown, skip this check" -- never as "free to buy."
    """
    if not crops_by_id:
        return None
    unlocked = [c for c in crops_by_id.values() if is_crop_unlocked(c, player)]
    if not unlocked:
        return None
    affordable = [c for c in unlocked if player.money >= c["seed_cost"]]
    viable = affordable or unlocked
    best_profit_per_day = max(expected_profit_per_day(c, player, upgrades_by_id) for c in viable)
    if best_profit_per_day <= 0:
        return None

    effect = upgrade.get("effect", {})
    effect_type = effect.get("type")
    if effect_type == "capacity":
        added_value_per_day = best_profit_per_day * effect.get("amount", 0)
    elif effect_type == "growth_time_reduction":
        amount = effect.get("amount", 0)
        if amount <= 0 or amount >= 1:
            return None
        added_value_per_day = player.slots_total * best_profit_per_day * (amount / (1 - amount))
    elif effect_type == "processing_capacity":
        # No recipe economics are visible from here; price one slot
        # conservatively rather than not pricing it at all.
        added_value_per_day = 0.5 * best_profit_per_day * effect.get("amount", 0)
    else:
        return None

    if added_value_per_day <= 0:
        return None
    return upgrade["cost"] / added_value_per_day


def should_buy_upgrade_within_budget(
    player, upgrade,
    cooldown_days: int = 6,
    min_payback_multiple: float = 2.0,
    max_cumulative_spend_fraction: float = 0.6,
    default_payback_horizon_days: int = 60,
) -> bool:
    """Composite upgrade-purchase gate.

    Beyond the existing reserve check, this adds three guards against the
    single-day upgrade cascade that can drain most of a farm's starting
    capital before it has produced anything: a cooldown since the last
    upgrade purchase, a cap on cumulative upgrade spend relative to the
    farm's peak cash, and -- when the upgrade can be priced -- a
    payback-period check against the days remaining in the run.
    """
    if not can_spend_with_reserve(player, upgrade["cost"]):
        return False

    if player.upgrade_purchase_days:
        days_since_last = player.day - max(player.upgrade_purchase_days.values())
        if days_since_last < cooldown_days:
            return False

    peak_money = player.highest_money if player.highest_money is not None else player.money
    spent_on_upgrades = player.expenses_by_category.get("upgrades", 0.0)
    if peak_money <= 0 or spent_on_upgrades + upgrade["cost"] > peak_money * max_cumulative_spend_fraction:
        return False

    payback = upgrade_payback_days(upgrade, player, player.crop_catalog, player.upgrades_catalog)
    if payback is not None:
        remaining_days = (
            player.total_days - player.day if getattr(player, "total_days", None) is not None
            else default_payback_horizon_days
        )
        if payback * min_payback_multiple > remaining_days:
            return False

    return True
