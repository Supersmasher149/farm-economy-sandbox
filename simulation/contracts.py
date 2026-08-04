"""Buyer contract offers, deliveries, and deadline resolution."""
from simulation import inventory
from simulation.state import ContractState


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
        player.total_expenses += penalty
        player.contract_penalties += penalty
        player.contracts_failed += 1
        player.reputation = max(0.0, player.reputation - 4.0)
        contract.resolved = True

    player.contract_offers = [
        offer for offer in player.contract_offers
        if player.day <= offer.offered_day + 3 and not offer.resolved
    ]
