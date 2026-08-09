"""Assembles the markdown summary report for a batch run. Written for a
game designer, not just a programmer -- plain language, one section per
strategy, warnings called out at the top.
"""


def _r(value, ndigits: int = 2):
    """Round a stat for display. Some fields (avg_final_money, bankruptcy_rate,
    avg_first_upgrade_day, first_upgrade_rate, crop_usage_pct,
    avg_crop_loss_rate) come back unrounded from aggregate_results.py on
    purpose, so warning-threshold comparisons see full precision; this
    rounds them at the point they're actually displayed instead. `None`
    (an undefined ratio, not an observed zero) passes through unchanged.
    """
    return value if value is None else round(value, ndigits)


def generate_markdown_report(
    config: dict,
    num_runs: int,
    summary: dict,
    warning_list: list,
    crop_names: dict,
    agent_descriptions: dict = None,
    economics_audit: dict = None,
    base_seed: int = None,
) -> str:
    agent_descriptions = agent_descriptions or {}
    lines = []
    lines.append("# Farm Economy Batch Report")
    lines.append("")
    lines.append(f"- Simulated days per run: **{config['days']}**")
    lines.append(f"- Runs per strategy: **{num_runs}**")
    lines.append(f"- Base seed: **{base_seed}**")
    lines.append(f"- Starting money: **{config['start_money']}**")
    lines.append(f"- Starting growing slots: **{config['start_slots']}**")
    lines.append("")
    reservoir_capacity = next(
        (stats.get("median_reservoir_capacity") for stats in summary.values()),
        "the configured",
    )
    lines.append(
        "Median values are exact while each cohort has at most "
        f"{reservoir_capacity} observations in the deterministic reservoir; "
        "larger cohorts use approximate medians."
    )
    lines.append("")

    if economics_audit:
        lines.append("## Economics Audit")
        lines.append("")
        lines.append(
            "Nominal values use configured base prices, average yield, and configured loss chance; they exclude weather, quality, capacity, and neglect effects."
        )
        lines.append("")
        for crop in economics_audit["crops"]:
            lines.append(
                f"- Crop `{crop['id']}`: seed {crop['seed_cost']}, growth {crop['growth_days']} days, "
                f"yield {crop['yield_range'][0]}-{crop['yield_range'][1]}, base sale {crop['base_sale_price']}, "
                f"loss {crop['loss_chance_pct']}%, expected revenue/cycle {crop['expected_revenue_per_cycle']}, "
                f"nominal profit/cycle {crop['nominal_profit_per_cycle']}, "
                f"profit/day {crop['nominal_profit_per_growth_day']}, "
                f"fertilizer marginal profit {crop['fertilizer_marginal_profit']}"
            )
        fertilizer = economics_audit["fertilizer"]
        lines.append(
            f"- Fertilizer: cost {fertilizer['cost']}, yield bonus {fertilizer['yield_bonus_pct']}%, "
            f"loss-chance reduction {fertilizer['loss_chance_reduction_pct']}%."
        )
        for channel in economics_audit["market_channels"]:
            lines.append(
                f"- Market `{channel['id']}`: price multiplier {channel['price_multiplier']}, "
                f"minimum quality {channel['min_quality']}, capacity {channel['daily_capacity']}, "
                f"flat fee {channel['flat_fee']}, fee rate {channel['fee_rate_pct']}%."
            )
        lines.append("")

    lines.append("## Accounting Definitions")
    lines.append("")
    lines.append(
        "- Total costs include every recorded cash outflow; gross profit subtracts seeds, watering, and fertilizer; operating profit also subtracts contract penalties; net cash change includes upgrades and all other costs."
    )
    lines.append("")

    lines.append("## Warnings")
    lines.append("")
    if warning_list:
        for w in warning_list:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("- No balance warnings triggered.")
    lines.append("")

    lines.append("## Results by Strategy")
    lines.append("")
    for strategy, stats in summary.items():
        lines.append(f"### {strategy}")
        lines.append("")
        if agent_descriptions.get(strategy):
            lines.append(f"*{agent_descriptions[strategy]}*")
            lines.append("")
        lines.append(f"- Runs: {stats['num_runs']}")
        lines.append(f"- Average final money: {_r(stats['avg_final_money'])}")
        lines.append(f"- Median final money: {stats['median_final_money']}")
        lines.append(
            f"- Min / Max final money: {stats['min_final_money']} / {stats['max_final_money']}"
        )
        lines.append(
            f"- Surviving / bankrupt runs: {stats['surviving_runs']} / {stats['bankrupt_runs']}"
        )
        lines.append(
            f"- Average final money, survivors / bankrupt: "
            f"{stats['avg_final_money_survivors']} / {stats['avg_final_money_bankrupt']}"
        )
        lines.append(
            f"- Median final money, survivors / bankrupt: "
            f"{stats['median_final_money_survivors']} / {stats['median_final_money_bankrupt']}"
        )
        lines.append(f"- Bankruptcy rate: {_r(stats['bankruptcy_rate'])}%")
        lines.append(
            f"- Bankruptcy day, average / range: {stats['avg_bankruptcy_day']} / "
            f"{stats['min_bankruptcy_day']} - {stats['max_bankruptcy_day']}"
        )
        lines.append(
            f"- Minimum cash balance, all runs / bankrupt runs: "
            f"{stats['avg_minimum_cash_balance']} / {stats['avg_minimum_cash_balance_bankrupt']}"
        )
        lines.append(f"- Bankruptcy reasons: {stats['bankruptcy_reasons'] or 'None'}")
        lines.append(
            f"- First upgrade reach: {stats['first_upgrade_count']} / {stats['num_runs']} runs "
            f"({_r(stats['first_upgrade_rate'])}%)"
        )
        lines.append(
            f"- Second upgrade reach: {stats['second_upgrade_count']} / {stats['num_runs']} runs "
            f"({stats['second_upgrade_rate']}%)"
        )
        lines.append(
            f"- Average day of first upgrade (purchasing runs): {_r(stats['avg_first_upgrade_day'])}"
        )
        lines.append(
            f"- Average day of second upgrade (purchasing runs): {stats['avg_second_upgrade_day']}"
        )
        lines.append(f"- Watering coverage: {stats['avg_watering_rate']}% of plot-days")
        lines.append(
            f"- Watering coverage of occupied plot-days: {stats['avg_occupied_watering_rate']}%"
        )
        lines.append(f"- Crop loss rate: {_r(stats['avg_crop_loss_rate'])}% of matured crops")
        lines.append(
            f"- Avg fertilizer applications per run: {stats['avg_fertilizer_applications']}"
        )
        lines.append(
            f"- Avg spoiled / processed units: {stats['avg_spoiled_units']} / {stats['avg_processed_units']}"
        )
        lines.append(
            f"- Avg contracts completed / failed: {stats['avg_contracts_completed']} / {stats['avg_contracts_failed']}"
        )
        lines.append(f"- Average final reputation: {stats['avg_final_reputation']}")
        lines.append(f"- Average total costs: {stats['avg_total_costs']}")
        lines.append(
            f"- Average gross / operating / net cash change: {stats['avg_gross_profit']} / {stats['avg_operating_profit']} / {stats['avg_net_cash_change']}"
        )
        lines.append(f"- Average costs by category: {stats['avg_expenses_by_category'] or 'None'}")
        lines.append(f"- Average revenue by channel: {stats['revenue_by_channel'] or 'None'}")
        lines.append(f"- Harvest quality mix: {stats['quality_harvested']}")
        lines.append("- Crop usage:")
        if not stats.get("crop_usage_observed", True):
            lines.append("- No crops were planted in any run.")
        else:
            for cid, pct in sorted(stats["crop_usage_pct"].items(), key=lambda kv: -kv[1]):
                name = crop_names.get(cid, cid)
                lines.append(f"- {name}: {_r(pct)}%")
        for cid, observation in sorted(stats["crop_decision_observations"].items()):
            opportunities = observation.get("opportunities", 0)
            unlocked_pct = (
                round(100 * observation.get("unlocked", 0) / opportunities, 2)
                if opportunities
                else 0.0
            )
            affordable_pct = (
                round(100 * observation.get("affordable", 0) / opportunities, 2)
                if opportunities
                else 0.0
            )
            lines.append(
                f"- Crop decision `{cid}`: opportunities {opportunities}, unlocked {unlocked_pct}%, "
                f"affordable {affordable_pct}%, selected {observation.get('selected', 0)}, "
                f"blocked locked/unaffordable {observation.get('blocked_locked', 0)}/"
                f"{observation.get('blocked_unaffordable', 0)}"
            )
        lines.append("")

    return "\n".join(lines)
