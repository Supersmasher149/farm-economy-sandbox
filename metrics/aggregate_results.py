"""Aggregate a batch's RunResults into per-strategy summary statistics."""
import statistics


def aggregate(results: list) -> dict:
    by_strategy = {}
    for r in results:
        by_strategy.setdefault(r.strategy, []).append(r)

    summary = {}
    for strategy, runs in by_strategy.items():
        final_moneys = [r.final_money for r in runs]
        bankrupt_count = sum(1 for r in runs if r.bankrupt)
        first_days = [r.first_upgrade_day for r in runs if r.first_upgrade_day is not None]
        second_days = [r.second_upgrade_day for r in runs if r.second_upgrade_day is not None]

        crop_totals = {}
        planted_total = 0
        for r in runs:
            planted_total += r.crops_planted
            for cid, count in r.crop_counts.items():
                crop_totals[cid] = crop_totals.get(cid, 0) + count
        crop_usage_pct = {
            cid: round(100 * count / planted_total, 2) if planted_total else 0.0
            for cid, count in crop_totals.items()
        }
        channel_revenue = {}
        quality_totals = {}
        for run in runs:
            for channel, revenue in run.revenue_by_channel.items():
                channel_revenue[channel] = channel_revenue.get(channel, 0.0) + revenue
            for quality, quantity in run.quality_harvested.items():
                quality_totals[quality] = quality_totals.get(quality, 0) + quantity

        summary[strategy] = {
            "num_runs": len(runs),
            "avg_final_money": round(statistics.mean(final_moneys), 2),
            "median_final_money": round(statistics.median(final_moneys), 2),
            "min_final_money": round(min(final_moneys), 2),
            "max_final_money": round(max(final_moneys), 2),
            "bankruptcy_rate": round(100 * bankrupt_count / len(runs), 2),
            "avg_first_upgrade_day": round(statistics.mean(first_days), 2) if first_days else None,
            "avg_second_upgrade_day": round(statistics.mean(second_days), 2) if second_days else None,
            "crop_usage_pct": crop_usage_pct,
            "avg_crop_loss_rate": round(statistics.mean(r.crop_loss_rate for r in runs), 2),
            "avg_watering_rate": round(statistics.mean(r.watering_rate for r in runs), 2),
            "avg_fertilizer_applications": round(statistics.mean(r.fertilizer_applications for r in runs), 2),
            "avg_spoiled_units": round(statistics.mean(r.spoiled_units for r in runs), 2),
            "avg_processed_units": round(statistics.mean(r.processed_units for r in runs), 2),
            "avg_contracts_completed": round(statistics.mean(r.contracts_completed for r in runs), 2),
            "avg_contracts_failed": round(statistics.mean(r.contracts_failed for r in runs), 2),
            "avg_final_reputation": round(statistics.mean(r.final_reputation for r in runs), 2),
            "revenue_by_channel": {key: round(value / len(runs), 2) for key, value in channel_revenue.items()},
            "quality_harvested": quality_totals,
        }
    return summary
