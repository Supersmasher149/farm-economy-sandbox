"""Lot inventory aging, eligibility, and FEFO consumption.

Consumption and sales both draw from the nearest-to-expiry eligible lot
first (FEFO -- first-expired, first-out), not insertion order (FIFO): with
a mixed-age inventory, spending the soonest-to-spoil stock first is what
actually minimizes spoilage loss, which is the outcome storage/spoilage
accounting exists to measure in the first place. `produced_day` is recorded
on every lot (for age/cost-basis bookkeeping) but is deliberately not part
of the consumption sort key -- see #22.
"""

from simulation.state import QUALITY_ORDER
from simulation.validation import is_positive_int


def available_quantity(player, item_id: str, min_quality: str = "rejected") -> int:
    threshold = QUALITY_ORDER[min_quality]
    return sum(
        lot.quantity
        for lot in player.inventory_lots
        if lot.item_id == item_id and QUALITY_ORDER[lot.quality] >= threshold
    )


def consume(
    player, item_id: str, quantity: int, min_quality: str = "rejected"
) -> tuple[int, float]:
    if not is_positive_int(quantity):
        return 0, 0.0
    threshold = QUALITY_ORDER[min_quality]
    eligible = sorted(
        (
            lot
            for lot in player.inventory_lots
            if lot.item_id == item_id and QUALITY_ORDER[lot.quality] >= threshold
        ),
        key=lambda lot: (lot.remaining_shelf_life, QUALITY_ORDER[lot.quality]),
    )
    consumed = 0
    cost = 0.0
    for lot in eligible:
        take = min(quantity - consumed, lot.quantity)
        lot.quantity -= take
        consumed += take
        cost += take * lot.unit_cost
        if consumed == quantity:
            break
    player.inventory_lots = [lot for lot in player.inventory_lots if lot.quantity > 0]
    player.rebuild_crop_inventory()
    return consumed, cost


def capture_storage_liability(player, storage_config: dict) -> float:
    """Capture the day's storage charge from inventory held at day start."""
    daily_cost = storage_config.get("daily_cost", 0.0)
    has_inventory = any(lot.quantity > 0 for lot in player.inventory_lots)
    return daily_cost if has_inventory and daily_cost > 0 else 0.0


def collect_storage_liability(player, liability: float) -> float:
    """Charge captured storage liability without allowing cash to go negative."""
    charged = min(max(0.0, player.money), max(0.0, liability))
    if charged:
        player.money -= charged
        player.record_expense("storage", charged)
    return charged


def _trim_to_capacity(player, capacity: int) -> int:
    """Reduce the soonest-to-expire (FEFO) lots' quantities until total
    inventory is at or under `capacity`. Returns units spoiled by trimming.
    Mutates lot quantities only -- does not filter zero-quantity lots out of
    player.inventory_lots, update total_spoiled/losses_by_cause, or rebuild
    crop_inventory; callers own that bookkeeping, since this can run more
    than once in a day (end-of-day aging via age_and_spoil, and again
    same-day after processing jobs complete via enforce_storage_capacity --
    see #19) and each call site already has its own spoilage tally to fold
    this into.
    """
    remaining_total = sum(lot.quantity for lot in player.inventory_lots)
    overflow = max(0, remaining_total - capacity)
    spoiled = 0
    if overflow > 0:
        for lot in sorted(player.inventory_lots, key=lambda item: item.remaining_shelf_life):
            if overflow <= 0:
                break
            removed = min(overflow, lot.quantity)
            lot.quantity -= removed
            overflow -= removed
            spoiled += removed
    return spoiled


def enforce_storage_capacity(player, capacity: int) -> int:
    """Trim inventory back to `capacity` immediately, spoiling overflow
    FEFO-first, with the same bookkeeping age_and_spoil's own overflow trim
    does (total_spoiled, losses_by_cause, the live lot list, crop_inventory).

    For same-day use right after processing jobs complete
    (simulation/engine.py): age_and_spoil already ran earlier that day,
    before a completing job's output existed, so overflow a completing job
    causes wouldn't otherwise spoil until the following day -- letting
    same-day agent sales/deliveries use inventory that should already have
    overflowed.
    """
    spoiled = _trim_to_capacity(player, capacity)
    if spoiled:
        player.inventory_lots = [lot for lot in player.inventory_lots if lot.quantity > 0]
        player.total_spoiled += spoiled
        player.losses_by_cause["spoilage_units"] = (
            player.losses_by_cause.get("spoilage_units", 0) + spoiled
        )
        player.rebuild_crop_inventory()
    return spoiled


def age_and_spoil(player, storage_config: dict, charge_storage: bool = True) -> int:
    liability = capture_storage_liability(player, storage_config) if charge_storage else 0.0
    multiplier = storage_config.get("shelf_life_multiplier", 1.0)
    spoiled = 0
    capacity = storage_config.get("capacity", 100)
    for lot in player.inventory_lots:
        lot.effective_shelf_life_days = max(1, round(lot.shelf_life_days * multiplier))

    for lot in sorted(player.inventory_lots, key=lambda item: item.remaining_shelf_life):
        if lot.produced_day >= player.day:
            continue
        lot.age_days += 1
        effective_life = lot.effective_shelf_life_days
        age_ratio = lot.age_days / effective_life
        if age_ratio >= 1:
            spoiled += lot.quantity
            lot.quantity = 0
        elif age_ratio >= 0.5 and lot.quality == "premium":
            lot.quality = "standard"
        elif age_ratio >= 0.8 and lot.quality == "standard":
            lot.quality = "processing"

    # _trim_to_capacity only sorts/mutates when something actually has to be
    # trimmed -- storage sits under capacity on the overwhelming majority of
    # days.
    spoiled += _trim_to_capacity(player, capacity)

    player.inventory_lots = [lot for lot in player.inventory_lots if lot.quantity > 0]
    player.total_spoiled += spoiled
    if spoiled:
        # Unit-suffixed key: see the note in actions.harvest_mature on why
        # this dict's keys name their measure.
        player.losses_by_cause["spoilage_units"] = (
            player.losses_by_cause.get("spoilage_units", 0) + spoiled
        )
    if charge_storage:
        collect_storage_liability(player, liability)
    player.rebuild_crop_inventory()
    return spoiled
