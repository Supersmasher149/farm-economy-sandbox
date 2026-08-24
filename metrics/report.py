"""Assembles the markdown summary report for a batch run. Written for a
game designer, not just a programmer -- plain language, one section per
strategy, warnings called out at the top.

Statistical sections (design, intervals, comparisons, convergence) are
rendered from documents built elsewhere -- `metrics/inference.py`,
`metrics/comparisons.py`, `runner/adaptive.py` -- and this module never
computes an interval or a p-value itself. It is a renderer: if a number
appears here it appears identically in `summary.json`, and a reader can point
at either.
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
    confidence: float = None,
    sampling_plan: dict = None,
    stop_reason: str = None,
    convergence: dict = None,
    comparisons_doc: dict = None,
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

    if sampling_plan or confidence is not None:
        lines.extend(_statistical_design_section(sampling_plan, confidence, stop_reason))
    lines.extend(_intervals_section(summary, confidence))
    if convergence and len(convergence.get("checkpoints", [])) > 1:
        lines.extend(_convergence_section(convergence))
    if comparisons_doc:
        lines.extend(_comparisons_section(comparisons_doc))

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
        lines.append(f"- Average profit per day: {stats['avg_profit_per_day']}")
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


def _fmt(value, ndigits: int = 2) -> str:
    """Render a possibly-undefined number. `—` means "not observed", which is
    deliberately not the same glyph as a zero."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{round(value, ndigits):,}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def _statistical_design_section(sampling_plan, confidence, stop_reason) -> list:
    plan = sampling_plan or {}
    lines = ["## Statistical Design", ""]
    lines.append(f"- Sampling plan: `{plan.get('plan', 'unknown')}`")
    if plan.get("paired"):
        lines.append(
            "- Pairing: **weak** -- strategies share a run seed per replicate, but "
            "differing decisions consume differing RNG draws, so environments diverge "
            "within a run. Measured correlation and variance reduction are reported "
            "per comparison rather than assumed."
        )
    if confidence is not None:
        lines.append(f"- Confidence level: {confidence:.0%}")
    lines.append(
        "- Interval methods: Student-t for means, Wilson score for proportions, "
        "deterministic percentile bootstrap for quantiles."
    )
    if stop_reason:
        lines.append(f"- Stop reason: `{stop_reason}`")
    lines.append(
        "- Every interval below is an interval on the *estimate*, not the spread of "
        "outcomes: the outcome standard deviation is reported separately."
    )
    lines.append("")
    return lines


def _intervals_section(summary: dict, confidence) -> list:
    """Per-strategy intervals for the headline estimands.

    Standard deviation and interval half-width sit in adjacent columns on
    purpose: they answer different questions ("how varied are runs?" versus
    "how well do we know the average?") and are the two numbers most often
    conflated when a batch is read quickly.
    """
    rows = []
    for strategy, stats in summary.items():
        inference = stats.get("inference") or {}
        for estimand_id in (
            "expected_final_money",
            "bankruptcy_probability",
            "expected_profit_per_day",
        ):
            estimate = inference.get(estimand_id)
            if not estimate:
                continue
            rows.append((strategy, estimand_id, estimate))
    if not rows:
        return []

    level = f"{confidence:.0%}" if confidence is not None else "95%"
    lines = ["## Confidence Intervals", ""]
    lines.append(
        f"{level} intervals over the runs each estimand is actually defined on "
        "(`n` below), with the method that produced them."
    )
    lines.append("")
    lines.append(
        "| Strategy | Estimand | n | Estimate | Interval | Half-width | Outcome SD | Method |"
    )
    lines.append("| --- | --- | ---: | ---: | --- | ---: | ---: | --- |")
    for strategy, estimand_id, estimate in rows:
        interval = (
            f"[{_fmt(estimate.get('lower'))}, {_fmt(estimate.get('upper'))}]"
            if estimate.get("lower") is not None
            else "undefined"
        )
        lines.append(
            f"| {strategy} | {estimand_id} | {_fmt(estimate.get('n'))} | "
            f"{_fmt(estimate.get('value'), 4)} | {interval} | "
            f"{_fmt(estimate.get('half_width'), 4)} | {_fmt(estimate.get('stdev'), 4)} | "
            f"`{estimate.get('method')}` |"
        )
    lines.append("")
    return lines


def _convergence_section(convergence: dict) -> list:
    design = convergence.get("design", {})
    checkpoints = convergence.get("checkpoints", [])
    lines = ["## Convergence", ""]
    lines.append(
        f"- Declared checkpoints: {design.get('checkpoint_schedule')} "
        f"(alpha spending: `{design.get('alpha_spending')}`)"
    )
    lines.append(f"- Checkpoints evaluated: {len(checkpoints)}")
    lines.append(f"- Stop reason: `{convergence.get('stop_reason')}`")
    if convergence.get("unmet_criteria"):
        lines.append("- Criteria still unmet at the maximum:")
        for entry in convergence["unmet_criteria"]:
            lines.append(f"  - {entry}")
    lines.append("")
    last = checkpoints[-1]
    lines.append("| Strategy | Runs | Estimate | Half-width | Change vs previous look |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for strategy, entry in sorted(last.get("strategies", {}).items()):
        estimate = entry.get("estimates", {}).get("expected_final_money")
        if not estimate:
            continue
        lines.append(
            f"| {strategy} | {_fmt(entry.get('runs'))} | {_fmt(estimate.get('estimate'))} | "
            f"{_fmt(estimate.get('half_width'), 4)} | "
            f"{_fmt(estimate.get('change_from_previous'), 4)} |"
        )
    lines.append("")
    return lines


def _comparisons_section(comparisons_doc: dict, top: int = 10) -> list:
    """The largest strategy differences, with multiplicity stated up front."""
    lines = ["## Strategy Comparisons", ""]
    pairing = comparisons_doc.get("pairing")
    correction = comparisons_doc.get("correction")
    lines.append(f"- Pairing: `{pairing}`")
    lines.append(f"- Multiple-comparison correction: `{correction}`")
    if comparisons_doc.get("baseline"):
        lines.append(f"- Baseline: `{comparisons_doc['baseline']}`")
    lines.append("")
    for estimand_id, pairs in comparisons_doc.get("estimands", {}).items():
        if not pairs:
            continue
        family_size = pairs[0].get("family_size")
        lines.append(f"### {estimand_id}")
        lines.append("")
        lines.append(
            f"{len(pairs)} comparison(s) in this family (family size {family_size}); "
            "the correction above applies across them."
        )
        lines.append("")
        lines.append("| A | B | Difference | Interval | P(A > B) | p (adjusted) |")
        lines.append("| --- | --- | ---: | --- | ---: | ---: |")
        ranked = sorted(pairs, key=lambda c: abs(c.get("difference") or 0.0), reverse=True)[:top]
        for pair in ranked:
            interval = (
                f"[{_fmt(pair.get('lower'), 3)}, {_fmt(pair.get('upper'), 3)}]"
                if pair.get("lower") is not None
                else "undefined"
            )
            lines.append(
                f"| {pair['strategy_a']} | {pair['strategy_b']} | "
                f"{_fmt(pair.get('difference'), 3)} | {interval} | "
                f"{_fmt(pair.get('win_probability'), 3)} | "
                f"{_fmt(pair.get('adjusted_p_value'), 4)} |"
            )
        if len(pairs) > top:
            lines.append("")
            lines.append(
                f"Showing the {top} largest differences of {len(pairs)}; "
                "the full set is in `comparisons.json`."
            )
        lines.append("")
    return lines
