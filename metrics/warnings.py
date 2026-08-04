"""Balance warning rules. Thresholds are configuration values, not fixed
assumptions -- override DEFAULT_THRESHOLDS as needed per scenario.
"""

DEFAULT_THRESHOLDS = {
    "dominant_crop_pct": 70,
    "dead_crop_pct": 5,
    "high_bankruptcy_pct": 20,
    "upgrade_too_fast_day": 5,
    "upgrade_too_slow_fraction": 0.9,   # fraction of sim days with no first upgrade purchase
    "runaway_money_multiple": 20,       # avg final money > start_money * multiple
    "high_crop_loss_rate_pct": 30,      # avg % of matured crops lost at harvest
}


def evaluate_warnings(summary: dict, config: dict, thresholds: dict = None) -> list:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    sim_days = config["days"]
    start_money = config["start_money"]
    warnings = []

    for strategy, stats in summary.items():
        for cid, pct in stats["crop_usage_pct"].items():
            if pct > thresholds["dominant_crop_pct"]:
                warnings.append(
                    f"[{strategy}] Dominant crop: '{cid}' is {pct}% of plantings "
                    f"(> {thresholds['dominant_crop_pct']}%)."
                )
            elif pct < thresholds["dead_crop_pct"]:
                warnings.append(
                    f"[{strategy}] Dead crop: '{cid}' is only {pct}% of plantings "
                    f"(< {thresholds['dead_crop_pct']}%)."
                )

        if stats["bankruptcy_rate"] > thresholds["high_bankruptcy_pct"]:
            warnings.append(
                f"[{strategy}] High bankruptcy rate: {stats['bankruptcy_rate']}% "
                f"(> {thresholds['high_bankruptcy_pct']}%)."
            )

        first_day = stats["avg_first_upgrade_day"]
        if first_day is not None and first_day <= thresholds["upgrade_too_fast_day"]:
            warnings.append(
                f"[{strategy}] First upgrade purchased very early on average "
                f"(day {first_day})."
            )
        if first_day is None or first_day >= sim_days * thresholds["upgrade_too_slow_fraction"]:
            warnings.append(
                f"[{strategy}] First upgrade is rarely reached before the simulation ends."
            )

        if stats["avg_final_money"] > start_money * thresholds["runaway_money_multiple"]:
            warnings.append(
                f"[{strategy}] Possible runaway economy: avg final money "
                f"{stats['avg_final_money']} is {thresholds['runaway_money_multiple']}x+ starting money."
            )

        if stats["avg_crop_loss_rate"] > thresholds["high_crop_loss_rate_pct"]:
            warnings.append(
                f"[{strategy}] High crop loss rate: {stats['avg_crop_loss_rate']}% of matured crops are lost "
                f"(> {thresholds['high_crop_loss_rate_pct']}%). Watering diligence for this strategy averages "
                f"{stats['avg_watering_rate']}% of plot-days."
            )

    return warnings
