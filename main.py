#!/usr/bin/env python3
"""CLI entry point for the farm economy sandbox.

Examples:
    python main.py single --strategy profit_optimizer --seed 42 --verbose
    python main.py batch --runs 1000
    python main.py replay --strategy fast_seller --seed 123456789
    python main.py view --sort bankruptcy_rate --top 5
"""

import argparse
import contextlib
import json
import math
import os
import shutil
import tempfile
import textwrap
import time
import webbrowser

from agents.diversifier import Diversifier
from agents.fast_seller import FastSeller
from agents.fertilizer_maximalist import FertilizerMaximalist
from agents.neglectful_grower import NeglectfulGrower
from agents.no_upgrade_player import NoUpgradePlayer
from agents.profit_optimizer import ProfitOptimizer
from agents.progression_player import ProgressionPlayer
from agents.random_agent import RandomAgent
from agents.reckless_spender import RecklessSpender
from agents.risk_averse_grower import RiskAverseGrower
from agents.upgrade_rusher import UpgradeRusher
from experiments import sensitivity, uncertainty
from metrics import analysis_metadata, comparisons, distributions, estimands, view
from metrics.aggregate_results import BatchAggregator
from metrics.dashboard import render_dashboard_html, write_no_charts_placeholder
from metrics.economics_audit import build_economics_audit
from metrics.inference import (
    DEFAULT_BOOTSTRAP_REPLICATIONS,
    DEFAULT_CONFIDENCE,
    INFERENCE_VERSION,
    derive_analysis_seed,
)
from metrics.report import generate_markdown_report
from metrics.run_results import write_csv
from metrics.warnings import evaluate_warnings
from runner import sampling_plan as sampling_plans
from runner.adaptive import (
    AdaptiveBatch,
    AdaptiveConfig,
    StoppingRule,
    fixed_convergence_document,
    summarize_stability,
)
from runner.batch_run import resolve_base_seed, run_batch
from runner.progress import ProgressReporter, format_duration, format_rate
from runner.single_run import run_single
from simulation.configuration import validate, validate_simulation_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

AGENT_REGISTRY = {
    "fast_seller": FastSeller,
    "profit_optimizer": ProfitOptimizer,
    "progression_player": ProgressionPlayer,
    "neglectful_grower": NeglectfulGrower,
    "reckless_spender": RecklessSpender,
    "random_agent": RandomAgent,
    "no_upgrade_player": NoUpgradePlayer,
    "fertilizer_maximalist": FertilizerMaximalist,
    "diversifier": Diversifier,
    "risk_averse_grower": RiskAverseGrower,
    "upgrade_rusher": UpgradeRusher,
}


def load_json(filename):
    with open(os.path.join(CONFIG_DIR, filename)) as f:
        return json.load(f)


def load_config():
    crops = load_json("crops.json")
    upgrades = load_json("upgrades.json")
    config = load_json("simulation_settings.json")
    world = {
        "watering": load_json("watering_settings.json"),
        "fertilizer": load_json("fertilizer.json"),
        "soil": load_json("soil.json"),
        "weather": load_json("weather.json"),
        "markets": load_json("markets.json"),
        "storage": load_json("storage.json"),
        "contracts": load_json("contracts.json"),
        "buyers": load_json("buyers.json"),
        "processing": load_json("processing.json"),
    }
    validate(crops, upgrades, world)
    validate_simulation_config(config)
    return crops, upgrades, config, world


def print_player_summary(player, seed, strategy_name):
    print(f"Strategy: {strategy_name}")
    print(f"Seed: {seed}")
    print(f"Days simulated: {player.day}")
    print(f"Final money: {round(player.money, 2)}")
    print(f"Total revenue: {round(player.total_revenue, 2)}")
    print(f"Total costs: {round(player.total_expenses, 2)}")
    production_costs = sum(
        player.expenses_by_category.get(category, 0.0)
        for category in ("seeds", "watering", "fertilizer")
    )
    gross_profit = player.total_revenue - production_costs
    operating_profit = gross_profit - player.expenses_by_category.get("contract_penalties", 0.0)
    print(f"Gross profit: {round(gross_profit, 2)}")
    print(f"Operating profit: {round(operating_profit, 2)}")
    print(f"Net cash change: {round(player.total_revenue - player.total_expenses, 2)}")
    print(f"Expenses by category: {dict(sorted(player.expenses_by_category.items()))}")
    print(
        f"Crops planted / harvested / sold: {player.total_planted} / {player.total_harvested} / {player.total_sold}"
    )
    print(f"Crop plant counts: {player.crop_plant_counts}")
    print(f"Upgrades owned: {sorted(player.upgrades_owned)}")
    print(f"Upgrade purchase days: {player.upgrade_purchase_days}")
    print(f"Idle days: {player.idle_days}")
    print(f"Bankrupt: {player.bankrupt}")
    print(f"Bankruptcy day / reason: {player.bankruptcy_day} / {player.bankruptcy_reason}")
    print(
        f"Lowest / highest money: {round(player.lowest_money, 2)} / {round(player.highest_money, 2)}"
    )
    watering_rate = 100 * player.total_waterings / player.slot_days if player.slot_days else 0.0
    occupied_watering_rate = (
        100 * player.total_waterings / player.occupied_slot_days
        if player.occupied_slot_days
        else 0.0
    )
    loss_rate = (
        100 * player.total_crops_lost / player.total_harvest_events
        if player.total_harvest_events
        else 0.0
    )
    print(
        f"Watering coverage: {round(watering_rate, 1)}% of plot-days ({player.total_waterings}/{player.slot_days})"
    )
    print(
        f"Watering coverage of occupied plot-days: {round(occupied_watering_rate, 1)}% ({player.total_waterings}/{player.occupied_slot_days})"
    )
    print(f"Crops lost: {player.total_crops_lost} ({round(loss_rate, 1)}% of matured crops)")
    print(
        f"Fertilizer bought / applied: {player.total_fertilizer_bought} / {player.total_fertilizer_applied}"
    )
    print(f"Quality harvested: {player.quality_harvested}")
    print(f"Revenue by channel: {dict(sorted(player.revenue_by_channel.items()))}")
    print(f"Spoiled / processed: {player.total_spoiled} / {player.total_processed}")
    print(f"Contracts completed / failed: {player.contracts_completed} / {player.contracts_failed}")
    print(f"Reputation: {round(player.reputation, 2)}")


def cmd_single(args):
    crops, upgrades, config, world = load_config()
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agent = AGENT_REGISTRY[args.strategy]()
    player, seed, history = run_single(
        config,
        agent,
        crops,
        upgrades,
        watering_settings,
        fertilizer_config,
        seed=args.seed,
        record_history=args.verbose,
        world=world,
    )
    print_player_summary(player, seed, agent.name)
    if args.verbose and history:
        print("\nDaily history:")
        for day_record in history:
            print(day_record)


def cmd_replay(args):
    crops, upgrades, config, world = load_config()
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agent = AGENT_REGISTRY[args.strategy]()
    player, seed, _ = run_single(
        config,
        agent,
        crops,
        upgrades,
        watering_settings,
        fertilizer_config,
        seed=args.seed,
        record_history=False,
        world=world,
    )
    print_player_summary(player, seed, agent.name)


UNCERTAINTY_DIRNAME = "uncertainty"


def _config_bundle(crops, upgrades, config, world) -> dict:
    """The whole configuration as one addressable document.

    Uncertainty paths (`crops[id=quickweed].loss_chance`,
    `simulation_settings.start_money`, `world.weather...`) are resolved against
    this shape, so a spec can reach any tunable number without the sampler
    knowing which JSON file it came from.
    """
    return {
        "crops": crops,
        "upgrades": upgrades,
        "simulation_settings": config,
        "world": world,
    }


def _validate_bundle(bundle: dict) -> None:
    """Validate a sampled configuration with the real validators.

    The same two calls `load_config()` makes. A sampled value that the runtime
    would reject must fail here, not three thousand simulated days later.
    """
    validate(bundle["crops"], bundle["upgrades"], bundle["world"])
    validate_simulation_config(bundle["simulation_settings"])


def cmd_uncertainty(args):
    """Nested epistemic study: sample configurations, re-run, decompose."""
    spec = uncertainty.load_spec(args.spec)
    crops, upgrades, config, world = load_config()
    config = dict(config)
    if args.days is not None:
        config["days"] = args.days
    bundle = _config_bundle(crops, upgrades, config, world)

    for parameter in spec.parameters:
        try:
            uncertainty.read_path(bundle, parameter.path)
        except uncertainty.UncertaintySpecError as exc:
            raise SystemExit(f"Specification does not match this config: {exc}") from None

    if args.strategy:
        unknown = [name for name in args.strategy.split(",") if name not in AGENT_REGISTRY]
        if unknown:
            raise SystemExit(f"Unknown strategy/strategies: {unknown}")
        agent_names = args.strategy.split(",")
    else:
        agent_names = list(AGENT_REGISTRY)

    scenarios = None
    if args.method == "scenarios":
        if not args.scenarios:
            raise SystemExit("--method scenarios needs --scenarios PATH (a JSON object of corners)")
        with open(args.scenarios) as handle:
            scenarios = json.load(handle)

    try:
        design, design_metadata = _build_design(args, spec, scenarios)
    except uncertainty.UncertaintySpecError as exc:
        raise SystemExit(str(exc)) from None

    base_seed = resolve_base_seed(args.seed)
    total_simulations = len(design) * args.replicates * len(agent_names)
    print(
        f"Design: {design_metadata['design']} -- {len(design)} configuration sample(s) "
        f"x {args.replicates} replicate(s) x {len(agent_names)} strateg(ies) "
        f"= {total_simulations} simulations."
    )
    if not design_metadata.get("honours_correlation_groups", True) and spec.correlation_groups:
        print(
            "  note: this design ignores declared correlation groups (see the error it "
            "would have raised); results assume independent inputs."
        )

    def simulate(sampled_bundle, sample_id):
        agents = [AGENT_REGISTRY[name]() for name in agent_names]
        # A per-sample seed derived from the study seed, so every configuration
        # sample gets its own aleatory replicates *and* the whole study
        # reproduces from one number.
        sample_seed = derive_analysis_seed(base_seed, "config-sample", sample_id) % (2**32)
        sampled_world = sampled_bundle["world"]
        return run_batch(
            sampled_bundle["simulation_settings"],
            agents,
            sampled_bundle["crops"],
            sampled_bundle["upgrades"],
            sampled_world["watering"],
            sampled_world["fertilizer"],
            num_runs=args.replicates,
            base_seed=sample_seed,
            world=sampled_world,
            workers=args.workers,
            sampling_plan=sampling_plans.IndependentHashedV1(sample_seed),
        )

    estimand_ids = (
        args.estimand.split(",")
        if args.estimand
        else ["expected_final_money", "bankruptcy_probability"]
    )
    for estimand_id in estimand_ids:
        estimands.get(estimand_id)

    study = uncertainty.run_study(
        bundle,
        spec,
        simulate,
        design,
        estimand_ids=estimand_ids,
        validator=_validate_bundle,
        analysis_seed=base_seed,
        confidence=args.confidence,
    )
    study["design"] = design_metadata
    study["base_seed"] = base_seed
    study["strategies"] = agent_names

    analysis_strategy = args.report_strategy or agent_names[0]
    study["sensitivity"] = {
        estimand_id: sensitivity.analyze(study, design_metadata, estimand_id, analysis_strategy)
        for estimand_id in estimand_ids
    }

    out_dir = args.out or os.path.join(
        REPORTS_DIR,
        UNCERTAINTY_DIRNAME,
        f"{time.strftime('%Y%m%dT%H%M%S')}-{design_metadata['design']}",
    )
    os.makedirs(out_dir, exist_ok=True)
    _write_json(os.path.join(out_dir, "study.json"), study)
    print()
    print(
        f"Configuration samples: {study['valid_samples']} valid, "
        f"{study['rejected_samples']} rejected "
        f"(policy: {spec.on_invalid})"
    )
    print()
    print(_render_uncertainty(study, estimand_ids, analysis_strategy))
    print()
    print(f"Wrote {os.path.join(out_dir, 'study.json')}")


def _build_design(args, spec, scenarios):
    if args.method == "oat":
        return sensitivity.one_at_a_time_design(spec, low=args.low, high=args.high)
    if args.method == "scenarios":
        return sensitivity.scenario_design(spec, scenarios)
    if args.method == "lhs":
        return sensitivity.latin_hypercube_design(spec, args.samples, seed=args.seed or 0)
    if args.method == "morris":
        return sensitivity.morris_design(
            spec, trajectories=args.trajectories, levels=args.levels, seed=args.seed or 0
        )
    if args.method == "sobol":
        return sensitivity.sobol_design(spec, base_samples=args.samples, seed=args.seed or 0)
    return sensitivity.monte_carlo_design(spec, args.samples, seed=args.seed or 0)


def _render_uncertainty(study: dict, estimand_ids, strategy: str) -> str:
    """Terminal read-out: variance split first, then the sensitivity ranking.

    The split comes first deliberately -- it says whether the sensitivity
    ranking below is even the interesting part, or whether the outcome is
    dominated by simulation noise that more runs would fix.
    """
    lines = [f"Strategy analysed: {strategy}", ""]
    for entry in study.get("variance_decomposition", []):
        if entry.get("strategy") != strategy:
            continue
        share = entry.get("epistemic_share")
        lines.append(
            f"[{entry['estimand']}] epistemic variance "
            f"{_fmt_opt(entry.get('epistemic_variance'))} | aleatory "
            f"{_fmt_opt(entry.get('aleatory_variance'))} | epistemic share "
            + ("—" if share is None else f"{share:.1%}")
        )
    for estimand_id in estimand_ids:
        analysis = study.get("sensitivity", {}).get(estimand_id) or {}
        lines.append("")
        lines.append(f"== {estimand_id} ({analysis.get('method')}) ==")
        if analysis.get("effects"):
            lines.append(f"{'parameter':<32}{'low':>14}{'base':>14}{'high':>14}{'swing':>14}")
            for row in analysis["effects"]:
                lines.append(
                    f"{row['parameter']:<32}{_fmt_opt(row['low']):>14}{_fmt_opt(row['base']):>14}"
                    f"{_fmt_opt(row['high']):>14}{_fmt_opt(row['swing']):>14}"
                )
        elif analysis.get("indices"):
            keys = [
                k
                for k in ("mu_star", "sigma", "first_order", "total_effect")
                if k in analysis["indices"][0]
            ]
            header = f"{'parameter':<32}" + "".join(f"{k:>16}" for k in keys)
            lines.append(header)
            for row in analysis["indices"]:
                lines.append(
                    f"{row['parameter']:<32}" + "".join(f"{_fmt_opt(row[k]):>16}" for k in keys)
                )
        else:
            lines.append(
                f"response mean {_fmt_opt(analysis.get('response_mean'))}, "
                f"sd {_fmt_opt(analysis.get('response_stdev'))}, "
                f"range [{_fmt_opt(analysis.get('response_min'))}, "
                f"{_fmt_opt(analysis.get('response_max'))}]"
            )
            if analysis.get("scenarios"):
                for name, value in analysis["scenarios"].items():
                    lines.append(f"  {name:<30} {_fmt_opt(value)}")
        if analysis.get("note"):
            lines.append(f"  note: {analysis['note']}")
        if analysis.get("caveat"):
            lines.append(f"  caveat: {analysis['caveat']}")
    return "\n".join(lines)


def _fmt_opt(value, ndigits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{round(value, ndigits):,}"
    return str(value)


def cmd_analyze(args):
    """Exact distribution analysis and strategy comparisons over a batch's raw runs.

    Reads a CSV rather than a summary: everything here is computed from the
    per-run observations, which is what makes the quantiles exact and what
    lets `--csv` point at a `farm-c` batch. The C port stays a deterministic
    raw-data producer; the inference layer is this one, for both.
    """
    if args.csv:
        csv_path = args.csv
        run_dir = None
        base_seed = args.analysis_seed
        horizon = args.days
    else:
        run_dir = view.resolve_run_dir(REPORTS_DIR, args.run)
        csv_path = os.path.join(run_dir, "run_results.csv")
        summary_doc = view.load_run(run_dir)
        base_seed = (
            args.analysis_seed if args.analysis_seed is not None else summary_doc.get("base_seed")
        )
        horizon = args.days if args.days is not None else summary_doc.get("days")
    if not os.path.exists(csv_path):
        raise SystemExit(f"No run_results.csv at {csv_path}")

    probabilities = tuple(float(p) for p in args.quantiles.split(",")) if args.quantiles else None
    distributions_doc = distributions.analyze_csv(
        csv_path,
        horizon_days=horizon,
        probabilities=probabilities or distributions.DEFAULT_QUANTILE_PROBABILITIES,
        tail_thresholds=tuple(float(t) for t in args.tail.split(",")) if args.tail else (0.0,),
        confidence=args.confidence,
        replications=args.bootstrap_replications,
        base_seed=base_seed,
    )

    comparisons_doc = None
    if args.compare != "none":
        observations = distributions.load_observations(csv_path)
        comparisons_doc = comparisons.compare_all_pairs(
            observations,
            estimand_ids=args.estimand.split(",") if args.estimand else None,
            pairing=(
                comparisons.PAIRING_PAIRED
                if args.compare == "paired"
                else comparisons.PAIRING_INDEPENDENT
            ),
            confidence=args.confidence,
            correction=args.correction,
            replications=args.bootstrap_replications,
            base_seed=base_seed,
            baseline=args.baseline,
        )

    print(f"Source: {csv_path}")
    print()
    print(view.render_distributions(distributions_doc, cohort=args.cohort))
    if comparisons_doc:
        print()
        print(view.render_comparisons(comparisons_doc, top=args.top or 10))

    if args.json:
        document = {
            "source_csv": csv_path,
            "run_dir": run_dir,
            "distributions": distributions_doc,
            "comparisons": comparisons_doc,
        }
        _write_json(args.json, document)
        print()
        print(f"Wrote {args.json}")


ADAPTIVE_FLAGS = (
    "min_runs",
    "max_runs",
    "checkpoint_runs",
    "target_half_width",
    "target_relative_half_width",
    "bankruptcy_half_width",
    "min_bankruptcies",
    "min_survivals",
)


def _adaptive_requested(args) -> bool:
    return any(getattr(args, flag, None) is not None for flag in ADAPTIVE_FLAGS)


def _build_adaptive_config(args):
    """Turn the adaptive CLI flags into a declared sampling design, or None.

    Adaptive mode is opt-in and all-or-nothing: passing any precision flag
    without a stopping rule is rejected rather than silently sampling to
    --max-runs, because "it ran 20,000 times" and "it ran until the interval
    was narrow" are different experiments and the artifacts must not claim the
    second when the first happened.
    """
    if not _adaptive_requested(args):
        return None

    rules = []
    stop_estimands = args.stop_estimand or (
        ["expected_final_money"]
        if (args.target_half_width is not None or args.target_relative_half_width is not None)
        else []
    )
    for estimand_id in stop_estimands:
        try:
            estimands.get(estimand_id)
        except estimands.UnknownEstimand as exc:
            raise SystemExit(str(exc)) from None
        rules.append(
            StoppingRule(
                estimand=estimand_id,
                target_half_width=args.target_half_width,
                target_relative_half_width=args.target_relative_half_width,
            )
        )
    if args.bankruptcy_half_width is not None:
        rules.append(
            StoppingRule(
                estimand="bankruptcy_probability",
                target_half_width=args.bankruptcy_half_width,
            )
        )
    if not rules:
        raise SystemExit(
            "Adaptive sampling needs at least one precision target: pass "
            "--target-half-width (optionally with --stop-estimand), "
            "--target-relative-half-width, or --bankruptcy-half-width."
        )

    min_runs = args.min_runs if args.min_runs is not None else min(500, args.runs)
    max_runs = args.max_runs if args.max_runs is not None else max(min_runs, args.runs)
    checkpoint_runs = (
        args.checkpoint_runs
        if args.checkpoint_runs is not None
        else max(1, (max_runs - min_runs) // 8 or min_runs)
    )
    try:
        return AdaptiveConfig(
            min_runs=min_runs,
            max_runs=max_runs,
            checkpoint_runs=checkpoint_runs,
            rules=rules,
            confidence=args.confidence,
            mode=args.stopping_mode,
            min_bankruptcies=args.min_bankruptcies or 0,
            min_survivals=args.min_survivals or 0,
            alpha_spending=args.alpha_spending,
            track_quantiles=args.track_quantiles,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid adaptive design: {exc}") from None


def _resolve_sampling_plan(args, base_seed: int, num_runs: int, adaptive: bool):
    requested = args.sampling_plan or ("independent" if adaptive else "legacy")
    plan_id = sampling_plans.PLAN_ALIASES.get(requested)
    if plan_id is None:
        raise SystemExit(
            f"Unknown --sampling-plan {requested!r}; expected legacy, independent or paired."
        )
    if adaptive and plan_id == sampling_plans.LEGACY_PLAN:
        raise SystemExit(
            "legacy-mt19937-v1 mints a whole batch from one sequential stream, so its "
            "seeds depend on the total run count -- which adaptive sampling does not know "
            "up front. Use --sampling-plan independent (or paired)."
        )
    return sampling_plans.resolve(requested, base_seed, num_runs)


def _write_json(path: str, document) -> None:
    with open(path, "w") as f:
        json.dump(document, f, indent=2)


def _skipped_document(reason: str) -> dict:
    return {"skipped": True, "reason": reason}


def _with_defaults(args, command: str):
    """Fill any option the caller did not set with the parser's own default.

    `cmd_batch` is called directly -- by tests and by tools -- with a
    hand-built args object, and that call site should not have to grow a new
    attribute every time a flag is added. Defaults are read from the real
    parser rather than duplicated here, so the two can never drift.
    """
    defaults = build_parser().parse_args([command])
    for name, value in vars(args).items():
        setattr(defaults, name, value)
    return defaults


def cmd_batch(args):
    args = _with_defaults(args, "batch")
    started_at = time.time()
    crops, upgrades, config, world = load_config()
    config = dict(config)
    if args.days is not None:
        config["days"] = args.days
    if args.start_money is not None:
        config["start_money"] = args.start_money
    validate_simulation_config(config)
    base_seed = resolve_base_seed(args.seed)
    analysis_seed = args.analysis_seed if args.analysis_seed is not None else base_seed
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agents = [cls() for cls in AGENT_REGISTRY.values()]

    adaptive_config = _build_adaptive_config(args)
    adaptive = adaptive_config is not None
    requested_runs = adaptive_config.max_runs if adaptive else args.runs
    plan = _resolve_sampling_plan(args, base_seed, args.runs, adaptive)
    total_runs = requested_runs * len(agents)

    # One aggregator for the whole batch, descriptive and inferential alike:
    # summary.json, the markdown report, the terminal view and any stopping
    # rule all read the same accumulators, so none of them can disagree about
    # a number (or about the interval around it).
    aggregator = BatchAggregator(confidence=args.confidence)
    driver = None

    if adaptive:

        def run_block(block_agents, start_replicate, count):
            return run_batch(
                config,
                block_agents,
                crops,
                upgrades,
                watering_settings,
                fertilizer_config,
                num_runs=count,
                base_seed=base_seed,
                world=world,
                workers=args.workers,
                sampling_plan=plan,
                start_replicate=start_replicate,
            )

        driver = AdaptiveBatch(
            adaptive_config,
            aggregator,
            run_block,
            agents,
            plan,
            estimand_ids=sorted(
                {rule.estimand for rule in adaptive_config.rules}
                | {"expected_final_money", "bankruptcy_probability"}
            ),
        )
        # AdaptiveBatch feeds the aggregator itself as it streams, so this
        # path must not tee again -- doing so would double-count every run.
        results = driver.stream()
    else:
        raw_results = run_batch(
            config,
            agents,
            crops,
            upgrades,
            watering_settings,
            fertilizer_config,
            num_runs=args.runs,
            base_seed=base_seed,
            world=world,
            workers=args.workers,
            sampling_plan=plan,
        )

        def _tee(stream):
            for r in stream:
                aggregator.add(r)
                yield r

        results = _tee(raw_results)

    # Progress is a pass-through over the result stream (stderr only), so it
    # cannot affect what a given seed produces.
    progress = ProgressReporter(total_runs, enabled=args.progress)
    results = progress.track(results)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    crop_ids = [c["id"] for c in crops]
    crop_names = {c["id"]: c["name"] for c in crops}
    agent_descriptions = {agent.name: agent.description for agent in agents}
    economics_audit = build_economics_audit(crops, fertilizer_config, world["markets"])

    _sweep_stale_staging(REPORTS_DIR)
    # mkdtemp rather than TemporaryDirectory: publication *renames* this
    # directory into place, so its cleanup is conditional on having failed
    # before that point rather than unconditional.
    staging_dir = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=REPORTS_DIR)
    try:
        # run_batch streams one RunResult at a time rather than returning a
        # materialized list, so CSV output and aggregation stay in one pass.
        staged_csv_path = os.path.join(staging_dir, "run_results.csv")
        write_csv(results, staged_csv_path, crop_ids)

        summary = aggregator.finalize()
        warning_list = evaluate_warnings(summary, config)
        realized_runs = driver.realized_runs if adaptive else args.runs
        stop_reason = driver.stop_reason if adaptive else "fixed_sample"

        snapshot = {
            "base_seed": base_seed,
            "crops": crops,
            "upgrades": upgrades,
            "simulation_settings": config,
            "watering_settings": watering_settings,
            "fertilizer": fertilizer_config,
            "world": world,
            "num_runs": realized_runs,
            "sampling_plan": plan.describe(),
        }
        staged_snapshot_path = os.path.join(staging_dir, "config_snapshot.json")
        _write_json(staged_snapshot_path, snapshot)

        # Machine-readable counterpart to summary_report.md -- exactly the
        # aggregator's own dict, so `main.py view` and any other consumer
        # never re-derive a number the report already computed.
        summary_doc = {
            "base_seed": base_seed,
            "num_runs": realized_runs,
            "requested_runs": requested_runs,
            "days": config["days"],
            "start_money": config["start_money"],
            "warnings": warning_list,
            "inference_version": INFERENCE_VERSION,
            "estimand_registry_version": estimands.ESTIMAND_REGISTRY_VERSION,
            "confidence": args.confidence,
            "sampling_plan": plan.describe(),
            "stop_reason": stop_reason,
            # Estimand metadata travels with the numbers: a reader of
            # summary.json can see the population and missing-value policy
            # behind every interval without opening the source.
            "estimands": estimands.metadata_document(estimands.DEFAULT_ESTIMANDS),
            "strategies": summary,
        }
        staged_summary_json_path = os.path.join(staging_dir, "summary.json")
        _write_json(staged_summary_json_path, summary_doc)

        convergence_doc = (
            driver.convergence_document()
            if adaptive
            else fixed_convergence_document(
                aggregator,
                args.confidence,
                ["expected_final_money", "bankruptcy_probability", "expected_profit_per_day"],
                realized_runs,
                plan.describe(),
            )
        )
        staged_convergence_path = os.path.join(staging_dir, "convergence.json")
        _write_json(staged_convergence_path, convergence_doc)

        # Exact distribution analysis reads the staged CSV, not the streaming
        # reservoirs -- the two-tier storage policy in metrics/distributions.py.
        staged_distributions_path = os.path.join(staging_dir, "distributions.json")
        staged_comparisons_path = os.path.join(staging_dir, "comparisons.json")
        distributions_doc = _skipped_document("--no-distributions")
        comparisons_doc = _skipped_document("--no-comparisons")
        if args.distributions:
            distributions_doc = distributions.analyze_csv(
                staged_csv_path,
                horizon_days=config["days"],
                confidence=args.confidence,
                replications=args.bootstrap_replications,
                base_seed=analysis_seed,
            )
            distributions_doc["source_csv"] = "run_results.csv"
        if args.comparisons:
            observations = distributions.load_observations(staged_csv_path)
            comparisons_doc = comparisons.compare_all_pairs(
                observations,
                pairing=(
                    comparisons.PAIRING_PAIRED if plan.paired else comparisons.PAIRING_INDEPENDENT
                ),
                confidence=args.confidence,
                correction=args.correction,
                replications=args.bootstrap_replications,
                base_seed=analysis_seed,
                baseline=args.baseline,
            )
        _write_json(staged_distributions_path, distributions_doc)
        _write_json(staged_comparisons_path, comparisons_doc)

        staged_dashboard_path = os.path.join(staging_dir, "dashboard.html")
        if args.charts:
            render_dashboard_html(
                staged_csv_path,
                staged_dashboard_path,
                title="Farm Economy Batch Report",
                subtitle=(
                    f"{realized_runs} runs x {len(agents)} strategies, "
                    f"{config['days']} days, base seed {base_seed}"
                ),
                convergence_path=staged_convergence_path,
                distributions_path=staged_distributions_path,
            )
        else:
            write_no_charts_placeholder(staged_dashboard_path)

        report_text = generate_markdown_report(
            config,
            realized_runs,
            summary,
            warning_list,
            crop_names,
            agent_descriptions,
            economics_audit,
            base_seed=base_seed,
            confidence=args.confidence,
            sampling_plan=plan.describe(),
            stop_reason=stop_reason,
            convergence=convergence_doc,
            comparisons_doc=None if comparisons_doc.get("skipped") else comparisons_doc,
        )
        staged_report_path = os.path.join(staging_dir, "summary_report.md")
        with open(staged_report_path, "w") as f:
            f.write(report_text)

        metadata_doc = analysis_metadata.build(
            base_seed=base_seed,
            sampling_plan=plan.describe(),
            requested_runs=requested_runs,
            realized_runs=realized_runs,
            strategies=[agent.name for agent in agents],
            config=config,
            confidence=args.confidence,
            analysis_seed=analysis_seed,
            bootstrap_replications=args.bootstrap_replications,
            correction_method=args.correction if args.comparisons else None,
            stopping=(
                adaptive_config.describe() if adaptive else {"mode": "fixed", "runs": args.runs}
            ),
            stop_reason=stop_reason,
            unmet_criteria=driver.unmet_criteria if adaptive else [],
            started_at=started_at,
            repo_dir=BASE_DIR,
            artifacts=list(ARTIFACT_NAMES),
            accumulator_state={
                strategy: accumulator.snapshot()
                for strategy, accumulator in aggregator.inference_accumulators().items()
            },
        )
        _write_json(os.path.join(staging_dir, "analysis_metadata.json"), metadata_doc)

        run_dir = _publish_report_artifacts(staging_dir, REPORTS_DIR)
    finally:
        # Publication consumed the staging directory by renaming it, so this
        # only removes it when the batch failed before getting that far.
        shutil.rmtree(staging_dir, ignore_errors=True)

    print(
        f"Ran {realized_runs} simulations x {len(agents)} strategies = "
        f"{realized_runs * len(agents)} total runs."
    )
    if adaptive:
        stability = summarize_stability(convergence_doc)
        print(
            f"Sampling:  adaptive ({plan.plan_id}), stopped at "
            f"{realized_runs}/{adaptive_config.max_runs} runs per strategy -- {stop_reason}"
        )
        print(
            f"Looks:     {stability['checkpoints']} checkpoint(s) of "
            f"{len(adaptive_config.checkpoint_schedule())} declared"
        )
        for entry in driver.unmet_criteria:
            print(f"  unmet:   {entry}")
    else:
        print(f"Sampling:  fixed {args.runs} runs per strategy ({plan.plan_id})")
    print(
        f"Time:      {format_duration(progress.elapsed)} elapsed ({format_rate(progress.rate)} sim/s)"
    )
    for name in ARTIFACT_NAMES:
        print(f"  {name:<22} {os.path.join(REPORTS_DIR, name)}")
    print(f"Run:       {run_dir}")
    print()
    print(report_text)


def cmd_view(args):
    if args.list:
        run_ids = view.list_runs(REPORTS_DIR)
        if not run_ids:
            raise SystemExit(
                f"No published runs found in {REPORTS_DIR}. Run `python3 main.py batch` first."
            )
        latest_link = os.path.join(REPORTS_DIR, "latest")
        latest_target = (
            os.path.basename(os.readlink(latest_link)) if os.path.islink(latest_link) else None
        )
        for i, run_id in enumerate(reversed(run_ids)):
            marker = "  (latest)" if run_id == latest_target else ""
            print(f"latest-{i}\t{run_id}{marker}")
        return

    fields = args.fields.split(",") if args.fields and args.fields != "all" else None

    if args.diff:
        ref_a, ref_b = args.diff
        doc_a = view.load_run(view.resolve_run_dir(REPORTS_DIR, ref_a))
        doc_b = view.load_run(view.resolve_run_dir(REPORTS_DIR, ref_b))
        diff_fields = fields
        if args.fields == "all":
            diff_fields = sorted(
                set(view.scalar_fields(doc_a["strategies"]))
                & set(view.scalar_fields(doc_b["strategies"]))
            )
        print(
            view.render_diff(
                ref_a,
                doc_a["strategies"],
                ref_b,
                doc_b["strategies"],
                diff_fields,
                args.only_changed,
            )
        )
        return

    run_dir = view.resolve_run_dir(REPORTS_DIR, args.run)
    doc = view.load_run(run_dir)

    if args.open:
        webbrowser.open("file://" + os.path.abspath(os.path.join(run_dir, "dashboard.html")))

    if args.fields == "all":
        fields = view.scalar_fields(doc["strategies"])
    strategy_filter = args.strategy.split(",") if args.strategy else None

    print(f"Run: {run_dir}")
    print(
        f"{doc['num_runs']} runs x {len(doc['strategies'])} strategies, "
        f"{doc['days']} days, base seed {doc['base_seed']}"
    )
    plan = doc.get("sampling_plan") or {}
    if plan:
        stop = doc.get("stop_reason", "fixed_sample")
        print(f"sampling plan: {plan.get('plan')} | stop reason: {stop}")
    print()
    print(view.render_warnings(doc["warnings"]))
    print()

    if args.intervals:
        print(
            view.render_intervals(
                doc["strategies"], args.estimand.split(",") if args.estimand else None
            )
        )
        print()
    if args.convergence:
        print(view.render_convergence(view.load_artifact(run_dir, "convergence.json")))
        print()
    print(
        view.render_table(
            doc["strategies"],
            fields,
            sort_by=args.sort,
            ascending=args.asc,
            top=args.top,
            strategy_filter=strategy_filter,
        )
    )


ARTIFACT_NAMES = (
    "run_results.csv",
    "config_snapshot.json",
    "summary_report.md",
    "summary.json",
    "dashboard.html",
    # Written on every batch, adaptive or not, so the published set is always
    # the same shape: a fixed-sample batch's convergence document is a
    # single terminal checkpoint, and a skipped analysis writes a document
    # that says it was skipped rather than leaving a dangling symlink.
    "convergence.json",
    "distributions.json",
    "comparisons.json",
    "analysis_metadata.json",
)
STAGING_PREFIX = ".batch-"
# Age past which a leftover staging directory is assumed to be from an
# interrupted run rather than a batch still writing into it. Generous
# because a multi-million-run batch legitimately takes hours, and deleting a
# live batch's staging directory would be far worse than leaving one behind.
STALE_STAGING_SECONDS = 24 * 60 * 60
RUNS_DIRNAME = "runs"
LATEST_LINK = "latest"
PUBLISH_LOCK = ".publish.lock"
# How many published run directories to keep. They are immutable, so old ones
# stay readable (and diffable) until this sweep removes them.
RUNS_RETAINED = 5


class _PublishLock:
    """Inter-process exclusive lock around report publication.

    Three separate `os.replace` calls could interleave between concurrent
    batch processes, so a reader could pair a CSV from one run with a summary
    from another, and one process's rollback could clobber another's freshly
    published file. Publication now swaps a single pointer, and this
    serializes even that against a second batch running at the same time.

    Degrades to a no-op where `fcntl` is unavailable rather than failing the
    run: the pointer swap is still atomic on its own, the lock only adds
    mutual exclusion for the surrounding directory bookkeeping.
    """

    def __init__(self, path: str):
        self.path = path
        self._handle = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self
        self._handle = open(self.path, "w")
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return False


def _replace_symlink(target: str, link_path: str) -> None:
    """Point `link_path` at `target`, atomically replacing whatever is there.

    A symlink cannot be retargeted in place, so this creates a uniquely named
    one beside it and renames over the old one -- `os.replace` on a symlink
    swaps the link itself, so no reader ever observes the link missing.
    """
    temporary = f"{link_path}.{os.getpid()}.tmp"
    if os.path.lexists(temporary):
        os.unlink(temporary)
    os.symlink(target, temporary)
    os.replace(temporary, link_path)


def _sweep_stale_staging(reports_dir: str, max_age: int = STALE_STAGING_SECONDS) -> None:
    """Remove staging directories abandoned by interrupted runs.

    A batch killed mid-write leaves a `reports/.batch-*/` directory holding a
    partial (and potentially very large) CSV. Only directories older than
    `max_age` are touched, so a batch currently writing into one -- including
    another process's -- is never disturbed.
    """
    now = time.time()
    try:
        entries = os.listdir(reports_dir)
    except FileNotFoundError:
        return
    for name in entries:
        if not name.startswith(STAGING_PREFIX):
            continue
        path = os.path.join(reports_dir, name)
        try:
            if os.path.isdir(path) and now - os.stat(path).st_mtime > max_age:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _sweep_old_runs(runs_dir: str, keep: int, current: str) -> None:
    try:
        entries = sorted(os.listdir(runs_dir))
    except FileNotFoundError:
        return
    for name in entries[: max(0, len(entries) - keep)]:
        if name == current:
            continue
        shutil.rmtree(os.path.join(runs_dir, name), ignore_errors=True)


def _publish_report_artifacts(staging_dir: str, reports_dir: str) -> str:
    """Publish one batch's artifacts as an immutable set, atomically.

    The staging directory is renamed into `reports/runs/<id>/` (a single
    atomic rename), then `reports/latest` is repointed at it (a single atomic
    symlink swap). The three familiar `reports/<name>` paths are stable
    symlinks through `latest`, so that one swap moves all of them together --
    replacing the previous three independent `os.replace` calls, which let a
    reader pair artifacts from different batches.

    Returns the run directory that was published. Nothing mutates a published
    run directory afterwards, so a reader that resolves `reports/latest` once
    holds a consistent snapshot even while a later batch publishes.
    """
    runs_dir = os.path.join(reports_dir, RUNS_DIRNAME)
    run_id = os.path.basename(staging_dir).lstrip(".").replace("batch-", "", 1)
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{run_id}"

    with _PublishLock(os.path.join(reports_dir, PUBLISH_LOCK)):
        os.makedirs(runs_dir, exist_ok=True)
        run_dir = os.path.join(runs_dir, run_id)
        # Atomic, and it consumes the staging directory: everything below
        # either succeeds or leaves the previous `latest` untouched.
        os.replace(staging_dir, run_dir)
        # mkdtemp creates 0700; published artifacts used to sit in reports/
        # itself and be world-readable, so widen the directory back out
        # rather than silently making reports/ private to its owner.
        with contextlib.suppress(OSError):
            os.chmod(run_dir, 0o755)
        latest_link_path = os.path.join(reports_dir, LATEST_LINK)
        # Captured before the swap below so a failure partway through -- even
        # after `latest` itself has already moved -- can put it back rather
        # than leave it dangling at a run_dir this function is about to
        # delete. None means nothing was published before this attempt.
        previous_latest_target = (
            os.readlink(latest_link_path) if os.path.islink(latest_link_path) else None
        )
        try:
            _replace_symlink(os.path.join(RUNS_DIRNAME, run_id), latest_link_path)
            for name in ARTIFACT_NAMES:
                link_path = os.path.join(reports_dir, name)
                target = os.path.join(LATEST_LINK, name)
                # Recreate only when it is not already the stable pointer --
                # e.g. upgrading a tree that still has real files here.
                if not os.path.islink(link_path) or os.readlink(link_path) != target:
                    _replace_symlink(target, link_path)
        except Exception:
            # `latest` may already point at run_dir (the swap above can
            # succeed even if a later ARTIFACT_NAMES swap fails) -- restore
            # it to whatever it pointed at before this attempt so it never
            # dangles at the run_dir being deleted below. The ARTIFACT_NAMES
            # links all indirect through `latest` rather than encoding a run
            # id, so restoring this one pointer is enough to make them
            # resolve correctly again too.
            with contextlib.suppress(OSError):
                if previous_latest_target is None:
                    os.unlink(latest_link_path)
                else:
                    _replace_symlink(previous_latest_target, latest_link_path)
            shutil.rmtree(run_dir, ignore_errors=True)
            raise
        _sweep_old_runs(runs_dir, RUNS_RETAINED, run_id)
    return run_dir


def strategy_roster(indent: str = "  ") -> str:
    """Render the agent registry as a help-text block.

    Built from each agent's own `description`, so `--help` can never drift
    from what the strategies actually do -- adding an agent to
    AGENT_REGISTRY is enough to document it here.
    """
    width = max(len(name) for name in AGENT_REGISTRY)
    lines = []
    for name, agent_cls in AGENT_REGISTRY.items():
        body = textwrap.wrap(agent_cls.description, width=76 - width - len(indent))
        lines.append(f"{indent}{name.ljust(width)}  {body[0] if body else ''}")
        lines.extend(f"{indent}{' ' * width}  {line}" for line in body[1:])
    return "\n".join(lines)


DESCRIPTION = """\
Headless, deterministic farm-economy simulator for balance testing.

This is not a playable game. Scripted agents each probe one deliberate
strategy, and a batch runner plays every strategy thousands of times to
surface how the economy behaves in aggregate -- dominant crops, exploitable
upgrades, bankruptcy traps.

Every run is seeded and exactly reproducible: `single` prints the seed it
used, and `replay` reproduces that run day for day. All tunable game data
lives in config/*.json; rebalancing means editing those, not the code.
"""

EPILOG = """\
parallelism:
  `batch` runs across all CPU cores by default. Control it with --workers N
  (process-based; 1 forces sequential). Results are identical for a given
  --seed at any worker count, because per-run seeds are minted single-
  threaded before any work is dispatched. `single` and `replay` are always
  one process.

progress:
  A long `batch` prints a live status line to stderr -- bar, percent, runs
  done vs total, sim/s, elapsed, and ETA -- whenever stderr is a terminal.
  Force it with --progress or suppress it with --no-progress; stdout (the
  report) is identical either way.

examples:
  # Play one strategy once and print a full day-by-day history
  python3 main.py single --strategy profit_optimizer --seed 42 --verbose

  # Reproduce a specific recorded run exactly
  python3 main.py replay --strategy fast_seller --seed 123456789

  # Run every strategy 1000 times and write reports/
  python3 main.py batch --runs 1000

  # A/B a config change under identical conditions (same seed both times)
  python3 main.py batch --runs 1000 --seed 12345

  # Short, richer diagnostic scenario without editing config files
  python3 main.py batch --runs 100 --days 30 --start-money 300

  # Force sequential execution (same results, easier to profile or debug)
  python3 main.py batch --runs 1000 --workers 1

Run `python3 main.py <command> --help` for per-command detail.
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        title="commands",
        metavar="{single,replay,batch,view,analyze,uncertainty}",
    )

    single = subparsers.add_parser(
        "single",
        help="Run one strategy once and print a result summary.",
        description=(
            "Run one strategy through one simulation and print a result summary\n"
            "(cash flow, crop mix, upgrades, contracts, watering coverage).\n\n"
            "The seed used is printed in the output. Pass it back to `replay`\n"
            "-- with the same strategy -- to reproduce the run exactly."
        ),
        epilog=(
            "examples:\n"
            "  # Default strategy, fresh random seed\n"
            "  python3 main.py single\n\n"
            "  # A specific strategy and seed, with the full daily history\n"
            "  python3 main.py single --strategy fast_seller --seed 42 --verbose\n\n"
            "strategies:\n" + strategy_roster() + "\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    single.add_argument(
        "--strategy",
        choices=AGENT_REGISTRY.keys(),
        default="profit_optimizer",
        metavar="NAME",
        help=(
            "Which scripted strategy to run (default: profit_optimizer). "
            "See the strategy list below for what each one probes."
        ),
    )
    single.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="INT",
        help=(
            "Seed driving the whole run. Omit for a fresh random seed; "
            "the one used is always printed so the run can be replayed."
        ),
    )
    single.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "Also print the day-by-day history: money, weather, market prices, "
            "inventory lots, and plantings for every simulated day."
        ),
    )
    single.set_defaults(func=cmd_single)

    replay = subparsers.add_parser(
        "replay",
        help="Reproduce a previously recorded run exactly.",
        description=(
            "Re-run a recorded (strategy, seed) pair and print the same summary\n"
            "`single` produced. Output is byte-for-byte identical every time.\n\n"
            "Both arguments are required, and the strategy must be the one the\n"
            "seed was recorded with: a seed only reproduces a run for the agent\n"
            "that made the decisions, since the sequence of random draws depends\n"
            "on what that agent chose to do."
        ),
        epilog=(
            "examples:\n"
            "  python3 main.py replay --strategy fast_seller --seed 123456789\n"
            "  python3 main.py replay --strategy profit_optimizer --seed 42\n\n"
            "strategies:\n" + strategy_roster() + "\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    replay.add_argument(
        "--strategy",
        choices=AGENT_REGISTRY.keys(),
        required=True,
        metavar="NAME",
        help="Required. The strategy the seed was recorded with.",
    )
    replay.add_argument(
        "--seed",
        type=int,
        required=True,
        metavar="INT",
        help="Required. The recorded seed to reproduce.",
    )
    replay.set_defaults(func=cmd_replay)

    batch = subparsers.add_parser(
        "batch",
        help="Run every strategy many times and generate a report.",
        description=(
            "Run every registered strategy --runs times each and write five\n"
            "artifacts to reports/:\n\n"
            "  run_results.csv      one row per run, with the seed that produced it\n"
            "  config_snapshot.json the exact config and base seed used\n"
            "  summary_report.md    per-strategy stats, cash-flow diagnostics,\n"
            "                       economics audit, and automated balance warnings\n"
            "  summary.json         the same per-strategy stats, machine-readable --\n"
            "                       what `python3 main.py view` reads\n"
            "  dashboard.html       every chart from `metrics.visualize`, bundled into\n"
            "                       one self-contained page (needs matplotlib; a\n"
            "                       placeholder page is written otherwise)\n\n"
            "All five are symlinks through reports/latest into an immutable\n"
            "reports/runs/<id>/ directory. Publishing swaps that one pointer, so\n"
            "reports/ never holds a half-updated set and past runs stay readable.\n\n"
            "Start with `python3 main.py view` for a quick table, or the 'Warnings'\n"
            "section of summary_report.md -- that is where a balance regression\n"
            "shows up without eyeballing every strategy."
        ),
        epilog=(
            "examples:\n"
            "  # Standard balance run\n"
            "  python3 main.py batch --runs 1000\n\n"
            "  # Reproducible batch: same seed gives the same per-run seeds, so a\n"
            "  # config or code change can be A/B'd against identical conditions\n"
            "  python3 main.py batch --runs 1000 --seed 12345\n\n"
            "  # Diagnostic scenario, leaving config/*.json untouched\n"
            "  python3 main.py batch --runs 100 --days 30 --start-money 300\n\n"
            "  # Force sequential execution (identical results, easier to profile)\n"
            "  python3 main.py batch --runs 1000 --workers 1\n\n"
            "  # Force the progress line on when stderr is not a terminal\n"
            "  python3 main.py batch --runs 100000 --progress\n\n"
            "  # Skip chart rendering for a fast/CI batch\n"
            "  python3 main.py batch --runs 1000 --no-charts\n\n"
            f"Runs all {len(AGENT_REGISTRY)} registered strategies. See "
            "`python3 main.py single --help`\nfor what each one probes.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    batch.add_argument(
        "--runs",
        type=_positive_int,
        default=1000,
        metavar="N",
        help=(
            f"Runs per strategy (default: 1000). Total simulations is this times "
            f"the {len(AGENT_REGISTRY)} registered strategies."
        ),
    )
    batch.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="INT",
        help=(
            "Base seed that mints every per-run seed, making the whole batch "
            "reproducible. Omit for a fresh one; either way the value used is "
            "recorded in the report and config snapshot."
        ),
    )
    batch.add_argument(
        "--workers",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Worker processes (default: one per CPU core). Use 1 to force "
            "sequential. Results are identical for a given seed at any worker "
            "count -- per-run seeds are minted before dispatch."
        ),
    )
    batch.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Live progress line on stderr: bar, percent, runs completed vs "
            "total, simulations/second, elapsed, and estimated time left. "
            "Shown automatically when stderr is a terminal; --progress forces "
            "it on (e.g. into a log), --no-progress off. Report output on "
            "stdout is unaffected either way."
        ),
    )
    batch.add_argument(
        "--days",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Override simulated days per run for this batch only, without "
            "editing config/simulation_settings.json. Useful for testing "
            "failure timing on short runs."
        ),
    )
    batch.add_argument(
        "--start-money",
        type=_nonnegative_float,
        default=None,
        metavar="AMOUNT",
        help=(
            "Override starting cash for this batch only, without editing "
            "config/simulation_settings.json. Useful for checking whether "
            "upgrades are reachable at all."
        ),
    )
    batch.add_argument(
        "--charts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Render reports/dashboard.html from this batch's results (default: "
            "on). Needs matplotlib (pip install -r requirements-viz.txt) -- "
            "skipped automatically with a placeholder page if it isn't "
            "installed. --no-charts skips rendering outright, for a faster or "
            "dependency-free batch."
        ),
    )
    statistics_group = batch.add_argument_group(
        "statistical analysis",
        "Formal inference around the same simulated runs. Nothing here changes "
        "what is simulated: analysis never touches the simulation RNG, and a "
        "fixed --runs batch keeps its legacy seed schedule byte for byte.",
    )
    statistics_group.add_argument(
        "--confidence",
        type=_probability,
        default=DEFAULT_CONFIDENCE,
        metavar="P",
        help=(
            "Confidence level for every interval in the report (default: 0.95). "
            "Means use a Student-t interval, proportions a Wilson score interval, "
            "quantiles a deterministic percentile bootstrap."
        ),
    )
    statistics_group.add_argument(
        "--sampling-plan",
        choices=("legacy", "independent", "paired"),
        default=None,
        help=(
            "Seed schedule (default: legacy for fixed --runs, independent for "
            "adaptive). legacy = the frozen historical stream, unchanged. "
            "independent = addressed per (strategy, replicate), so blocks extend "
            "and adding a strategy remaps nobody. paired = shared-initial-seed-v1, "
            "every strategy gets the same run seed per replicate (weak common "
            "random numbers; measured correlation is reported, not assumed)."
        ),
    )
    statistics_group.add_argument(
        "--analysis-seed",
        type=int,
        default=None,
        metavar="INT",
        help=(
            "Root seed for bootstrap resampling (default: the batch base seed). "
            "Separate from the simulation stream, so re-running an analysis can "
            "never perturb a recorded run."
        ),
    )
    statistics_group.add_argument(
        "--bootstrap-replications",
        type=_positive_int,
        default=DEFAULT_BOOTSTRAP_REPLICATIONS,
        metavar="N",
        help=f"Bootstrap replications for quantile and paired intervals (default: {DEFAULT_BOOTSTRAP_REPLICATIONS}).",
    )
    statistics_group.add_argument(
        "--correction",
        choices=comparisons.CORRECTION_METHODS,
        default="holm",
        help=(
            "Multiple-comparison correction for the all-pairs table (default: holm). "
            "bonferroni widens the intervals themselves for simultaneous coverage; "
            "holm adjusts p-values at the same family-wise error rate; "
            "benjamini_hochberg controls the false discovery rate and is exploratory only."
        ),
    )
    statistics_group.add_argument(
        "--baseline",
        default=None,
        metavar="STRATEGY",
        help=(
            "Compare every strategy against this one instead of all 55 pairs -- "
            "far less multiplicity to pay for when a baseline is what you actually "
            "want to know about."
        ),
    )
    statistics_group.add_argument(
        "--distributions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Write distributions.json: exact quantiles, ECDFs, histograms, tail "
            "probabilities and shape diagnostics from this batch's raw CSV "
            "(default: on). --no-distributions skips the bootstrap work."
        ),
    )
    statistics_group.add_argument(
        "--comparisons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write comparisons.json: strategy-vs-strategy differences (default: on).",
    )

    adaptive_group = batch.add_argument_group(
        "adaptive sampling",
        "Run until a declared precision target is met. Stopping is evaluated only "
        "at predeclared checkpoints, each spending its own slice of the error "
        "budget, so an early stop keeps its stated coverage.",
    )
    adaptive_group.add_argument(
        "--min-runs",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Minimum runs per strategy before any stopping check (default: min(500, --runs)).",
    )
    adaptive_group.add_argument(
        "--max-runs",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Hard ceiling on runs per strategy. Reaching it publishes the result "
            "with the precision target recorded as unmet rather than sampling on."
        ),
    )
    adaptive_group.add_argument(
        "--checkpoint-runs",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Runs per strategy between checkpoints (default: an eighth of the range).",
    )
    adaptive_group.add_argument(
        "--stop-estimand",
        action="append",
        default=None,
        metavar="ID",
        help=(
            "Estimand a precision target applies to; repeatable "
            f"(default: expected_final_money). Known: {', '.join(estimands.adaptive_estimands())}."
        ),
    )
    adaptive_group.add_argument(
        "--target-half-width",
        type=_nonnegative_float,
        default=None,
        metavar="X",
        help="Stop once the interval half-width is at most X, in the estimand's own unit.",
    )
    adaptive_group.add_argument(
        "--target-relative-half-width",
        type=_nonnegative_float,
        default=None,
        metavar="F",
        help=(
            "Stop once the half-width is at most this fraction of the estimate. "
            "Never satisfied while the estimate sits at ~0, where relative "
            "precision is meaningless."
        ),
    )
    adaptive_group.add_argument(
        "--bankruptcy-half-width",
        type=_nonnegative_float,
        default=None,
        metavar="X",
        help="Precision target on the bankruptcy probability (Wilson interval half-width).",
    )
    adaptive_group.add_argument(
        "--min-bankruptcies",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Require at least N bankruptcies per strategy before stopping. If the "
            "maximum is reached without them, the batch publishes with stop reason "
            "rare_event_minimum_unmet."
        ),
    )
    adaptive_group.add_argument(
        "--min-survivals",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Require at least N surviving runs per strategy before stopping.",
    )
    adaptive_group.add_argument(
        "--stopping-mode",
        choices=("all", "any"),
        default="all",
        help="Whether every precision target must be met (default) or just one.",
    )
    adaptive_group.add_argument(
        "--alpha-spending",
        choices=("obrien_fleming", "pocock", "none"),
        default="obrien_fleming",
        help=(
            "How the error budget is spread over checkpoints (default: "
            "obrien_fleming -- spends almost nothing early, so an early stop has "
            "to clear a high bar). 'none' disables sequential correction and is "
            "only honest for a single look."
        ),
    )
    adaptive_group.add_argument(
        "--track-quantiles",
        action="store_true",
        help=(
            "Also record an exact median at every checkpoint. Keeps every run's "
            "final money in memory (O(runs) rather than O(1)), so it is off by "
            "default."
        ),
    )

    batch.set_defaults(func=cmd_batch)

    view_parser = subparsers.add_parser(
        "view",
        help="Print a quick strategy-comparison table from a published batch.",
        description=(
            "Read a published batch's reports/<run>/summary.json and print a\n"
            "compact table -- for the 'just tell me the numbers' question that\n"
            "scrolling summary_report.md doesn't answer quickly.\n\n"
            "Defaults to reports/latest. Use --run to pick another published run\n"
            "(--list shows what's available), or --diff to compare two runs\n"
            "side by side -- the isolate-the-change step of the balance-testing\n"
            "workflow in CLAUDE.md."
        ),
        epilog=(
            "examples:\n"
            "  # Quick glance at the latest batch\n"
            "  python3 main.py view\n\n"
            "  # Which strategies are riskiest, top 5\n"
            "  python3 main.py view --sort bankruptcy_rate --top 5\n\n"
            "  # Just two strategies, custom columns\n"
            "  python3 main.py view --strategy fast_seller,profit_optimizer \\\n"
            "      --fields avg_final_money,avg_watering_rate\n\n"
            "  # What changed since the previous batch\n"
            "  python3 main.py view --diff latest-1 latest\n\n"
            "  # List published runs (for --run/--diff references)\n"
            "  python3 main.py view --list\n\n"
            "  # Open this run's chart dashboard in the browser\n"
            "  python3 main.py view --open\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    view_parser.add_argument(
        "--run",
        default=None,
        metavar="REF",
        help="Which published run to show (default: latest). 'latest', 'latest-N', or a run id -- see --list.",
    )
    view_parser.add_argument(
        "--list",
        action="store_true",
        help="List published runs (most recent first) instead of showing a table.",
    )
    view_parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("RUN_A", "RUN_B"),
        default=None,
        help="Compare two runs field by field (e.g. --diff latest-1 latest), sorted by size of change.",
    )
    view_parser.add_argument(
        "--only-changed",
        action="store_true",
        help="With --diff, hide strategies whose value didn't change.",
    )
    view_parser.add_argument(
        "--fields",
        default=None,
        metavar="F1,F2,...",
        help=(
            "Comma-separated stat fields to show (default: avg_final_money,"
            "bankruptcy_rate,avg_profit_per_day). 'all' shows every scalar field."
        ),
    )
    view_parser.add_argument(
        "--sort",
        default=None,
        metavar="FIELD",
        help="Field to sort by (default: the first --fields column). Descending unless --asc.",
    )
    view_parser.add_argument(
        "--asc", action="store_true", help="Sort ascending instead of the default descending."
    )
    view_parser.add_argument(
        "--top",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Show only the top N strategies.",
    )
    view_parser.add_argument(
        "--strategy",
        default=None,
        metavar="NAME,...",
        help="Comma-separated strategy names to include (default: all).",
    )
    view_parser.add_argument(
        "--open",
        action="store_true",
        help="Also open this run's dashboard.html in the default browser.",
    )
    view_parser.add_argument(
        "--intervals",
        action="store_true",
        help=(
            "Also print each strategy's confidence intervals, with the effective "
            "sample count and interval method behind every estimate."
        ),
    )
    view_parser.add_argument(
        "--convergence",
        action="store_true",
        help="Also print the checkpoint history and why sampling stopped.",
    )
    view_parser.add_argument(
        "--estimand",
        default=None,
        metavar="IDS",
        help=(
            "Comma-separated estimand ids to show with --intervals "
            f"(default: all recorded). Known: {', '.join(estimands.REGISTRY)}."
        ),
    )
    view_parser.set_defaults(func=cmd_view)

    analyze = subparsers.add_parser(
        "analyze",
        help="Exact distribution analysis and strategy comparisons for a batch.",
        description=(
            "Formal analysis over a batch's *raw* per-run observations.\n\n"
            "Quantiles, ECDFs, tails and shape diagnostics are computed from\n"
            "run_results.csv, not from the bounded median reservoir the streaming\n"
            "aggregator keeps -- so they are exact, and they are labelled with the\n"
            "empirical quantile convention that produced them.\n\n"
            "Bootstrap intervals are deterministic: the resampling stream is derived\n"
            "from the batch's base seed (or --analysis-seed) and the estimand id, and\n"
            "is entirely separate from the simulation RNG, so analysis can never\n"
            "perturb a recorded run.\n\n"
            "--csv accepts any batch CSV with the same columns, including one written\n"
            "by `farm-c batch --csv`: the C port stays a raw-data producer and its\n"
            "output goes through this same inference layer."
        ),
        epilog=(
            "examples:\n"
            "  # The latest published batch\n"
            "  python3 main.py analyze\n\n"
            "  # A specific published run, compared against one baseline strategy\n"
            "  python3 main.py analyze --run latest-1 --baseline profit_optimizer\n\n"
            "  # A paired experiment (batch run with --sampling-plan paired)\n"
            "  python3 main.py analyze --compare paired\n\n"
            "  # A farm-c batch, through the same inference code\n"
            "  python3 main.py analyze --csv farm-c/reports/run_results.csv\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze.add_argument(
        "--run",
        default=None,
        metavar="REF",
        help="Published run to analyze: 'latest' (default), 'latest-N', or a run id.",
    )
    analyze.add_argument(
        "--csv",
        default=None,
        metavar="PATH",
        help="Analyze this CSV instead of a published run (accepts farm-c output).",
    )
    analyze.add_argument(
        "--confidence",
        type=_probability,
        default=DEFAULT_CONFIDENCE,
        metavar="P",
        help="Confidence level for every interval (default: 0.95).",
    )
    analyze.add_argument(
        "--bootstrap-replications",
        type=_positive_int,
        default=DEFAULT_BOOTSTRAP_REPLICATIONS,
        metavar="N",
        help=f"Bootstrap replications (default: {DEFAULT_BOOTSTRAP_REPLICATIONS}).",
    )
    analyze.add_argument(
        "--analysis-seed",
        type=int,
        default=None,
        metavar="INT",
        help="Bootstrap seed root (default: the batch's own base seed).",
    )
    analyze.add_argument(
        "--quantiles",
        default=None,
        metavar="P,P,...",
        help="Quantile probabilities to report (default: 0.05,0.25,0.5,0.75,0.95).",
    )
    analyze.add_argument(
        "--tail",
        default=None,
        metavar="X,X,...",
        help="Report P(final money < X) for each threshold (default: 0).",
    )
    analyze.add_argument(
        "--cohort",
        choices=("all_runs", "survivors", "bankrupt", "bankruptcy_day"),
        default="all_runs",
        help="Which cohort the printed table describes (default: all_runs).",
    )
    analyze.add_argument(
        "--compare",
        choices=("independent", "paired", "none"),
        default="independent",
        help=(
            "Comparison mode (default: independent). 'paired' joins runs on "
            "replicate_id and needs a batch run with --sampling-plan paired."
        ),
    )
    analyze.add_argument(
        "--estimand",
        default=None,
        metavar="IDS",
        help="Comma-separated estimand ids to compare (default: money, profit/day, bankruptcy).",
    )
    analyze.add_argument(
        "--correction",
        choices=comparisons.CORRECTION_METHODS,
        default="holm",
        help="Multiple-comparison correction across each estimand's pair family.",
    )
    analyze.add_argument(
        "--baseline",
        default=None,
        metavar="STRATEGY",
        help="Compare every strategy against this one instead of all pairs.",
    )
    analyze.add_argument(
        "--top",
        type=_positive_int,
        default=10,
        metavar="N",
        help="How many of the largest differences to print per estimand (default: 10).",
    )
    analyze.add_argument(
        "--days",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Horizon to censor survival curves at (default: the batch's own).",
    )
    analyze.add_argument(
        "--json",
        default=None,
        metavar="PATH",
        help="Also write the full analysis document to this path.",
    )
    analyze.set_defaults(func=cmd_analyze)

    uncertainty_parser = subparsers.add_parser(
        "uncertainty",
        help="Sample uncertain config parameters and measure what the outcome depends on.",
        description=(
            "Epistemic parameter uncertainty: re-run the economy under sampled\n"
            "configurations and report how much of the outcome is 'we do not know\n"
            "the parameters' versus 'the simulation is stochastic'.\n\n"
            "The uncertainty specification is a separate document (schema\n"
            "farm-uncertainty-v1) and never config/*.json -- those stay the runtime\n"
            "contract that both this simulator and farm-c read. Every sampled\n"
            "configuration is deep-copied and passed through the same validators\n"
            "load_config() uses; rejects are counted and published rather than\n"
            "clamped into range.\n\n"
            "Cost is the thing to watch: a design's configuration count times\n"
            "--replicates times the strategy count is how many simulations run. The\n"
            "count is printed before any of them start."
        ),
        epilog=(
            "methods (cheapest first):\n"
            "  oat        low/base/high per parameter -- 2k+1 configs, no interactions\n"
            "  scenarios  named corners from a JSON file\n"
            "  monte-carlo  plain sampling; the ONLY design that honours correlation groups\n"
            "  lhs        Latin hypercube -- stratified coverage at --samples configs\n"
            "  morris     screening -- --trajectories * (k+1) configs, ranks by mu*\n"
            "  sobol      variance decomposition -- --samples * (k+2) configs\n\n"
            "examples:\n"
            "  python3 main.py uncertainty --spec experiments/specs/example-uncertainty.json \\\n"
            "      --method oat --replicates 50 --strategy profit_optimizer\n\n"
            "  python3 main.py uncertainty --spec experiments/specs/example-uncertainty.json \\\n"
            "      --method morris --trajectories 8 --replicates 25\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    uncertainty_parser.add_argument(
        "--spec", required=True, metavar="PATH", help="farm-uncertainty-v1 specification file."
    )
    uncertainty_parser.add_argument(
        "--method",
        choices=("oat", "scenarios", "monte-carlo", "lhs", "morris", "sobol"),
        default="oat",
        help="Sampling/sensitivity design (default: oat).",
    )
    uncertainty_parser.add_argument(
        "--samples",
        type=_positive_int,
        default=16,
        metavar="N",
        help="Configuration samples for monte-carlo/lhs, or the Sobol base sample count.",
    )
    uncertainty_parser.add_argument(
        "--trajectories",
        type=_positive_int,
        default=8,
        metavar="R",
        help="Morris trajectories (default: 8).",
    )
    uncertainty_parser.add_argument(
        "--levels",
        type=_positive_int,
        default=4,
        metavar="P",
        help="Morris grid levels, must be even (default: 4).",
    )
    uncertainty_parser.add_argument(
        "--low",
        type=_probability,
        default=0.05,
        metavar="Q",
        help="Quantile used as the 'low' level for --method oat (default: 0.05).",
    )
    uncertainty_parser.add_argument(
        "--high",
        type=_probability,
        default=0.95,
        metavar="Q",
        help="Quantile used as the 'high' level for --method oat (default: 0.95).",
    )
    uncertainty_parser.add_argument(
        "--scenarios",
        default=None,
        metavar="PATH",
        help='JSON object of named corners, e.g. {"pessimistic": {"greenleaf_base_price": 0.05}}.',
    )
    uncertainty_parser.add_argument(
        "--replicates",
        type=_positive_int,
        default=25,
        metavar="N",
        help=(
            "Aleatory replicates per configuration sample per strategy (default: 25). "
            "This is the within-configuration sample size the variance split uses."
        ),
    )
    uncertainty_parser.add_argument(
        "--strategy",
        default=None,
        metavar="NAMES",
        help="Comma-separated strategies to run (default: all). Fewer strategies, fewer runs.",
    )
    uncertainty_parser.add_argument(
        "--report-strategy",
        default=None,
        metavar="NAME",
        help="Strategy the printed sensitivity table describes (default: the first one run).",
    )
    uncertainty_parser.add_argument(
        "--estimand",
        default=None,
        metavar="IDS",
        help="Comma-separated response estimands (default: expected_final_money,bankruptcy_probability).",
    )
    uncertainty_parser.add_argument(
        "--seed", type=int, default=None, metavar="INT", help="Study seed (omit for a fresh one)."
    )
    uncertainty_parser.add_argument(
        "--confidence", type=_probability, default=DEFAULT_CONFIDENCE, metavar="P"
    )
    uncertainty_parser.add_argument(
        "--days",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Override simulated days per run (shorter runs make a big design affordable).",
    )
    uncertainty_parser.add_argument(
        "--workers", type=_positive_int, default=None, metavar="N", help="Worker processes."
    )
    uncertainty_parser.add_argument(
        "--out",
        default=None,
        metavar="DIR",
        help="Output directory (default: reports/uncertainty/<timestamp>-<design>/).",
    )
    uncertainty_parser.set_defaults(func=cmd_uncertainty)

    return parser


def _positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _nonnegative_float(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _probability(value):
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise argparse.ArgumentTypeError("must be strictly between 0 and 1")
    return parsed


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
