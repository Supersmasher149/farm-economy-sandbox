"""Buyer contract offers, deliveries, and deadline resolution."""
from simulation import crop_growth, economy_rules, inventory, markets
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


def _effective_deadline(player, deadline: int) -> int:
    """A deadline past the run's own end is never reachable: no `run_day`
    call ever executes with `player.day >= player.total_days` (the loop in
    runner.single_run runs exactly `total_days` times), so nothing can be
    grown, processed, or delivered after that point regardless of what a
    contract's own deadline_day says. Every production forecast in this
    module must be capped here, not just at the contract's own deadline.
    """
    total_days = getattr(player, "total_days", None)
    return min(deadline, total_days) if total_days is not None else deadline


def _best_possible_grade(planted, crop: dict, plot) -> str:
    """The best quality grade an already-planted crop could still reach.

    simulation.crop_growth.update_crop_stress only ever adds to a planted
    crop's accumulated stress fields between now and harvest -- nothing
    reduces them -- so today's stress is a floor on quality_stress and
    therefore a ceiling on the achievable grade. A crop already stressed
    enough that even this best case grades below a contract's min_quality
    can never satisfy it and must not be forecast as future supply for it.
    """
    _yield_multiplier, quality_score = crop_growth.harvest_multipliers(planted, crop, plot)
    return crop_growth.quality_grade(quality_score)


def _future_crop_capacity(
    player, crop: dict, deadline: int, min_quality: str = "standard"
) -> tuple[float, float, float]:
    """Return future safe yield and seed cash needed, excluding inventory."""
    # Future harvest grade is not guaranteed. Standard is the highest grade
    # this conservative estimate can promise without a quality forecast.
    if QUALITY_ORDER.get(min_quality, 0) > QUALITY_ORDER["standard"]:
        return 0.0, 0.0, 0.0
    deadline = _effective_deadline(player, deadline)
    growth_days = max(1, economy_rules.effective_growth_days(crop, player, player.upgrades_catalog))
    days_available = max(0, deadline - player.day)
    expected_yield = (
        (crop["min_yield"] + crop["max_yield"]) / 2
        * (1 - crop.get("loss_chance", 0.0))
        * getattr(player, "contract_config", {}).get(
            "production_safety_factor", PRODUCTION_SAFETY_FACTOR
        )
    )
    min_quality_rank = QUALITY_ORDER.get(min_quality, 0)
    free_cycles = 0
    seeded_cycles = 0
    for _ in range(max(0, player.open_slots)):
        seeded_cycles += days_available // growth_days
    for planted in player.planted:
        days_until_free = max(0, planted.growth_days_required - (player.day - planted.day_planted))
        if planted.crop_id == crop["id"]:
            if days_until_free > days_available:
                continue
            plot = (
                player.plots[planted.plot_index]
                if planted.plot_index is not None and planted.plot_index < len(player.plots)
                else None
            )
            if QUALITY_ORDER[_best_possible_grade(planted, crop, plot)] >= min_quality_rank:
                free_cycles += 1
            seeded_cycles += max(0, (days_available - days_until_free) // growth_days)
        else:
            if days_until_free < days_available:
                seeded_cycles += (days_available - days_until_free) // growth_days

    seed_inventory = player.seed_inventory.get(crop["id"], 0)
    seed_cost = crop["seed_cost"]
    # A validly-configured crop may cost 0 (a free starter crop); cash can
    # never be the limiting factor there, so cap at seeded_cycles itself
    # rather than floor-dividing by a seed cost that may be zero.
    if seed_cost > 0:
        cash_seed_units = int(max(0.0, player.money - economy_rules.operating_reserve(player)) // seed_cost)
    else:
        cash_seed_units = seeded_cycles
    funded_seeded_cycles = min(seeded_cycles, seed_inventory + cash_seed_units)
    purchased = max(0, funded_seeded_cycles - seed_inventory)
    return (
        (free_cycles + funded_seeded_cycles) * expected_yield,
        purchased * seed_cost,
        free_cycles * expected_yield,
    )


def _item_capacity(player, item_id: str, min_quality: str, deadline: int, seen=()) -> tuple[float, float, float]:
    """Return current quantity, future quantity, and future funding needed."""
    deadline = _effective_deadline(player, deadline)
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
    if QUALITY_ORDER.get(min_quality, 0) > QUALITY_ORDER["standard"]:
        return current, future, funding

    days_available = max(0, deadline - player.day)
    # Capacity as slot-*days*, not a static "currently free slots" count: an
    # existing job frees its slot the moment it completes, and that freed
    # slot is available for further batches for whatever's left of the
    # window -- a job completing tomorrow with a 30-day-out deadline should
    # not permanently look like an occupied slot for the whole window.
    occupied_slot_days = sum(
        max(0, min(job.completion_day, deadline) - player.day) for job in player.processing_jobs
    )
    capacity_slot_days = max(0, _processing_capacity(player) * days_available - occupied_slot_days)

    # Multiple recipes producing this item can compete for the same input
    # inventory and the same input crop's future yield -- each already-seen
    # recipe in this loop reserves what it claims here, so a later recipe's
    # batch count is computed against what's actually left, not the full,
    # unreserved pool (previously two recipes sharing one input could each
    # be forecast as if the other didn't exist).
    #
    # Cash to fund new plantings is NOT similarly reserved across recipes
    # here (each recipe's funding need is still priced against the full
    # cash pool) -- joint cash contention across multiple simultaneous
    # forecasts is a pre-existing simplification this fix does not extend
    # to (is_offer_feasible already evaluates each contract's own cash need
    # independently of every other contract for the same reason).
    reserved_current: dict = {}
    reserved_future: dict = {}
    for recipe in _recipes(player):
        if recipe.get("output_item_id") != item_id or capacity_slot_days <= 0:
            continue
        recipe_days = max(1, recipe.get("processing_days", 1))
        if player.day + recipe_days > deadline:
            continue  # cannot complete even one batch of this recipe in time

        input_id = recipe["input_item_id"]
        input_min_quality = recipe.get("min_quality", "processing")
        input_current_total = _inventory_quantity(player, input_id, input_min_quality)
        already_current = reserved_current.get(input_id, 0)
        input_current = max(0, input_current_total - already_current)

        input_future_total, input_funding = 0.0, 0.0
        input_crop = player.crop_catalog.get(input_id)
        if input_crop is not None:
            input_future_total, input_funding, _free_input = _future_crop_capacity(
                player, input_crop, deadline, input_min_quality
            )
        already_future = reserved_future.get(input_id, 0.0)
        input_future = max(0.0, input_future_total - already_future)

        batches_by_input = int((input_current + input_future) // recipe["input_quantity"])
        batches_by_capacity = capacity_slot_days // recipe_days
        batches = min(batches_by_input, batches_by_capacity)
        if batches <= 0:
            continue

        future += batches * recipe["output_quantity"]
        needed_input = batches * recipe["input_quantity"]
        used_current = min(input_current, needed_input)
        used_future = needed_input - used_current
        reserved_current[input_id] = already_current + used_current
        if used_future:
            reserved_future[input_id] = already_future + used_future
            if input_future_total:
                funding += input_funding * min(1.0, used_future / input_future_total)
        funding += batches * recipe.get("cost", 0.0)
        capacity_slot_days -= batches * recipe_days
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
