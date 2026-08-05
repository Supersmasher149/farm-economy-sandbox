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
    total_costs: float
    net_profit: float
    gross_profit: float
    operating_profit: float
    net_cash_change: float
    expenses_by_category: dict
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
    bankruptcy_day: object
    bankruptcy_reason: object
    lowest_money: float
    minimum_cash_balance: float
    highest_money: float
    crops_lost: int
    crop_loss_rate: float
    watering_rate: float
    occupied_watering_rate: float
    occupied_slot_days: int
    fertilizer_applications: int
    spoiled_units: int
    processed_units: int
    contracts_completed: int
    contracts_failed: int
    final_reputation: float
    revenue_by_channel: dict
    quality_harvested: dict
    crop_decision_observations: dict


def build_run_result(player, strategy_name: str, seed: int, days_simulated: int, crops: list, upgrades: list) -> RunResult:
    net_profit = player.total_revenue - player.total_expenses
    expenses = dict(player.expenses_by_category)
    production_costs = sum(expenses.get(category, 0.0) for category in ("seeds", "watering", "fertilizer"))
    gross_profit = player.total_revenue - production_costs
    operating_profit = gross_profit - expenses.get("contract_penalties", 0.0)

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
    occupied_watering_rate = (
        100 * player.total_waterings / player.occupied_slot_days
        if player.occupied_slot_days else 0.0
    )

    rounded_expenses = {key: round(value, 2) for key, value in expenses.items()}
    rounded_observations = {
        crop_id: dict(observation)
        for crop_id, observation in player.crop_decision_observations.items()
    }

    return RunResult(
        strategy=strategy_name,
        seed=seed,
        days_simulated=days_simulated,
        final_money=round(player.money, 2),
        total_revenue=round(player.total_revenue, 2),
        total_expenses=round(player.total_expenses, 2),
        total_costs=round(player.total_expenses, 2),
        net_profit=round(net_profit, 2),
        gross_profit=round(gross_profit, 2),
        operating_profit=round(operating_profit, 2),
        net_cash_change=round(net_profit, 2),
        expenses_by_category=rounded_expenses,
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
        bankruptcy_day=player.bankruptcy_day,
        bankruptcy_reason=player.bankruptcy_reason,
        lowest_money=round(player.lowest_money, 2),
        minimum_cash_balance=round(player.lowest_money, 2),
        highest_money=round(player.highest_money, 2),
        crops_lost=player.total_crops_lost,
        crop_loss_rate=round(crop_loss_rate, 2),
        watering_rate=round(watering_rate, 2),
        occupied_watering_rate=round(occupied_watering_rate, 2),
        occupied_slot_days=player.occupied_slot_days,
        fertilizer_applications=player.total_fertilizer_applied,
        spoiled_units=player.total_spoiled,
        processed_units=player.total_processed,
        contracts_completed=player.contracts_completed,
        contracts_failed=player.contracts_failed,
        final_reputation=round(player.reputation, 2),
        revenue_by_channel={key: round(value, 2) for key, value in player.revenue_by_channel.items()},
        quality_harvested=dict(player.quality_harvested),
        crop_decision_observations=rounded_observations,
    )


def write_csv(results: list, path: str, crop_ids: list) -> None:
    fieldnames = [
        "strategy", "seed", "days_simulated", "final_money", "total_revenue", "total_expenses",
        "total_costs",
        "net_profit", "crops_planted", "crops_harvested", "crops_sold",
        "gross_profit", "operating_profit", "net_cash_change", "expenses_by_category",
        "avg_profit_per_day", "avg_profit_per_slot_day",
        "first_upgrade_day", "second_upgrade_day", "idle_days", "bankrupt", "bankruptcy_day",
        "bankruptcy_reason", "lowest_money", "minimum_cash_balance", "highest_money",
        "crops_lost", "crop_loss_rate", "watering_rate", "occupied_watering_rate",
        "occupied_slot_days", "fertilizer_applications",
        "spoiled_units", "processed_units", "contracts_completed", "contracts_failed",
        "final_reputation", "revenue_by_channel", "quality_harvested", "crop_decision_observations",
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
            row["expenses_by_category"] = json.dumps(row["expenses_by_category"], sort_keys=True)
            row["revenue_by_channel"] = json.dumps(row["revenue_by_channel"], sort_keys=True)
            row["quality_harvested"] = json.dumps(row["quality_harvested"], sort_keys=True)
            row["crop_decision_observations"] = json.dumps(row["crop_decision_observations"], sort_keys=True)
            writer.writerow(row)
