"""Per-run metrics: one RunResult per completed simulation, plus CSV export."""
import csv
import json
from dataclasses import asdict, dataclass


@dataclass
class RunResult:
    strategy: str
    seed: int
    days_simulated: int
    final_money: float
    total_revenue: float
    total_expenses: float
    net_profit: float
    crops_planted: int
    crops_harvested: int
    crops_sold: int
    crop_counts: dict
    crop_percentages: dict
    avg_profit_per_day: float
    avg_profit_per_slot_day: float
    first_upgrade_day: object
    second_upgrade_day: object
    idle_days: int
    bankrupt: bool
    lowest_money: float
    highest_money: float
    crops_lost: int
    crop_loss_rate: float
    watering_rate: float
    fertilizer_applications: int
    spoiled_units: int
    processed_units: int
    contracts_completed: int
    contracts_failed: int
    final_reputation: float
    revenue_by_channel: dict
    quality_harvested: dict


def build_run_result(player, strategy_name: str, seed: int, days_simulated: int, crops: list, upgrades: list) -> RunResult:
    net_profit = player.total_revenue - player.total_expenses

    crop_counts = {crop["id"]: player.crop_plant_counts.get(crop["id"], 0) for crop in crops}
    crop_percentages = {
        cid: round(100 * count / player.total_planted, 2)
        for cid, count in crop_counts.items()
    } if player.total_planted else {}

    upgrade_days = sorted(player.upgrade_purchase_days.values())
    first_upgrade_day = upgrade_days[0] if len(upgrade_days) > 0 else None
    second_upgrade_day = upgrade_days[1] if len(upgrade_days) > 1 else None

    avg_profit_per_day = net_profit / days_simulated if days_simulated else 0.0
    avg_profit_per_slot_day = net_profit / player.slot_days if player.slot_days else 0.0
    crop_loss_rate = 100 * player.total_crops_lost / player.total_harvest_events if player.total_harvest_events else 0.0
    watering_rate = 100 * player.total_waterings / player.slot_days if player.slot_days else 0.0

    return RunResult(
        strategy=strategy_name,
        seed=seed,
        days_simulated=days_simulated,
        final_money=round(player.money, 2),
        total_revenue=round(player.total_revenue, 2),
        total_expenses=round(player.total_expenses, 2),
        net_profit=round(net_profit, 2),
        crops_planted=player.total_planted,
        crops_harvested=player.total_harvested,
        crops_sold=player.total_sold,
        crop_counts=crop_counts,
        crop_percentages=crop_percentages,
        avg_profit_per_day=round(avg_profit_per_day, 4),
        avg_profit_per_slot_day=round(avg_profit_per_slot_day, 4),
        first_upgrade_day=first_upgrade_day,
        second_upgrade_day=second_upgrade_day,
        idle_days=player.idle_days,
        bankrupt=player.bankrupt,
        lowest_money=round(player.lowest_money, 2),
        highest_money=round(player.highest_money, 2),
        crops_lost=player.total_crops_lost,
        crop_loss_rate=round(crop_loss_rate, 2),
        watering_rate=round(watering_rate, 2),
        fertilizer_applications=player.total_fertilizer_applied,
        spoiled_units=player.total_spoiled,
        processed_units=player.total_processed,
        contracts_completed=player.contracts_completed,
        contracts_failed=player.contracts_failed,
        final_reputation=round(player.reputation, 2),
        revenue_by_channel={key: round(value, 2) for key, value in player.revenue_by_channel.items()},
        quality_harvested=dict(player.quality_harvested),
    )


def write_csv(results: list, path: str, crop_ids: list) -> None:
    fieldnames = [
        "strategy", "seed", "days_simulated", "final_money", "total_revenue", "total_expenses",
        "net_profit", "crops_planted", "crops_harvested", "crops_sold",
        "avg_profit_per_day", "avg_profit_per_slot_day",
        "first_upgrade_day", "second_upgrade_day", "idle_days", "bankrupt",
        "lowest_money", "highest_money",
        "crops_lost", "crop_loss_rate", "watering_rate", "fertilizer_applications",
        "spoiled_units", "processed_units", "contracts_completed", "contracts_failed",
        "final_reputation", "revenue_by_channel", "quality_harvested",
    ] + [f"pct_{cid}" for cid in crop_ids]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row = asdict(r)
            for cid in crop_ids:
                row[f"pct_{cid}"] = r.crop_percentages.get(cid, 0.0)
            row.pop("crop_counts", None)
            row.pop("crop_percentages", None)
            row["revenue_by_channel"] = json.dumps(row["revenue_by_channel"], sort_keys=True)
            row["quality_harvested"] = json.dumps(row["quality_harvested"], sort_keys=True)
            writer.writerow(row)
