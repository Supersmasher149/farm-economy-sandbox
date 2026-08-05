"""Deterministic audits of configured crop and market economics."""
from simulation import economy_rules


def build_economics_audit(crops: list, fertilizer_config: dict, market_config: dict) -> dict:
    crop_audit = []
    for crop in crops:
        average_yield = (crop["min_yield"] + crop["max_yield"]) / 2
        expected_revenue = average_yield * crop["base_price"] * (1 - crop["loss_chance"])
        profit_per_cycle = expected_revenue - crop["seed_cost"]
        crop_audit.append({
            "id": crop["id"],
            "seed_cost": crop["seed_cost"],
            "growth_days": crop["growth_days"],
            "yield_range": [crop["min_yield"], crop["max_yield"]],
            "average_yield": round(average_yield, 2),
            "base_sale_price": crop["base_price"],
            "loss_chance_pct": round(100 * crop["loss_chance"], 2),
            "expected_revenue_per_cycle": round(expected_revenue, 2),
            "nominal_profit_per_cycle": round(profit_per_cycle, 2),
            "nominal_profit_per_growth_day": round(profit_per_cycle / crop["growth_days"], 2),
            "fertilizer_marginal_profit": round(
                economy_rules.fertilizer_expected_marginal_profit(crop, fertilizer_config), 2
            ),
        })

    channels = [
        {
            "id": channel["id"],
            "price_multiplier": channel.get("price_multiplier", 1.0),
            "min_quality": channel.get("min_quality", "rejected"),
            "daily_capacity": channel.get("daily_capacity"),
            "flat_fee": channel.get("flat_fee", 0.0),
            "fee_rate_pct": round(100 * channel.get("fee_rate", 0.0), 2),
        }
        for channel in market_config.get("channels", [])
    ]
    return {
        "crops": crop_audit,
        "fertilizer": {
            "cost": fertilizer_config["cost"],
            "yield_bonus_pct": round(100 * fertilizer_config.get("yield_bonus_pct", 0.0), 2),
            "loss_chance_reduction_pct": round(
                100 * fertilizer_config.get("loss_chance_reduction", 0.0), 2
            ),
        },
        "market_channels": channels,
    }
