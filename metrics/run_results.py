"""Per-run metrics: one RunResult per completed simulation, plus CSV export."""

import csv
import json
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")


def _money(value) -> float:
    """Return one canonical, cent-rounded monetary value."""
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _money_sum(values) -> float:
    """Sum already-independent monetary components without float drift."""
    total = sum((Decimal(str(value)) for value in values), Decimal("0"))
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))


def _round_or_none(value, ndigits: int = 2):
    """Round a ratio that may be undefined (no denominator to measure it against).

    None here means "not observed this run", not "observed as zero" -- callers
    (metrics/aggregate_results.py) must not fold it into a mean as a real 0.0.
    """
    return None if value is None else round(value, ndigits)


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
    crop_loss_rate: object  # float, or None if no crop matured (undefined, not 0%)
    watering_rate: float
    occupied_watering_rate: object  # float, or None if no slot was ever occupied
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
    # Which replicate of the sampling plan produced this run. `None` under the
    # legacy schedule's own callers that never set it; under any plan in
    # runner/sampling_plan.py it is the index that lines this run up with the
    # *same* replicate of every other strategy, which is what a paired
    # comparison joins on. Defaulted so every existing constructor call keeps
    # working unchanged.
    replicate_id: object = None


def build_run_result(
    player,
    strategy_name: str,
    seed: int,
    days_simulated: int,
    crops: list,
    upgrades: list,
    replicate_id=None,
) -> RunResult:
    expenses = {key: _money(value) for key, value in player.expenses_by_category.items()}
    revenue_by_channel = {key: _money(value) for key, value in player.revenue_by_channel.items()}
    total_revenue = _money_sum(revenue_by_channel.values())
    total_expenses = _money_sum(expenses.values())
    production_costs = _money_sum(
        expenses.get(category, 0.0) for category in ("seeds", "watering", "fertilizer")
    )
    net_profit = _money(total_revenue - total_expenses)
    gross_profit = _money(total_revenue - production_costs)
    operating_profit = _money(gross_profit - expenses.get("contract_penalties", 0.0))

    crop_counts = {crop["id"]: player.crop_plant_counts.get(crop["id"], 0) for crop in crops}
    crop_percentages = (
        {cid: round(100 * count / player.total_planted, 2) for cid, count in crop_counts.items()}
        if player.total_planted
        else {}
    )

    upgrade_days = sorted(player.upgrade_purchase_days.values())
    first_upgrade_day = upgrade_days[0] if len(upgrade_days) > 0 else None
    second_upgrade_day = upgrade_days[1] if len(upgrade_days) > 1 else None

    avg_profit_per_day = net_profit / days_simulated if days_simulated else 0.0
    avg_profit_per_slot_day = net_profit / player.slot_days if player.slot_days else 0.0
    # None (not 0.0) when there's nothing to measure the ratio against -- a
    # run with no harvest events had no loss rate to observe, and one with
    # no occupied plot-days had no watering-of-occupied-slots to observe.
    # Folding either into 0% would misrepresent "unobserved" as "perfect."
    crop_loss_rate = (
        100 * player.total_crops_lost / player.total_harvest_events
        if player.total_harvest_events
        else None
    )
    watering_rate = 100 * player.total_waterings / player.slot_days if player.slot_days else 0.0
    occupied_watering_rate = (
        100 * player.total_waterings / player.occupied_slot_days
        if player.occupied_slot_days
        else None
    )

    rounded_observations = {
        crop_id: dict(observation)
        for crop_id, observation in player.crop_decision_observations.items()
    }

    return RunResult(
        strategy=strategy_name,
        seed=seed,
        days_simulated=days_simulated,
        final_money=_money(player.money),
        total_revenue=total_revenue,
        total_expenses=total_expenses,
        total_costs=total_expenses,
        net_profit=net_profit,
        gross_profit=gross_profit,
        operating_profit=operating_profit,
        net_cash_change=net_profit,
        expenses_by_category=expenses,
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
        lowest_money=_money(player.lowest_money),
        minimum_cash_balance=_money(player.lowest_money),
        highest_money=_money(player.highest_money),
        crops_lost=player.total_crops_lost,
        crop_loss_rate=_round_or_none(crop_loss_rate),
        watering_rate=round(watering_rate, 2),
        occupied_watering_rate=_round_or_none(occupied_watering_rate),
        occupied_slot_days=player.occupied_slot_days,
        fertilizer_applications=player.total_fertilizer_applied,
        spoiled_units=player.total_spoiled,
        processed_units=player.total_processed,
        contracts_completed=player.contracts_completed,
        contracts_failed=player.contracts_failed,
        final_reputation=round(player.reputation, 2),
        revenue_by_channel=revenue_by_channel,
        quality_harvested=dict(player.quality_harvested),
        crop_decision_observations=rounded_observations,
        replicate_id=replicate_id,
    )


def write_csv(results: list, path: str, crop_ids: list) -> None:
    fieldnames = [
        "strategy",
        "seed",
        "replicate_id",
        "days_simulated",
        "final_money",
        "total_revenue",
        "total_expenses",
        "total_costs",
        "net_profit",
        "crops_planted",
        "crops_harvested",
        "crops_sold",
        "gross_profit",
        "operating_profit",
        "net_cash_change",
        "expenses_by_category",
        "avg_profit_per_day",
        "avg_profit_per_slot_day",
        "first_upgrade_day",
        "second_upgrade_day",
        "idle_days",
        "bankrupt",
        "bankruptcy_day",
        "bankruptcy_reason",
        "lowest_money",
        "minimum_cash_balance",
        "highest_money",
        "crops_lost",
        "crop_loss_rate",
        "watering_rate",
        "occupied_watering_rate",
        "occupied_slot_days",
        "fertilizer_applications",
        "spoiled_units",
        "processed_units",
        "contracts_completed",
        "contracts_failed",
        "final_reputation",
        "revenue_by_channel",
        "quality_harvested",
        "crop_decision_observations",
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
            row["crop_decision_observations"] = json.dumps(
                row["crop_decision_observations"], sort_keys=True
            )
            writer.writerow(row)
