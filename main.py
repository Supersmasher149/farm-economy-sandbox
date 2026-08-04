#!/usr/bin/env python3
"""CLI entry point for the farm economy sandbox.

Examples:
    python main.py single --strategy profit_optimizer --seed 42 --verbose
    python main.py batch --runs 1000
    python main.py replay --strategy fast_seller --seed 123456789
"""
import argparse
import json
import os

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
from metrics.aggregate_results import aggregate
from metrics.report import generate_markdown_report
from metrics.run_results import write_csv
from metrics.warnings import evaluate_warnings
from runner.batch_run import run_batch
from runner.single_run import run_single
from simulation.configuration import validate

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
    return crops, upgrades, config, world


def print_player_summary(player, seed, strategy_name):
    print(f"Strategy: {strategy_name}")
    print(f"Seed: {seed}")
    print(f"Days simulated: {player.day}")
    print(f"Final money: {round(player.money, 2)}")
    print(f"Total revenue: {round(player.total_revenue, 2)}")
    print(f"Total expenses: {round(player.total_expenses, 2)}")
    print(f"Net profit: {round(player.total_revenue - player.total_expenses, 2)}")
    print(f"Crops planted / harvested / sold: {player.total_planted} / {player.total_harvested} / {player.total_sold}")
    print(f"Crop plant counts: {player.crop_plant_counts}")
    print(f"Upgrades owned: {sorted(player.upgrades_owned)}")
    print(f"Upgrade purchase days: {player.upgrade_purchase_days}")
    print(f"Idle days: {player.idle_days}")
    print(f"Bankrupt: {player.bankrupt}")
    print(f"Lowest / highest money: {round(player.lowest_money, 2)} / {round(player.highest_money, 2)}")
    watering_rate = 100 * player.total_waterings / player.slot_days if player.slot_days else 0.0
    loss_rate = 100 * player.total_crops_lost / player.total_harvest_events if player.total_harvest_events else 0.0
    print(f"Watering coverage: {round(watering_rate, 1)}% of plot-days ({player.total_waterings}/{player.slot_days})")
    print(f"Crops lost: {player.total_crops_lost} ({round(loss_rate, 1)}% of matured crops)")
    print(f"Fertilizer bought / applied: {player.total_fertilizer_bought} / {player.total_fertilizer_applied}")
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
        config, agent, crops, upgrades, watering_settings, fertilizer_config,
        seed=args.seed, record_history=args.verbose, world=world,
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
        config, agent, crops, upgrades, watering_settings, fertilizer_config,
        seed=args.seed, record_history=False, world=world,
    )
    print_player_summary(player, seed, agent.name)


def cmd_batch(args):
    crops, upgrades, config, world = load_config()
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agents = [cls() for cls in AGENT_REGISTRY.values()]

    results = run_batch(
        config, agents, crops, upgrades, watering_settings, fertilizer_config,
        num_runs=args.runs, base_seed=args.seed, world=world,
    )
    summary = aggregate(results)
    warning_list = evaluate_warnings(summary, config)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    crop_ids = [c["id"] for c in crops]
    crop_names = {c["id"]: c["name"] for c in crops}
    agent_descriptions = {agent.name: agent.description for agent in agents}

    csv_path = os.path.join(REPORTS_DIR, "run_results.csv")
    write_csv(results, csv_path, crop_ids)

    config_snapshot_path = os.path.join(REPORTS_DIR, "config_snapshot.json")
    with open(config_snapshot_path, "w") as f:
        json.dump({
            "crops": crops,
            "upgrades": upgrades,
            "simulation_settings": config,
            "watering_settings": watering_settings,
            "fertilizer": fertilizer_config,
            "world": world,
            "num_runs": args.runs,
        }, f, indent=2)

    report_path = os.path.join(REPORTS_DIR, "summary_report.md")
    report_text = generate_markdown_report(config, args.runs, summary, warning_list, crop_names, agent_descriptions)
    with open(report_path, "w") as f:
        f.write(report_text)

    print(f"Ran {args.runs} simulations x {len(agents)} strategies = {len(results)} total runs.")
    print(f"CSV:    {csv_path}")
    print(f"Config: {config_snapshot_path}")
    print(f"Report: {report_path}")
    print()
    print(report_text)


def build_parser():
    parser = argparse.ArgumentParser(description="Farm economy sandbox simulator.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    single = subparsers.add_parser("single", help="Run one simulation.")
    single.add_argument("--strategy", choices=AGENT_REGISTRY.keys(), default="profit_optimizer")
    single.add_argument("--seed", type=int, default=None)
    single.add_argument("--verbose", action="store_true", help="Print full daily history.")
    single.set_defaults(func=cmd_single)

    replay = subparsers.add_parser("replay", help="Reproduce a run from a previously recorded seed.")
    replay.add_argument("--strategy", choices=AGENT_REGISTRY.keys(), required=True)
    replay.add_argument("--seed", type=int, required=True)
    replay.set_defaults(func=cmd_replay)

    batch = subparsers.add_parser("batch", help="Run a batch across all strategies and generate a report.")
    batch.add_argument("--runs", type=int, default=1000)
    batch.add_argument("--seed", type=int, default=None, help="Base seed for generating per-run seeds.")
    batch.set_defaults(func=cmd_batch)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
