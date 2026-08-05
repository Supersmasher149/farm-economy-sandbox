"""Buyer contract offers, deliveries, and deadline resolution."""
from simulation import economy_rules, inventory, markets
from simulation.state import ContractState

PRODUCTION_SAFETY_FACTOR = 0.45


def generate_offers(player, contract_config: dict, buyers: list, items_by_id: dict, rng) -> list:
    interval = contract_config.get("offer_interval_days", 7)
    if player.day == 0 or player.day % interval != 0:
        return []
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


def producible_quantity(player, contract) -> float:
    """Estimate safely producible quantity before a contract deadline.

    The estimate uses a 45% yield safety factor and accounts for crops that
    must mature before their slot can be replanted for the contract item.
    """
    crop = player.crop_catalog.get(contract.item_id)
    if crop is None:
        return 0.0
    growth_days = economy_rules.effective_growth_days(
        crop, player, player.upgrades_catalog
    )
    days_available = max(0, contract.deadline_day - player.day)
    expected_yield = (
        (crop["min_yield"] + crop["max_yield"]) / 2
        * (1 - crop.get("loss_chance", 0.0))
        * getattr(player, "contract_config", {}).get(
            "production_safety_factor", PRODUCTION_SAFETY_FACTOR
        )
    )
    quantity = 0.0
    open_slots = player.open_slots
    quantity += open_slots * (days_available // growth_days) * expected_yield
    for planted in player.planted:
        days_after_current_crop = (
            days_available
            if planted.crop_id == contract.item_id
            else days_available - planted.growth_days_required
        )
        if days_after_current_crop >= growth_days:
            quantity += (days_after_current_crop // growth_days) * expected_yield
    return quantity


def is_offer_feasible(player, contract) -> bool:
    crop = player.crop_catalog.get(contract.item_id)
    if crop is None:
        return False
    if not economy_rules.can_spend_with_reserve(player, crop["seed_cost"]):
        return False
    return contract.quantity <= producible_quantity(player, contract)


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

    player.contract_offers = [
        offer for offer in player.contract_offers
        if player.day <= offer.offered_day + 3 and not offer.resolved
    ]
