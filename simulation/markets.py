"""Daily market prices, channel quotes, and sales."""
from simulation import inventory
from simulation.state import QUALITY_ORDER


QUALITY_MULTIPLIERS = {
    "rejected": 0.0,
    "processing": 0.65,
    "standard": 1.0,
    "premium": 1.35,
}


def update_daily_prices(player, items_by_id: dict, market_config: dict, rng) -> dict:
    season = player.current_weather.get("season", "spring")
    prices = {}
    for item_id, item in items_by_id.items():
        base = item.get("base_price", item.get("processed_base_price", 1.0))
        variation = item.get("price_variation", market_config.get("default_variation", 0.12))
        seasonal = item.get("seasonal_demand", {}).get(season, 1.0)
        supply = player.market_supply.get(item_id, 0.0)
        saturation = max(market_config.get("minimum_supply_multiplier", 0.65), 1.0 - supply * 0.01)
        prices[item_id] = max(0.01, base * seasonal * saturation * rng.uniform(1 - variation, 1 + variation))
        player.market_supply[item_id] = supply * market_config.get("supply_decay", 0.75)
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
