"""Aggregate a batch's RunResults into per-strategy summary statistics.

Statistics are accumulated incrementally, one RunResult at a time, rather
than by materializing every run into per-strategy lists -- so aggregating a
multi-million-run batch doesn't require holding the whole batch in memory.
Almost every stat here is a running sum/count, running min/max, or running
dict-sum, all of which are O(1) per run. Median fields use deterministic,
fixed-capacity reservoirs, so they are exact up to the capacity and
approximate for larger cohorts while memory remains bounded.

Running means use Neumaier (improved Kahan-Babuska) compensated summation
instead of naive `total += value`, so they stay numerically close to
statistics.mean's higher-precision internal calculation and don't quietly
drift from it at the 2-decimal rounding this module reports at.

`aggregate(results) -> dict` is kept as a convenience wrapper with the same
signature and output as before (works on a list or any other iterable, in a
single pass). Batch callers that want to interleave aggregation with other
per-result work (e.g. streaming a CSV row per result) should drive
`BatchAggregator` directly instead.
"""

import hashlib
import random
import statistics

MEDIAN_RESERVOIR_CAPACITY = 1024


class _DeterministicReservoir:
    """Bounded reservoir sampling with a stable per-field random stream."""

    __slots__ = ("capacity", "values", "seen", "_rng")

    def __init__(self, key: str, capacity: int = MEDIAN_RESERVOIR_CAPACITY):
        self.capacity = capacity
        self.values = []
        self.seen = 0
        seed = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big")
        self._rng = random.Random(seed)

    def add(self, value) -> None:
        self.seen += 1
        if len(self.values) < self.capacity:
            self.values.append(value)
            return
        replacement = self._rng.randrange(self.seen)
        if replacement < self.capacity:
            self.values[replacement] = value

    def median(self):
        return round(statistics.median(self.values), 2) if self.values else None

    @property
    def is_approximate(self) -> bool:
        return self.seen > self.capacity


class _MeanAccumulator:
    """Running mean via Neumaier compensated summation. count==0 -> None."""

    __slots__ = ("total", "comp", "count")

    def __init__(self):
        self.total = 0.0
        self.comp = 0.0
        self.count = 0

    def add(self, value) -> None:
        t = self.total + value
        if abs(self.total) >= abs(value):
            self.comp += (self.total - t) + value
        else:
            self.comp += (value - t) + self.total
        self.total = t
        self.count += 1

    def mean(self):
        return round((self.total + self.comp) / self.count, 2) if self.count else None


# (summary-dict mean field name, RunResult attribute) for the per-run stats
# whose mean is computed over every run regardless of bankruptcy/upgrades.
_SIMPLE_MEAN_FIELDS = (
    ("crop_loss_rate", "crop_loss_rate"),
    ("watering_rate", "watering_rate"),
    ("occupied_watering_rate", "occupied_watering_rate"),
    ("occupied_slot_days", "occupied_slot_days"),
    ("fertilizer_applications", "fertilizer_applications"),
    ("spoiled_units", "spoiled_units"),
    ("processed_units", "processed_units"),
    ("contracts_completed", "contracts_completed"),
    ("contracts_failed", "contracts_failed"),
    ("final_reputation", "final_reputation"),
    ("total_costs", "total_expenses"),
    ("gross_profit", "gross_profit"),
    ("operating_profit", "operating_profit"),
    ("net_cash_change", "net_cash_change"),
)

# Mean fields that are conditional (only some runs contribute) and so need
# their own named accumulator rather than a blanket per-run add.
_CONDITIONAL_MEAN_FIELDS = (
    "final_money",
    "final_money_survivors",
    "final_money_bankrupt",
    "bankruptcy_day",
    "minimum_cash_balance",
    "minimum_cash_balance_bankrupt",
    "first_upgrade_day",
    "second_upgrade_day",
)


class _StrategyAccumulator:
    def __init__(self, strategy: str):
        self.count = 0
        self.means = {
            name: _MeanAccumulator()
            for name in _CONDITIONAL_MEAN_FIELDS + tuple(name for name, _ in _SIMPLE_MEAN_FIELDS)
        }

        self.final_money_min = None
        self.final_money_max = None
        self.bankruptcy_day_min = None
        self.bankruptcy_day_max = None

        self.all_money_values = _DeterministicReservoir(f"{strategy}:all_money")
        self.survivor_money_values = _DeterministicReservoir(f"{strategy}:survivors")
        self.bankrupt_money_values = _DeterministicReservoir(f"{strategy}:bankrupt")
        self.bankruptcy_day_values = _DeterministicReservoir(f"{strategy}:bankruptcy_day")

        self.survivor_count = 0
        self.bankrupt_count = 0
        self.first_upgrade_count = 0
        self.second_upgrade_count = 0

        self.crop_totals = {}
        self.planted_total = 0
        self.channel_revenue = {}
        self.quality_totals = {}
        self.expense_totals = {}
        self.crop_observations = {}
        self.bankruptcy_reasons = {}

    def add(self, r) -> None:
        self.count += 1
        means = self.means

        self.all_money_values.add(r.final_money)
        means["final_money"].add(r.final_money)
        if self.final_money_min is None or r.final_money < self.final_money_min:
            self.final_money_min = r.final_money
        if self.final_money_max is None or r.final_money > self.final_money_max:
            self.final_money_max = r.final_money

        if r.bankrupt:
            self.bankrupt_count += 1
            self.bankrupt_money_values.add(r.final_money)
            means["final_money_bankrupt"].add(r.final_money)
            means["minimum_cash_balance_bankrupt"].add(r.minimum_cash_balance)
            if r.bankruptcy_day is not None:
                self.bankruptcy_day_values.add(r.bankruptcy_day)
                means["bankruptcy_day"].add(r.bankruptcy_day)
                if self.bankruptcy_day_min is None or r.bankruptcy_day < self.bankruptcy_day_min:
                    self.bankruptcy_day_min = r.bankruptcy_day
                if self.bankruptcy_day_max is None or r.bankruptcy_day > self.bankruptcy_day_max:
                    self.bankruptcy_day_max = r.bankruptcy_day
            reason = r.bankruptcy_reason or "unknown"
            self.bankruptcy_reasons[reason] = self.bankruptcy_reasons.get(reason, 0) + 1
        else:
            self.survivor_count += 1
            self.survivor_money_values.add(r.final_money)
            means["final_money_survivors"].add(r.final_money)

        means["minimum_cash_balance"].add(r.minimum_cash_balance)

        if r.first_upgrade_day is not None:
            self.first_upgrade_count += 1
            means["first_upgrade_day"].add(r.first_upgrade_day)
        if r.second_upgrade_day is not None:
            self.second_upgrade_count += 1
            means["second_upgrade_day"].add(r.second_upgrade_day)

        for name, attr in _SIMPLE_MEAN_FIELDS:
            means[name].add(getattr(r, attr))

        self.planted_total += r.crops_planted
        for cid, count in r.crop_counts.items():
            self.crop_totals[cid] = self.crop_totals.get(cid, 0) + count
        for channel, revenue in r.revenue_by_channel.items():
            self.channel_revenue[channel] = self.channel_revenue.get(channel, 0.0) + revenue
        for quality, quantity in r.quality_harvested.items():
            self.quality_totals[quality] = self.quality_totals.get(quality, 0) + quantity
        for category, amount in r.expenses_by_category.items():
            self.expense_totals[category] = self.expense_totals.get(category, 0.0) + amount
        for cid, observation in r.crop_decision_observations.items():
            aggregate_observation = self.crop_observations.setdefault(cid, {})
            for key, value in observation.items():
                aggregate_observation[key] = aggregate_observation.get(key, 0) + value

    def finalize(self) -> dict:
        means = self.means
        median_approximate = any(
            reservoir.is_approximate
            for reservoir in (
                self.all_money_values,
                self.survivor_money_values,
                self.bankrupt_money_values,
                self.bankruptcy_day_values,
            )
        )

        return {
            "num_runs": self.count,
            "avg_final_money": means["final_money"].mean(),
            "median_final_money": self.all_money_values.median(),
            "min_final_money": round(self.final_money_min, 2),
            "max_final_money": round(self.final_money_max, 2),
            "surviving_runs": self.survivor_count,
            "bankrupt_runs": self.bankrupt_count,
            "avg_final_money_survivors": means["final_money_survivors"].mean(),
            "median_final_money_survivors": self.survivor_money_values.median(),
            "avg_final_money_bankrupt": means["final_money_bankrupt"].mean(),
            "median_final_money_bankrupt": self.bankrupt_money_values.median(),
            "bankruptcy_rate": round(100 * self.bankrupt_count / self.count, 2),
            "avg_bankruptcy_day": means["bankruptcy_day"].mean(),
            "median_bankruptcy_day": self.bankruptcy_day_values.median(),
            "min_bankruptcy_day": self.bankruptcy_day_min,
            "max_bankruptcy_day": self.bankruptcy_day_max,
            "avg_minimum_cash_balance": means["minimum_cash_balance"].mean(),
            "avg_minimum_cash_balance_bankrupt": means["minimum_cash_balance_bankrupt"].mean(),
            "bankruptcy_reasons": self.bankruptcy_reasons,
            "avg_first_upgrade_day": means["first_upgrade_day"].mean(),
            "avg_second_upgrade_day": means["second_upgrade_day"].mean(),
            "first_upgrade_count": self.first_upgrade_count,
            "second_upgrade_count": self.second_upgrade_count,
            "first_upgrade_rate": round(100 * self.first_upgrade_count / self.count, 2),
            "second_upgrade_rate": round(100 * self.second_upgrade_count / self.count, 2),
            "median_reservoir_capacity": MEDIAN_RESERVOIR_CAPACITY,
            "median_approximate": median_approximate,
            "crop_usage_pct": {
                cid: round(100 * count / self.planted_total, 2) if self.planted_total else 0.0
                for cid, count in self.crop_totals.items()
            },
            "avg_crop_loss_rate": means["crop_loss_rate"].mean(),
            "avg_watering_rate": means["watering_rate"].mean(),
            "avg_occupied_watering_rate": means["occupied_watering_rate"].mean(),
            "avg_occupied_slot_days": means["occupied_slot_days"].mean(),
            "avg_fertilizer_applications": means["fertilizer_applications"].mean(),
            "avg_spoiled_units": means["spoiled_units"].mean(),
            "avg_processed_units": means["processed_units"].mean(),
            "avg_contracts_completed": means["contracts_completed"].mean(),
            "avg_contracts_failed": means["contracts_failed"].mean(),
            "avg_final_reputation": means["final_reputation"].mean(),
            "avg_total_costs": means["total_costs"].mean(),
            "avg_gross_profit": means["gross_profit"].mean(),
            "avg_operating_profit": means["operating_profit"].mean(),
            "avg_net_cash_change": means["net_cash_change"].mean(),
            "avg_expenses_by_category": {
                key: round(value / self.count, 2) for key, value in self.expense_totals.items()
            },
            "revenue_by_channel": {
                key: round(value / self.count, 2) for key, value in self.channel_revenue.items()
            },
            "quality_harvested": self.quality_totals,
            "crop_decision_observations": self.crop_observations,
        }


class BatchAggregator:
    """Streaming aggregator: call .add(result) once per RunResult, in any
    order or interleaving with other per-result work, then .finalize() once
    at the end to get the same summary dict aggregate() returns."""

    def __init__(self):
        self._by_strategy = {}

    def add(self, result) -> None:
        acc = self._by_strategy.get(result.strategy)
        if acc is None:
            acc = _StrategyAccumulator(result.strategy)
            self._by_strategy[result.strategy] = acc
        acc.add(result)

    def finalize(self) -> dict:
        return {strategy: acc.finalize() for strategy, acc in self._by_strategy.items()}


def aggregate(results) -> dict:
    aggregator = BatchAggregator()
    for r in results:
        aggregator.add(r)
    return aggregator.finalize()
