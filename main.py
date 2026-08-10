#!/usr/bin/env python3
"""CLI entry point for the farm economy sandbox.

Examples:
    python main.py single --strategy profit_optimizer --seed 42 --verbose
    python main.py batch --runs 1000
    python main.py replay --strategy fast_seller --seed 123456789
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
from metrics.aggregate_results import BatchAggregator
from metrics.economics_audit import build_economics_audit
from metrics.report import generate_markdown_report
from metrics.run_results import write_csv
from metrics.warnings import evaluate_warnings
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


def cmd_batch(args):
    crops, upgrades, config, world = load_config()
    config = dict(config)
    if args.days is not None:
        config["days"] = args.days
    if args.start_money is not None:
        config["start_money"] = args.start_money
    validate_simulation_config(config)
    base_seed = resolve_base_seed(args.seed)
    watering_settings, fertilizer_config = world["watering"], world["fertilizer"]
    agents = [cls() for cls in AGENT_REGISTRY.values()]
    total_runs = args.runs * len(agents)

    results = run_batch(
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
    )

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
        aggregator = BatchAggregator()

        def _tee(stream):
            for r in stream:
                aggregator.add(r)
                yield r

        staged_csv_path = os.path.join(staging_dir, "run_results.csv")
        write_csv(_tee(results), staged_csv_path, crop_ids)

        summary = aggregator.finalize()
        warning_list = evaluate_warnings(summary, config)

        snapshot = {
            "base_seed": base_seed,
            "crops": crops,
            "upgrades": upgrades,
            "simulation_settings": config,
            "watering_settings": watering_settings,
            "fertilizer": fertilizer_config,
            "world": world,
            "num_runs": args.runs,
        }
        staged_snapshot_path = os.path.join(staging_dir, "config_snapshot.json")
        with open(staged_snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)

        report_text = generate_markdown_report(
            config,
            args.runs,
            summary,
            warning_list,
            crop_names,
            agent_descriptions,
            economics_audit,
            base_seed=base_seed,
        )
        staged_report_path = os.path.join(staging_dir, "summary_report.md")
        with open(staged_report_path, "w") as f:
            f.write(report_text)

        run_dir = _publish_report_artifacts(staging_dir, REPORTS_DIR)
    finally:
        # Publication consumed the staging directory by renaming it, so this
        # only removes it when the batch failed before getting that far.
        shutil.rmtree(staging_dir, ignore_errors=True)

    csv_path = os.path.join(REPORTS_DIR, "run_results.csv")
    config_snapshot_path = os.path.join(REPORTS_DIR, "config_snapshot.json")
    report_path = os.path.join(REPORTS_DIR, "summary_report.md")

    print(f"Ran {args.runs} simulations x {len(agents)} strategies = {total_runs} total runs.")
    print(
        f"Time:   {format_duration(progress.elapsed)} elapsed ({format_rate(progress.rate)} sim/s)"
    )
    print(f"CSV:    {csv_path}")
    print(f"Config: {config_snapshot_path}")
    print(f"Report: {report_path}")
    print(f"Run:    {run_dir}")
    print()
    print(report_text)


ARTIFACT_NAMES = ("run_results.csv", "config_snapshot.json", "summary_report.md")
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
        try:
            _replace_symlink(
                os.path.join(RUNS_DIRNAME, run_id), os.path.join(reports_dir, LATEST_LINK)
            )
            for name in ARTIFACT_NAMES:
                link_path = os.path.join(reports_dir, name)
                target = os.path.join(LATEST_LINK, name)
                # Recreate only when it is not already the stable pointer --
                # e.g. upgrading a tree that still has real files here.
                if not os.path.islink(link_path) or os.readlink(link_path) != target:
                    _replace_symlink(target, link_path)
        except Exception:
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
        metavar="{single,replay,batch}",
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
            "Run every registered strategy --runs times each and write three\n"
            "artifacts to reports/:\n\n"
            "  run_results.csv      one row per run, with the seed that produced it\n"
            "  config_snapshot.json the exact config and base seed used\n"
            "  summary_report.md    per-strategy stats, cash-flow diagnostics,\n"
            "                       economics audit, and automated balance warnings\n\n"
            "All three are symlinks through reports/latest into an immutable\n"
            "reports/runs/<id>/ directory. Publishing swaps that one pointer, so\n"
            "reports/ never holds a half-updated set and past runs stay readable.\n\n"
            "Start with the 'Warnings' section of summary_report.md -- that is where\n"
            "a balance regression shows up without eyeballing every strategy."
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
    batch.set_defaults(func=cmd_batch)

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


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
