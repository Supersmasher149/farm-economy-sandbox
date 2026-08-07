"""Balance warning rules. Thresholds are configuration values, not fixed
assumptions -- override DEFAULT_THRESHOLDS as needed per scenario.
"""

DEFAULT_THRESHOLDS = {
    "dominant_crop_pct": 70,
    "dead_crop_pct": 5,
    "high_bankruptcy_pct": 20,
    "upgrade_too_fast_day": 5,
    "upgrade_too_slow_fraction": 0.9,  # fraction of runs with no first upgrade purchase
    # avg final money > start_money * multiple, where the multiple is scaled
    # from runaway_reference_days up to the run's actual length. A farm that
    # reinvests compounds with time, so a fixed multiple is a different test
    # at every horizon: at 365 days a flat 20x meant $1,200 on a $60 start,
    # which every surviving strategy clears. It even flagged reckless_spender
    # as a "runaway economy" in the same report that had it going bankrupt in
    # 63.5% of runs. The multiple below is calibrated at the reference
    # horizon and scaled linearly from there.
    "runaway_money_multiple": 20,
    "runaway_reference_days": 30,
    "high_crop_loss_rate_pct": 30,  # avg % of matured crops lost at harvest
}


def runaway_money_multiple(config: dict, thresholds: dict) -> float:
    """Runaway-economy multiple for this run's horizon."""
    reference_days = thresholds.get("runaway_reference_days") or 1
    days = config.get("days") or reference_days
    return thresholds["runaway_money_multiple"] * max(1, days) / reference_days


def evaluate_warnings(summary: dict, config: dict, thresholds: dict = None) -> list:
    thresholds = thresholds or DEFAULT_THRESHOLDS
    start_money = config["start_money"]
    runaway_multiple = runaway_money_multiple(config, thresholds)
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
                f"[{strategy}] First upgrade purchased very early on average (day {first_day})."
            )
        first_rate = stats["first_upgrade_rate"]
        max_unreached_rate = thresholds["upgrade_too_slow_fraction"] * 100
        if first_rate <= 100 - max_unreached_rate:
            warnings.append(
                f"[{strategy}] First upgrade is rarely reached: "
                f"{first_rate}% of runs purchased one."
            )

        if stats["avg_final_money"] > start_money * runaway_multiple:
            warnings.append(
                f"[{strategy}] Possible runaway economy: avg final money "
                f"{stats['avg_final_money']} is {round(runaway_multiple, 1)}x+ starting money "
                f"over {config.get('days')} days."
            )

        if stats["avg_crop_loss_rate"] > thresholds["high_crop_loss_rate_pct"]:
            warnings.append(
                f"[{strategy}] High crop loss rate: {stats['avg_crop_loss_rate']}% of matured crops are lost "
                f"(> {thresholds['high_crop_loss_rate_pct']}%). Watering diligence for this strategy averages "
                f"{stats['avg_watering_rate']}% of plot-days."
            )

    return warnings
