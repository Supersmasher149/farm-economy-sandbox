"""Assembles the markdown summary report for a batch run. Written for a
game designer, not just a programmer -- plain language, one section per
strategy, warnings called out at the top.
"""


def generate_markdown_report(config: dict, num_runs: int, summary: dict, warning_list: list, crop_names: dict,
                              agent_descriptions: dict = None) -> str:
    agent_descriptions = agent_descriptions or {}
    lines = []
    lines.append("# Farm Economy Batch Report")
    lines.append("")
    lines.append(f"- Simulated days per run: **{config['days']}**")
    lines.append(f"- Runs per strategy: **{num_runs}**")
    lines.append(f"- Starting money: **{config['start_money']}**")
    lines.append(f"- Starting growing slots: **{config['start_slots']}**")
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
        lines.append(f"- Average final money: {stats['avg_final_money']}")
        lines.append(f"- Median final money: {stats['median_final_money']}")
        lines.append(f"- Min / Max final money: {stats['min_final_money']} / {stats['max_final_money']}")
        lines.append(f"- Bankruptcy rate: {stats['bankruptcy_rate']}%")
        lines.append(f"- Average day of first upgrade: {stats['avg_first_upgrade_day']}")
        lines.append(f"- Average day of second upgrade: {stats['avg_second_upgrade_day']}")
        lines.append(f"- Watering coverage: {stats['avg_watering_rate']}% of plot-days")
        lines.append(f"- Crop loss rate: {stats['avg_crop_loss_rate']}% of matured crops")
        lines.append(f"- Avg fertilizer applications per run: {stats['avg_fertilizer_applications']}")
        lines.append(f"- Avg spoiled / processed units: {stats['avg_spoiled_units']} / {stats['avg_processed_units']}")
        lines.append(f"- Avg contracts completed / failed: {stats['avg_contracts_completed']} / {stats['avg_contracts_failed']}")
        lines.append(f"- Average final reputation: {stats['avg_final_reputation']}")
        lines.append("- Average revenue by channel:")
        if stats["revenue_by_channel"]:
            for channel, revenue in sorted(stats["revenue_by_channel"].items(), key=lambda item: -item[1]):
                lines.append(f"  - {channel}: {revenue}")
        else:
            lines.append("  - No sales")
        lines.append(f"- Harvest quality mix: {stats['quality_harvested']}")
        lines.append("- Crop usage:")
        for cid, pct in sorted(stats["crop_usage_pct"].items(), key=lambda kv: -kv[1]):
            name = crop_names.get(cid, cid)
            lines.append(f"  - {name}: {pct}%")
        lines.append("")

    return "\n".join(lines)
