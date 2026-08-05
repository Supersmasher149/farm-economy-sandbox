"""Daily market prices, channel quotes, and sales."""
from simulation import derived, inventory
from simulation.state import QUALITY_ORDER


QUALITY_MULTIPLIERS = {
    "rejected": 0.0,
    "processing": 0.65,
    "standard": 1.0,
    "premium": 1.35,
}


def update_daily_prices(player, items_by_id: dict, market_config: dict, rng, profiles=None) -> dict:
    # Per-item base price / variation / seasonal table are fixed config, so
    # they come from a precomputed profile list instead of being re-read (with
    # per-item .get defaults) every day. Its order matches items_by_id's, which
    # keeps the sequence of rng draws -- and therefore every recorded seed --
    # unchanged. The engine passes the list it already holds; omitting it
    # falls back to an equivalent cached lookup.
    if profiles is None:
        profiles = derived.market_profiles(items_by_id, market_config)
    season = player.current_weather.get("season", "spring")
    minimum_supply = market_config.get("minimum_supply_multiplier", 0.65)
    supply_decay = market_config.get("supply_decay", 0.75)
    market_supply = player.market_supply
    prices = {}
    for item_id, base, variation, seasonal_demand in profiles:
        seasonal = seasonal_demand.get(season, 1.0)
        supply = market_supply.get(item_id, 0.0)
        saturation = max(minimum_supply, 1.0 - supply * 0.01)
        prices[item_id] = max(0.01, base * seasonal * saturation * rng.uniform(1 - variation, 1 + variation))
        market_supply[item_id] = supply * supply_decay
    player.market_prices = prices
    player.channel_capacity_used = {}
    return prices


def quote(player, item_id: str, quality: str, channel: dict, quantity: int) -> dict | None:
    if item_id not in player.market_prices or quantity <= 0:
        return None
    if QUALITY_ORDER[quality] < QUALITY_ORDER[channel.get("min_quality", "rejected")]:
        return None
    if player.reputation < channel.get("min_reputation", 0):
        return None
    used = player.channel_capacity_used.get(channel["id"], 0)
    capacity = channel.get("daily_capacity", quantity)
    accepted = min(quantity, max(0, capacity - used))
    if accepted <= 0:
        return None
    unit_price = (
        player.market_prices[item_id]
        * channel.get("price_multiplier", 1.0)
        * QUALITY_MULTIPLIERS[quality]
        * (1 + min(0.25, player.reputation * channel.get("reputation_bonus", 0.002)))
    )
    gross = unit_price * accepted
    fee = channel.get("flat_fee", 0.0) + gross * channel.get("fee_rate", 0.0)
    if gross <= fee:
        return None
    return {"quantity": accepted, "unit_price": unit_price, "gross": gross, "fee": fee, "net": gross - fee}


def sell(player, item_id: str, quantity: int, channel: dict) -> tuple[float, int]:
    minimum = channel.get("min_quality", "rejected")
    if (
        quantity <= 0
        or item_id not in player.market_prices
        or player.reputation < channel.get("min_reputation", 0)
    ):
        return 0.0, 0
    used = player.channel_capacity_used.get(channel["id"], 0)
    quantity = min(quantity, max(0, channel.get("daily_capacity", quantity) - used))
    lots = sorted(
        (lot for lot in player.inventory_lots
         if lot.item_id == item_id and QUALITY_ORDER[lot.quality] >= QUALITY_ORDER[minimum]),
        key=lambda lot: (lot.remaining_shelf_life, -QUALITY_ORDER[lot.quality]),
    )
    planned = []
    sold = 0
    gross = 0.0
    reputation_multiplier = 1 + min(0.25, player.reputation * channel.get("reputation_bonus", 0.002))
    for lot in lots:
        if sold >= quantity:
            break
        take = min(lot.quantity, quantity - sold)
        unit_price = (
            player.market_prices[item_id]
            * channel.get("price_multiplier", 1.0)
            * QUALITY_MULTIPLIERS[lot.quality]
            * reputation_multiplier
        )
        planned.append((lot, take))
        sold += take
        gross += unit_price * take
    if sold:
        fee = channel.get("flat_fee", 0.0) + gross * channel.get("fee_rate", 0.0)
        if gross <= fee:
            return 0.0, 0
        revenue = gross - fee
        for lot, take in planned:
            lot.quantity -= take
        player.inventory_lots = [lot for lot in player.inventory_lots if lot.quantity > 0]
        player.rebuild_crop_inventory()
        player.channel_capacity_used[channel["id"]] = used + sold
        player.money += revenue
        player.total_revenue += revenue
        player.total_sold += sold
        player.revenue_by_channel[channel["id"]] = player.revenue_by_channel.get(channel["id"], 0.0) + revenue
        player.market_supply[item_id] = player.market_supply.get(item_id, 0.0) + sold
        return revenue, sold
    return 0.0, 0


def best_channel(player, item_id: str, quality: str, channels: list, quantity: int) -> dict | None:
    candidates = []
    for channel in channels:
        offer = quote(player, item_id, quality, channel, quantity)
        if offer:
            candidates.append((offer["net"] / offer["quantity"], channel))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else None
