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


def _merge_thresholds(overrides: dict | None) -> dict:
    """Merge a partial override over DEFAULT_THRESHOLDS rather than replacing it.

    A caller passing `{"high_bankruptcy_pct": 50}` means "change just this
    one," per this module's own docstring -- not "use only this one and
    KeyError on everything else downstream." Unknown keys and non-numeric
    values are rejected outright instead of silently no-op'ing, since a
    typo'd key would otherwise look like it took effect. Never mutates
    DEFAULT_THRESHOLDS itself.
    """
    if not overrides:
        return dict(DEFAULT_THRESHOLDS)
    unknown = sorted(set(overrides) - set(DEFAULT_THRESHOLDS))
    if unknown:
        raise ValueError(f"Unknown warning threshold(s): {unknown}")
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Threshold '{key}' must be numeric, got {type(value).__name__}")
        merged[key] = value
    return merged


def runaway_money_multiple(config: dict, thresholds: dict) -> float:
    """Runaway-economy multiple for this run's horizon."""
    reference_days = thresholds.get("runaway_reference_days") or 1
    days = config.get("days") or reference_days
    return thresholds["runaway_money_multiple"] * max(1, days) / reference_days


def evaluate_warnings(summary: dict, config: dict, thresholds: dict = None) -> list:
    thresholds = _merge_thresholds(thresholds)
    start_money = config["start_money"]
    runaway_multiple = runaway_money_multiple(config, thresholds)
    warnings = []

    for strategy, stats in summary.items():
        # crop_usage_pct is unrounded and full-precision (see
        # aggregate_results.py); round only for the message text, not the
        # threshold comparison itself, so a value the aggregator sees as
        # e.g. 70.004% still fires a > 70 threshold that display rounding
        # would otherwise have hidden.
        if not stats.get("crop_usage_observed", True):
            # No crop_usage_pct entry means "never observed," not "observed
            # at 0%" -- reporting every known crop as dead here would be
            # noise, not a finding, so this cohort gets one clear diagnostic
            # instead of one dead-crop warning per crop in the catalog.
            if stats["num_runs"] > 0:
                warnings.append(f"[{strategy}] No crops were planted in any run.")
        else:
            for cid, pct in stats["crop_usage_pct"].items():
                if pct > thresholds["dominant_crop_pct"]:
                    warnings.append(
                        f"[{strategy}] Dominant crop: '{cid}' is {round(pct, 2)}% of plantings "
                        f"(> {thresholds['dominant_crop_pct']}%)."
                    )
                elif pct < thresholds["dead_crop_pct"]:
                    warnings.append(
                        f"[{strategy}] Dead crop: '{cid}' is only {round(pct, 2)}% of plantings "
                        f"(< {thresholds['dead_crop_pct']}%)."
                    )

        if stats["bankruptcy_rate"] > thresholds["high_bankruptcy_pct"]:
            warnings.append(
                f"[{strategy}] High bankruptcy rate: {round(stats['bankruptcy_rate'], 2)}% "
                f"(> {thresholds['high_bankruptcy_pct']}%)."
            )

        first_day = stats["avg_first_upgrade_day"]
        if first_day is not None and first_day <= thresholds["upgrade_too_fast_day"]:
            warnings.append(
                f"[{strategy}] First upgrade purchased very early on average "
                f"(day {round(first_day, 2)})."
            )
        first_rate = stats["first_upgrade_rate"]
        max_unreached_rate = thresholds["upgrade_too_slow_fraction"] * 100
        if first_rate <= 100 - max_unreached_rate:
            warnings.append(
                f"[{strategy}] First upgrade is rarely reached: "
                f"{round(first_rate, 2)}% of runs purchased one."
            )

        if stats["avg_final_money"] > start_money * runaway_multiple:
            warnings.append(
                f"[{strategy}] Possible runaway economy: avg final money "
                f"{round(stats['avg_final_money'], 2)} is {round(runaway_multiple, 1)}x+ starting "
                f"money over {config.get('days')} days."
            )

        crop_loss_rate = stats["avg_crop_loss_rate"]
        # None means no run in the cohort ever had a harvest event to
        # measure loss against -- there is nothing to compare to the
        # threshold, not an implicit 0% loss.
        if crop_loss_rate is not None and crop_loss_rate > thresholds["high_crop_loss_rate_pct"]:
            warnings.append(
                f"[{strategy}] High crop loss rate: {round(crop_loss_rate, 2)}% of matured crops "
                f"are lost (> {thresholds['high_crop_loss_rate_pct']}%). Watering diligence for "
                f"this strategy averages {stats['avg_watering_rate']}% of plot-days."
            )

    return warnings
