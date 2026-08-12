"""Buyer contract offers, deliveries, and deadline resolution."""

from simulation import crop_growth, economy_rules, inventory, markets
from simulation.state import QUALITY_ORDER, ContractState
from simulation.validation import is_positive_int

PRODUCTION_SAFETY_FACTOR = 0.45
DEFAULT_OFFER_EXPIRY_DAYS = 3
# Assumed contract premium over the bare market price, used only when no
# sales channel can quote the item at all. Overridable via
# contracts.fallback_price_multiplier.
DEFAULT_FALLBACK_PRICE_MULTIPLIER = 1.15
# Per-buyer relationship: how much a completed/failed contract with a given
# buyer moves that buyer's standing, and how much of a price bump full
# standing can ever earn -- separate from and additional to the global
# `player.reputation` gate. Mirrors reputation's own +5/-4 asymmetry (harder
# to lose than to build) and markets.py's 0.25 cap on its own reputation
# bonus. All three are overridable via contracts.json.
DEFAULT_RELATIONSHIP_GAIN = 6.0
DEFAULT_RELATIONSHIP_LOSS = 5.0
DEFAULT_RELATIONSHIP_BONUS_CAP = 0.25


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


def _relationship_price_multiplier(player, buyer: dict, contract_config: dict) -> float:
    """Price bump this buyer's next offer earns from standing history.

    `buyer_relationships` only holds entries for buyers the farm has already
    done business with, so a buyer never dealt with yet reads as 0.0
    (no bonus) rather than needing to be pre-seeded.
    """
    relationship = player.buyer_relationships.get(buyer["id"], 0.0)
    bonus_rate = buyer.get("relationship_bonus_rate", 0.0)
    cap = contract_config.get("relationship_bonus_cap", DEFAULT_RELATIONSHIP_BONUS_CAP)
    return 1 + min(cap, relationship * bonus_rate)


def generate_offers(player, contract_config: dict, buyers: list, items_by_id: dict, rng) -> list:
    # Keep the expiry policy available to visibility and acceptance helpers
    # even when this API is used without the full engine setup.
    player.contract_config = contract_config
    interval = contract_config.get("offer_interval_days", 7)
    if player.day == 0 or player.day % interval != 0:
        return []
    player.contract_offers = visible_offers(player)
    unresolved_ids = {
        contract.id
        for contract in player.contract_offers + player.active_contracts
        if not contract.resolved
    }
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
        base = items_by_id[item_id].get(
            "base_price", items_by_id[item_id].get("processed_base_price", 1.0)
        )
        price_multiplier = buyer.get(
            "contract_price_multiplier", 1.2
        ) * _relationship_price_multiplier(player, buyer, contract_config)
        offers.append(
            ContractState(
                id=identifier,
                buyer_id=buyer["id"],
                item_id=item_id,
                quantity=quantity,
                min_quality=buyer.get("min_quality", "standard"),
                unit_price=base * price_multiplier,
                offered_day=player.day,
                deadline_day=player.day + buyer.get("deadline_days", 10),
                penalty_rate=buyer.get(
                    "penalty_rate", contract_config.get("default_penalty_rate", 0.35)
                ),
            )
        )
    player.contract_offers.extend(offers)
    return offers


def accept(player, contract_id: str) -> bool:
    contract = next(
        (
            offer
            for offer in player.contract_offers
            if offer.id == contract_id and not offer.resolved
        ),
        None,
    )
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
    return market_price * getattr(player, "contract_config", {}).get(
        "fallback_price_multiplier", DEFAULT_FALLBACK_PRICE_MULTIPLIER
    )


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
    """A deadline past the run's own end is never reachable, so every
    production forecast in this module caps here rather than at the
    contract's own deadline_day.

    Delegates to `economy_rules.effective_deadline`, the single authority for
    the run horizon. This used to cap at `total_days`, one day too late: the
    run's last executed day is `total_days - 1`, so a harvest or processing
    completion landing on `total_days` was counted as deliverable supply even
    though `run_day` is never called again to produce it.
    """
    return economy_rules.effective_deadline(player, deadline)


def _best_possible_grade(planted, crop: dict, plot, dynamics=None) -> str:
    """The best quality grade an already-planted crop could still reach.

    simulation.crop_growth.update_crop_stress only ever adds to a planted
    crop's accumulated stress fields between now and harvest -- nothing
    reduces them -- so today's stress is a floor on quality_stress and
    therefore a ceiling on the achievable grade. A crop already stressed
    enough that even this best case grades below a contract's min_quality
    can never satisfy it and must not be forecast as future supply for it.
    """
    _yield_multiplier, quality_score = crop_growth.harvest_multipliers(
        planted, crop, plot, dynamics=dynamics
    )
    return crop_growth.quality_grade(quality_score)


def _future_crop_arrivals(
    player, crop: dict, deadline: int, min_quality: str = "standard"
) -> tuple[list[int], list[int], float, float]:
    """Return *when* each forecast harvest of `crop` actually lands.

    `(guaranteed_days, seeded_days, yield_per_harvest, seed_cash_needed)`:
    the first list is one day per already-planted crop that can still reach
    `min_quality`, the second is one day per new planting the farm can fund
    into an open (or newly freed) slot. Both are day-sorted.

    The days matter for recipes: pooling a whole window's worth of future
    yield into one total silently lets processing capacity early in the
    window be spent on a crop that is not harvested until the end of it
    (CQ-03). Callers that only need totals go through
    `_future_crop_capacity`, which counts these lists rather than summing
    them so its arithmetic is unchanged.

    Grades above standard are handled more conservatively: future plantings
    are only promised at `standard` (their real grade depends on stress that
    hasn't occurred yet), so a premium-grade target counts only crops already
    planted whose best achievable grade reaches it -- and funds no new
    plantings toward it.
    """
    deadline = _effective_deadline(player, deadline)
    growth_days = max(1, economy_rules.effective_growth_days(crop, player, player.upgrades_catalog))
    days_available = max(0, deadline - player.day)
    expected_yield = (
        (crop["min_yield"] + crop["max_yield"])
        / 2
        * (1 - crop.get("loss_chance", 0.0))
        * getattr(player, "contract_config", {}).get(
            "production_safety_factor", PRODUCTION_SAFETY_FACTOR
        )
    )
    min_quality_rank = QUALITY_ORDER.get(min_quality, 0)

    # A grade above standard cannot be promised of crops not yet planted:
    # their final quality is set by stress accumulated from soil and weather
    # between today and harvest, none of which exists yet. So for such
    # contracts only crops already planted whose best possible grade reaches
    # the minimum (bounded by stress already accumulated -- that only ever
    # grows) count as committed future supply, and no seed cash is forecast
    # to start new plantings whose grade can't be guaranteed.
    guaranteed_grade = min_quality_rank <= QUALITY_ORDER["standard"]
    guaranteed_days: list[int] = []
    seeded_days: list[int] = []

    def _replant_cycles(free_after: int) -> None:
        """Harvest days for repeated replanting of a slot freed in `free_after` days."""
        for cycle in range(1, (days_available - free_after) // growth_days + 1):
            seeded_days.append(player.day + free_after + cycle * growth_days)

    if guaranteed_grade:
        for _ in range(max(0, player.open_slots)):
            _replant_cycles(0)
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
            best_grade = _best_possible_grade(
                planted, crop, plot, getattr(player, "soil_dynamics", None)
            )
            if QUALITY_ORDER[best_grade] >= min_quality_rank:
                guaranteed_days.append(player.day + days_until_free)
            if guaranteed_grade:
                _replant_cycles(days_until_free)
        else:
            if guaranteed_grade and days_until_free < days_available:
                _replant_cycles(days_until_free)

    seed_inventory = player.seed_inventory.get(crop["id"], 0)
    seed_cost = crop["seed_cost"]
    # A validly-configured crop may cost 0 (a free starter crop); cash can
    # never be the limiting factor there, so cap at the cycle count itself
    # rather than floor-dividing by a seed cost that may be zero.
    if seed_cost > 0:
        cash_seed_units = int(
            max(0.0, player.money - economy_rules.operating_reserve(player)) // seed_cost
        )
    else:
        cash_seed_units = len(seeded_days)
    funded_seeded_cycles = min(len(seeded_days), seed_inventory + cash_seed_units)
    purchased = max(0, funded_seeded_cycles - seed_inventory)

    guaranteed_days.sort()
    # Cash funds the *earliest* cycles: a farm short of seed money plants as
    # soon as it can afford to rather than saving up for the end of the run.
    seeded_days.sort()
    del seeded_days[funded_seeded_cycles:]
    return guaranteed_days, seeded_days, expected_yield, purchased * seed_cost


def _future_crop_capacity(
    player, crop: dict, deadline: int, min_quality: str = "standard"
) -> tuple[float, float, float]:
    """Return future safe yield and seed cash needed, excluding inventory.

    A totals-only view of `_future_crop_arrivals`. Deliberately multiplies
    the *counts* by `expected_yield` rather than summing a per-harvest list,
    so the floating-point result is identical to what this function computed
    before harvest days were tracked.
    """
    guaranteed_days, seeded_days, expected_yield, funding = _future_crop_arrivals(
        player, crop, deadline, min_quality
    )
    return (
        (len(guaranteed_days) + len(seeded_days)) * expected_yield,
        funding,
        len(guaranteed_days) * expected_yield,
    )


class _InputSupply:
    """When each unit of a recipe's input actually becomes available.

    `arrivals` is a day-sorted list of mutable `[day, quantity, is_future]`
    entries: inventory already held arrives today, and each forecast harvest
    arrives on the day it lands (see `_future_crop_arrivals`). Recipes that
    share an input share one instance and consume from it in turn, so the
    same crate can never be promised to two of them.
    """

    __slots__ = ("arrivals", "funding", "future_total", "used_future")

    def __init__(self, arrivals: list, funding: float, future_total: float):
        self.arrivals = arrivals
        self.funding = funding
        self.future_total = future_total
        self.used_future = 0.0


def _slot_free_days(player, capacity: int) -> list[int]:
    """The day each processing slot next becomes usable.

    A slot running an existing job is busy until that job completes; any
    remaining slot is free today. Jobs beyond `capacity` are ignored rather
    than allowed to make the list longer than the farm's real slot count.
    """
    busy = sorted(job.completion_day for job in player.processing_jobs)[: max(0, capacity)]
    free = [max(player.day, completion_day) for completion_day in busy]
    free.extend([player.day] * max(0, capacity - len(free)))
    return free


def _input_supply(player, input_id: str, min_quality: str, deadline: int) -> _InputSupply:
    current = _inventory_quantity(player, input_id, min_quality)
    arrivals = [[player.day, current, False]] if current > 0 else []
    funding = 0.0
    future_total = 0.0
    crop = player.crop_catalog.get(input_id)
    if crop is not None:
        guaranteed_days, seeded_days, expected_yield, funding = _future_crop_arrivals(
            player, crop, deadline, min_quality
        )
        harvest_days = sorted(guaranteed_days + seeded_days)
        arrivals.extend([day, expected_yield, True] for day in harvest_days)
        future_total = len(harvest_days) * expected_yield
    # Stable, so inventory already on hand is consumed before a harvest
    # landing on the very same day.
    arrivals.sort(key=lambda entry: entry[0])
    return _InputSupply(arrivals, funding, future_total)


def _arrival_day(arrivals: list, needed: int):
    """The day by which `needed` units have all arrived, or None if never."""
    remaining = needed
    for day, quantity, _is_future in arrivals:
        remaining -= quantity
        if remaining <= 0:
            return day
    return None


def _consume(supply: _InputSupply, needed: int) -> None:
    """Remove `needed` units from the front of `supply.arrivals`."""
    remaining = needed
    while remaining > 0 and supply.arrivals:
        entry = supply.arrivals[0]
        take = min(entry[1], remaining)
        entry[1] -= take
        remaining -= take
        if entry[2]:
            supply.used_future += take
        if entry[1] <= 0:
            supply.arrivals.pop(0)


def _schedule_batches(
    supply: _InputSupply,
    slot_free_day: list[int],
    input_quantity: int,
    recipe_days: int,
    deadline: int,
) -> int:
    """Greedily schedule as many batches of one recipe as really fit.

    Each batch starts on the later of "its inputs have arrived" and "the
    earliest slot is free", and only counts if it still *finishes* by
    `deadline`. Both of those only move later as batches are placed, so the
    first batch that cannot finish in time ends the loop -- and its inputs
    stay unconsumed for whatever recipe is considered next.
    """
    batches = 0
    while slot_free_day:
        arrival = _arrival_day(supply.arrivals, input_quantity)
        if arrival is None:
            break
        slot = min(range(len(slot_free_day)), key=slot_free_day.__getitem__)
        start = max(arrival, slot_free_day[slot])
        if start + recipe_days > deadline:
            break
        _consume(supply, input_quantity)
        slot_free_day[slot] = start + recipe_days
        batches += 1
    return batches


def _item_capacity(
    player, item_id: str, min_quality: str, deadline: int, seen=()
) -> tuple[float, float, float]:
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

    # Slot-level scheduling, not a pooled slot-day budget. A pooled budget
    # knows how much capacity the window holds but not *when* it is usable,
    # so it happily spent capacity from the start of the window on a crop
    # that is not harvested until the end of it -- approving processed-goods
    # contracts that cannot actually be scheduled in the daily order (CQ-03).
    # Here every batch has to clear three things at once: its inputs have
    # arrived, a slot is free, and it still finishes by the deadline.
    slot_free_day = _slot_free_days(player, _processing_capacity(player))

    # Multiple recipes producing this item can compete for the same input
    # inventory and the same input crop's future yield, so they share one
    # `_InputSupply` per input and consume from it in turn -- a later recipe
    # sees what is actually left, not the full unreserved pool.
    #
    # Cash to fund new plantings is NOT similarly reserved across recipes
    # here (each recipe's funding need is still priced against the full
    # cash pool) -- joint cash contention across multiple simultaneous
    # forecasts is a pre-existing simplification this fix does not extend
    # to (is_offer_feasible already evaluates each contract's own cash need
    # independently of every other contract for the same reason).
    supplies: dict[str, _InputSupply] = {}
    for recipe in _recipes(player):
        if recipe.get("output_item_id") != item_id or not slot_free_day:
            continue
        recipe_days = max(1, recipe.get("processing_days", 1))
        if player.day + recipe_days > deadline:
            continue  # cannot complete even one batch of this recipe in time

        input_id = recipe["input_item_id"]
        supply = supplies.get(input_id)
        if supply is None:
            # Keyed by input alone, like the reservation it replaces: two
            # recipes sharing an input but declaring different min_quality
            # is not modeled, and splitting the pool per quality would let
            # them double-count the same crate instead.
            supply = _input_supply(
                player, input_id, recipe.get("min_quality", "processing"), deadline
            )
            supplies[input_id] = supply

        batches = _schedule_batches(
            supply, slot_free_day, recipe["input_quantity"], recipe_days, deadline
        )
        if batches <= 0:
            continue

        future += batches * recipe["output_quantity"]
        funding += batches * recipe.get("cost", 0.0)

    # Seed cash for the future harvests the schedule actually consumed,
    # prorated once per input across every recipe that drew on it.
    for supply in supplies.values():
        if supply.used_future and supply.future_total:
            funding += supply.funding * min(1.0, supply.used_future / supply.future_total)
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

    A premium-grade contract follows the same rule: crops already planted
    that can still reach the grade (see `_future_crop_capacity`) count as
    committed, so the agent stops forcing more of the crop into open slots
    once they cover `contract.remaining` -- the over-planting #30 targeted
    applied to premium buyers too.
    """
    # Capped at the run horizon like every other forecast here: a job whose
    # completion_day falls past the last executed day never completes, so
    # counting it as committed supply told an agent it was already covered
    # when it was not (and stopped it planting toward the shortfall).
    deadline = _effective_deadline(player, contract.deadline_day)
    current = _inventory_quantity(player, contract.item_id, contract.min_quality)
    for job in player.processing_jobs:
        if (
            job.output_item_id == contract.item_id
            and job.completion_day <= deadline
            and QUALITY_ORDER.get(contract.min_quality, 0) <= QUALITY_ORDER["standard"]
        ):
            current += job.output_quantity
    crop = player.crop_catalog.get(contract.item_id)
    if crop is not None:
        _future, _funding, free_future = _future_crop_capacity(
            player, crop, deadline, contract.min_quality
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
    if not is_positive_int(quantity):
        return 0.0, 0
    contract = next(
        (item for item in player.active_contracts if item.id == contract_id and not item.resolved),
        None,
    )
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
        gain = getattr(player, "contract_config", {}).get(
            "relationship_gain_per_delivery", DEFAULT_RELATIONSHIP_GAIN
        )
        player.buyer_relationships[contract.buyer_id] = min(
            100.0, player.buyer_relationships.get(contract.buyer_id, 0.0) + gain
        )
    return revenue, delivered


def resolve_expired(player) -> None:
    for contract in player.active_contracts:
        if contract.resolved or player.day <= contract.deadline_day:
            continue
        shortfall_value = contract.remaining * contract.unit_price
        # Both operands floored at zero. The penalty is deliberately bounded
        # by cash on hand, but taking that bound from a negative balance made
        # `money -= penalty` *credit* the farm for failing a contract, and
        # record_expense silently dropped the negative while
        # player.contract_penalties still accumulated it -- leaving the two
        # irreconcilable. Cash cannot currently go negative (every spend site
        # guards), so this defends an invariant rather than fixing observed
        # output.
        penalty = min(max(0.0, player.money), max(0.0, shortfall_value * contract.penalty_rate))
        player.money -= penalty
        player.record_expense("contract_penalties", penalty)
        player.contract_penalties += penalty
        player.contracts_failed += 1
        player.reputation = max(0.0, player.reputation - 4.0)
        loss = getattr(player, "contract_config", {}).get(
            "relationship_loss_per_failure", DEFAULT_RELATIONSHIP_LOSS
        )
        player.buyer_relationships[contract.buyer_id] = max(
            0.0, player.buyer_relationships.get(contract.buyer_id, 0.0) - loss
        )
        contract.resolved = True

    # Resolved contracts (completed via deliver() or failed above) were never
    # removed, so this list grew for the whole run and was re-scanned every
    # day by both this function and every agent's delivery hook. Every
    # consumer already filters on `not resolved`, so dropping them here is
    # behaviour-preserving; it just bounds the list by the number of
    # *outstanding* contracts instead of by run length. Completion and
    # failure totals live in player.contracts_completed/contracts_failed.
    player.active_contracts = [
        contract for contract in player.active_contracts if not contract.resolved
    ]
    player.contract_offers = visible_offers(player)
