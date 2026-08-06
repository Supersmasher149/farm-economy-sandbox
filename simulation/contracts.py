"""Buyer contract offers, deliveries, and deadline resolution."""
from simulation import economy_rules, inventory, markets
from simulation.state import ContractState, QUALITY_ORDER

PRODUCTION_SAFETY_FACTOR = 0.45
DEFAULT_OFFER_EXPIRY_DAYS = 3


def offer_expiry_day(player, offer) -> int:
    expiry = getattr(player, "contract_config", {}).get(
        "offer_expiry_days", DEFAULT_OFFER_EXPIRY_DAYS
    )
    return offer.offered_day + expiry


def is_offer_expired(player, offer) -> bool:
    return player.day > offer_expiry_day(player, offer)


def visible_offers(player, offers=None) -> list:
    """Return unresolved offers that are still visible to an agent.

    ``offers`` is optional so the engine can pass a merged list while callers
    that only need retained offers can use the player state directly.
    """
    source = player.contract_offers if offers is None else offers
    return [offer for offer in source if not offer.resolved and not is_offer_expired(player, offer)]


def generate_offers(player, contract_config: dict, buyers: list, items_by_id: dict, rng) -> list:
    # Keep the expiry policy available to visibility and acceptance helpers
    # even when this API is used without the full engine setup.
    player.contract_config = contract_config
    interval = contract_config.get("offer_interval_days", 7)
    if player.day == 0 or player.day % interval != 0:
        return []
    player.contract_offers = visible_offers(player)
    unresolved_ids = {contract.id for contract in player.contract_offers + player.active_contracts if not contract.resolved}
    offers = []
    for buyer in buyers:
        if player.reputation < buyer.get("min_reputation", 0):
            continue
        eligible = [item_id for item_id in buyer.get("items", []) if item_id in items_by_id]
        if not eligible:
            continue
        item_id = rng.choice(eligible)
        identifier = f"{buyer['id']}-{item_id}-{player.day}"
        if identifier in unresolved_ids:
            continue
        quantity_range = buyer.get("quantity_range", [5, 12])
        quantity = rng.roll_yield(quantity_range[0], quantity_range[1])
        base = items_by_id[item_id].get("base_price", items_by_id[item_id].get("processed_base_price", 1.0))
        offers.append(ContractState(
            id=identifier,
            buyer_id=buyer["id"],
            item_id=item_id,
            quantity=quantity,
            min_quality=buyer.get("min_quality", "standard"),
            unit_price=base * buyer.get("contract_price_multiplier", 1.2),
            offered_day=player.day,
            deadline_day=player.day + buyer.get("deadline_days", 10),
            penalty_rate=buyer.get("penalty_rate", contract_config.get("default_penalty_rate", 0.35)),
        ))
    player.contract_offers.extend(offers)
    return offers


def accept(player, contract_id: str) -> bool:
    contract = next((offer for offer in player.contract_offers if offer.id == contract_id and not offer.resolved), None)
    if contract is None:
        return False
    if is_offer_expired(player, contract):
        player.contract_offers.remove(contract)
        return False
    contract.accepted = True
    player.contract_offers.remove(contract)
    player.active_contracts.append(contract)
    return True


def best_market_alternative(player, contract) -> float:
    """Return the best current net unit value for a contract's item and grade."""
    alternatives = []
    for channel in getattr(player, "market_channels", []):
        quote = markets.quote(
            player,
            contract.item_id,
            contract.min_quality,
            channel,
            contract.quantity,
        )
        if quote:
            alternatives.append(quote["net"] / quote["quantity"])
    if alternatives:
        return max(alternatives)
    market_price = player.market_prices.get(contract.item_id, 0.0)
    return market_price * 1.15


def is_offer_profitable(player, contract) -> bool:
    """Contracts must beat the best available sale channel, not raw price."""
    return contract.unit_price > best_market_alternative(player, contract)


def _inventory_quantity(player, item_id: str, min_quality: str) -> int:
    threshold = QUALITY_ORDER[min_quality]
    return sum(
        lot.quantity
        for lot in player.inventory_lots
        if lot.item_id == item_id
        and lot.quantity > 0
        and lot.remaining_shelf_life > 0
        and QUALITY_ORDER[lot.quality] >= threshold
    )


def available_quantity(player, item_id: str, min_quality: str = "rejected") -> int:
    """Return non-expired inventory eligible for a contract or recipe."""
    return _inventory_quantity(player, item_id, min_quality)


def _recipes(player) -> list[dict]:
    configured = getattr(player, "processing_recipes", None)
    if configured:
        return configured
    return getattr(player, "contract_config", {}).get("recipes", [])


def _processing_capacity(player) -> int:
    configured = getattr(player, "processing_capacity", None)
    if configured is not None:
        return configured
    return getattr(player, "contract_config", {}).get("processing_capacity", 0)


def _future_crop_capacity(
    player, crop: dict, deadline: int, min_quality: str = "standard"
) -> tuple[float, float, float]:
    """Return future safe yield and seed cash needed, excluding inventory."""
    # Future harvest grade is not guaranteed. Standard is the highest grade
    # this conservative estimate can promise without a quality forecast.
    if QUALITY_ORDER.get(min_quality, 0) > QUALITY_ORDER["standard"]:
        return 0.0, 0.0, 0.0
    growth_days = max(1, economy_rules.effective_growth_days(crop, player, player.upgrades_catalog))
    days_available = max(0, deadline - player.day)
    expected_yield = (
        (crop["min_yield"] + crop["max_yield"]) / 2
        * (1 - crop.get("loss_chance", 0.0))
        * getattr(player, "contract_config", {}).get(
            "production_safety_factor", PRODUCTION_SAFETY_FACTOR
        )
    )
    free_cycles = 0
    seeded_cycles = 0
    for _ in range(max(0, player.open_slots)):
        seeded_cycles += days_available // growth_days
    for planted in player.planted:
        days_until_free = max(0, planted.growth_days_required - (player.day - planted.day_planted))
        if planted.crop_id == crop["id"]:
            if days_until_free > days_available:
                continue
            free_cycles += 1
            seeded_cycles += max(0, (days_available - days_until_free) // growth_days)
        else:
            if days_until_free < days_available:
                seeded_cycles += (days_available - days_until_free) // growth_days

    seed_inventory = player.seed_inventory.get(crop["id"], 0)
    seed_cost = crop["seed_cost"]
    cash_seed_units = int(max(0.0, player.money - economy_rules.operating_reserve(player)) // seed_cost)
    funded_seeded_cycles = min(seeded_cycles, seed_inventory + cash_seed_units)
    purchased = max(0, funded_seeded_cycles - seed_inventory)
    return (
        (free_cycles + funded_seeded_cycles) * expected_yield,
        purchased * seed_cost,
        free_cycles * expected_yield,
    )


def _item_capacity(player, item_id: str, min_quality: str, deadline: int, seen=()) -> tuple[float, float, float]:
    """Return current quantity, future quantity, and future funding needed."""
    current = _inventory_quantity(player, item_id, min_quality)
    for job in player.processing_jobs:
        if (
            job.output_item_id == item_id
            and job.completion_day <= deadline
            and QUALITY_ORDER.get(min_quality, 0) <= QUALITY_ORDER["standard"]
        ):
            current += job.output_quantity

    crop = player.crop_catalog.get(item_id)
    if crop is not None:
        future, funding, _free_future = _future_crop_capacity(player, crop, deadline, min_quality)
        return current, future, funding
    if item_id in seen:
        return current, 0.0, 0.0

    future = 0.0
    funding = 0.0
    free_capacity = max(0, _processing_capacity(player) - len(player.processing_jobs))
    for recipe in _recipes(player):
        if recipe.get("output_item_id") != item_id or free_capacity <= 0:
            continue
        if QUALITY_ORDER.get(min_quality, 0) > QUALITY_ORDER["standard"]:
            continue
        input_current = _inventory_quantity(
            player, recipe["input_item_id"], recipe.get("min_quality", "processing")
        )
        input_future, input_funding = 0.0, 0.0
        input_crop = player.crop_catalog.get(recipe["input_item_id"])
        if input_crop is not None:
            input_future, input_funding, _free_input = _future_crop_capacity(
                player, input_crop, deadline, recipe.get("min_quality", "processing")
            )
        batches = min(
            free_capacity,
            int((input_current + input_future) // recipe["input_quantity"]),
        )
        if batches <= 0:
            continue
        future += batches * recipe["output_quantity"]
        future_input = max(0, batches * recipe["input_quantity"] - input_current)
        if input_future:
            funding += input_funding * min(1.0, future_input / input_future)
        funding += batches * recipe.get("cost", 0.0)
        free_capacity -= batches
    return current, future, funding


def producible_quantity(player, contract) -> float:
    """Estimate eligible stock plus safely fundable future supply."""
    current, future, _funding = _item_capacity(
        player, contract.item_id, contract.min_quality, contract.deadline_day
    )
    return current + future


def forecast_committed_supply(player, contract) -> float:
    """Supply already locked in toward a contract without any further
    planting decision: eligible inventory, processing output already due by
    the deadline, and the yield already guaranteed by crops already planted
    (crop_growth.py's harvest, not a hypothetical future one).

    Deliberately excludes the "seeded_cycles" component of
    `_future_crop_capacity` (fundable planting into open slots, and
    replanting the same slot again after harvest) -- that component assumes
    the crop being evaluated will keep winning every future planting
    decision, which is exactly the choice a caller is trying to make, not a
    fact already true about the farm. Used by agents deciding whether to
    plant *more* of a contracted crop: if this already meets
    `contract.remaining`, an additional planting would just overshoot.
    """
    current = _inventory_quantity(player, contract.item_id, contract.min_quality)
    for job in player.processing_jobs:
        if (
            job.output_item_id == contract.item_id
            and job.completion_day <= contract.deadline_day
            and QUALITY_ORDER.get(contract.min_quality, 0) <= QUALITY_ORDER["standard"]
        ):
            current += job.output_quantity
    crop = player.crop_catalog.get(contract.item_id)
    if crop is not None:
        _future, _funding, free_future = _future_crop_capacity(
            player, crop, contract.deadline_day, contract.min_quality
        )
        current += free_future
    return current


def is_offer_feasible(player, contract) -> bool:
    if is_offer_expired(player, contract):
        return False
    current, future, funding = _item_capacity(
        player, contract.item_id, contract.min_quality, contract.deadline_day
    )
    if current + future < contract.quantity:
        return False
    missing = max(0.0, contract.quantity - current)
    free_future = 0.0
    crop = player.crop_catalog.get(contract.item_id)
    if crop is not None:
        _future, _funding, free_future = _future_crop_capacity(
            player, crop, contract.deadline_day, contract.min_quality
        )
    paid_future = max(0.0, future - free_future)
    required = funding * (max(0.0, missing - free_future) / paid_future) if paid_future else 0.0
    return required <= max(0.0, player.money - economy_rules.operating_reserve(player))


def deliver(player, contract_id: str, quantity: int) -> tuple[float, int]:
    contract = next((item for item in player.active_contracts if item.id == contract_id and not item.resolved), None)
    if contract is None or player.day > contract.deadline_day:
        return 0.0, 0
    requested = min(quantity, contract.remaining)
    delivered, _cost = inventory.consume(player, contract.item_id, requested, contract.min_quality)
    if delivered <= 0:
        return 0.0, 0
    revenue = delivered * contract.unit_price
    contract.delivered += delivered
    player.money += revenue
    player.track_peak_cash()
    player.total_revenue += revenue
    player.total_sold += delivered
    player.revenue_by_channel["contract"] = player.revenue_by_channel.get("contract", 0.0) + revenue
    if contract.remaining == 0:
        contract.resolved = True
        player.contracts_completed += 1
        player.reputation = min(100.0, player.reputation + 5.0)
    return revenue, delivered


def resolve_expired(player) -> None:
    for contract in player.active_contracts:
        if contract.resolved or player.day <= contract.deadline_day:
            continue
        shortfall_value = contract.remaining * contract.unit_price
        penalty = min(player.money, shortfall_value * contract.penalty_rate)
        player.money -= penalty
        player.record_expense("contract_penalties", penalty)
        player.contract_penalties += penalty
        player.contracts_failed += 1
        player.reputation = max(0.0, player.reputation - 4.0)
        contract.resolved = True

    player.contract_offers = visible_offers(player)
