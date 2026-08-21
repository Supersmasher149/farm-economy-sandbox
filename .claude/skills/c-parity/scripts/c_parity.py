#!/usr/bin/env python3
"""Cross-language parity harness for farm-c against the Python simulator.

farm-c/README.md's Phase 6 note establishes that a `--seed` shared between
`farm-c batch` and `python3 main.py batch` mints the identical per-run seed
for the identical strategy at the identical position in the run list -- and
then says the run-for-run comparison that property enables was done *by
hand* for one (strategy, seed) pair during development. This automates it.

Design notes, because two obvious shortcuts are both wrong here:

  * **Do not diff the two CSVs.** `metrics/run_results.py:_money` rounds
    every monetary field to cents (Decimal, ROUND_HALF_UP) on its way into
    `reports/run_results.csv`, while farm-c writes raw doubles at `%.17g`.
    Comparing those compares a cent-rounded number against a full-precision
    one; re-rounding the C side to match would then hide any drift smaller
    than a cent -- the exact failure mode replay-guard's SKILL.md documents
    for `round(money, 6)`. So the Python side here calls `run_single`
    in-process and reads raw `PlayerState` attributes, *before* any
    reporting-layer rounding touches them.

  * **Do not compare with `==` or an epsilon.** Floats are compared by
    `float.hex()`, which is exact and (unlike `==`) distinguishes +0.0 from
    -0.0. Every C value round-trips exactly: `%.17g` -> `float()` is the
    identical double.

The seeds are read out of the C batch's own CSV rather than re-derived, so
the field comparison does not silently depend on the minting property
holding. `check` verifies minting separately, against
`runner/batch_run.py`'s own `random.Random(base).randrange(2**32)` order, and
reports it as its own result.

Usage:
    python3 .claude/skills/c-parity/scripts/c_parity.py check [--runs N] [--seed S]
    python3 .claude/skills/c-parity/scripts/c_parity.py seeds [--runs N] [--seed S]
    python3 .claude/skills/c-parity/scripts/c_parity.py trace STRATEGY SEED
"""

import argparse
import csv
import os
import random
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SKILL_DIR)))
FARM_C_DIR = os.path.join(REPO_ROOT, "farm-c")
DEFAULT_BINARY = os.path.join(FARM_C_DIR, "farm-c")
# Pinned, not a flag: main.py's load_config() reads <repo>/config with no way
# to point it elsewhere, so letting the C side take a different --config would
# only let the two sides silently diverge on inputs.
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

sys.path.insert(0, REPO_ROOT)


# --- the comparable field set --------------------------------------------
#
# Exactly the 20 non-key columns farm-c's batch CSV carries (main.c's
# write_csv_row), which is the scalar subset of metrics/run_results.py's
# RunResult -- BatchRunResult deliberately drops the crop-count and
# percentage dicts, so those are out of scope here and this file says so
# rather than appearing to check more than it does.
#
# `kind` drives both parsing and comparison: floats compare by .hex().
FIELDS = [
    ("days_simulated", "int"),
    ("final_money", "float"),
    ("total_revenue", "float"),
    ("total_expenses", "float"),
    ("net_profit", "float"),
    ("total_planted", "int"),
    ("total_harvested", "int"),
    ("total_sold", "int"),
    ("idle_days", "int"),
    ("bankrupt", "bool"),
    ("bankruptcy_day", "optint"),
    ("lowest_money", "float"),
    ("highest_money", "float"),
    ("total_waterings", "int"),
    ("total_fertilizer_applied", "int"),
    ("total_processed", "int"),
    ("contracts_completed", "int"),
    ("contracts_failed", "int"),
    ("contract_penalties", "float"),
    ("reputation", "float"),
]


def parse_c_value(raw, kind):
    if kind == "float":
        return float(raw)
    if kind == "int":
        return int(raw)
    if kind == "bool":
        return raw == "true"
    if kind == "optint":
        return int(raw) if raw != "" else None
    raise AssertionError("unknown kind " + kind)


def python_fields(player, days_simulated):
    """Raw PlayerState values, mirroring src/batch.c:snapshot_result.

    net_profit repeats snapshot_result's `total_revenue - total_expenses`
    literally rather than reusing metrics' Decimal-based subtraction: the
    point is to compare the same arithmetic, not an equivalent one.
    """
    return {
        "days_simulated": days_simulated,
        "final_money": player.money,
        "total_revenue": player.total_revenue,
        "total_expenses": player.total_expenses,
        "net_profit": player.total_revenue - player.total_expenses,
        "total_planted": player.total_planted,
        "total_harvested": player.total_harvested,
        "total_sold": player.total_sold,
        "idle_days": player.idle_days,
        "bankrupt": player.bankrupt,
        "bankruptcy_day": player.bankruptcy_day,
        "lowest_money": player.lowest_money,
        "highest_money": player.highest_money,
        "total_waterings": player.total_waterings,
        "total_fertilizer_applied": player.total_fertilizer_applied,
        "total_processed": player.total_processed,
        "contracts_completed": player.contracts_completed,
        "contracts_failed": player.contracts_failed,
        "contract_penalties": player.contract_penalties,
        "reputation": player.reputation,
    }


def show(value, kind):
    """Render for both comparison and display. Floats go to hex, never
    rounded -- see this module's header."""
    if kind == "float":
        return float(value).hex()
    return repr(value)


# --- running the two sides ------------------------------------------------


def run_c_batch(binary, strategies, runs, base_seed, days, start_money, csv_path):
    cmd = [
        binary,
        "batch",
        "--runs",
        str(runs),
        "--seed",
        str(base_seed),
        "--config",
        CONFIG_DIR,
        "--csv",
        csv_path,
    ]
    for name in strategies:
        cmd += ["--strategy", name]
    if days is not None:
        cmd += ["--days", str(days)]
    if start_money is not None:
        cmd += ["--start-money", str(start_money)]
    proc = subprocess.run(cmd, cwd=FARM_C_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"farm-c batch failed (exit {proc.returncode}): {' '.join(cmd)}")
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def load_python_side(days, start_money):
    from main import AGENT_REGISTRY, load_config
    from simulation.configuration import validate_simulation_config

    crops, upgrades, config, world = load_config()
    config = dict(config)
    if days is not None:
        config["days"] = days
    if start_money is not None:
        config["start_money"] = start_money
    validate_simulation_config(config)
    return AGENT_REGISTRY, crops, upgrades, config, world


def run_python_single(registry, crops, upgrades, config, world, strategy, seed, on_day=None):
    from runner.single_run import run_single

    # A fresh agent instance per run, matching runner/batch_run.py:_execute --
    # which constructs one per job precisely so no per-run state can leak
    # between runs on either the sequential or the pooled path.
    agent = registry[strategy]()
    player, _, _ = run_single(
        config,
        agent,
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        seed=seed,
        record_history=False,
        world=world,
        on_day=on_day,
    )
    return player


def mint_expected_seeds(strategies, runs, base_seed):
    """runner/batch_run.py's minting loop, reproduced exactly.

    `jobs = ((agent, seed_rng.randrange(2**32)) for agent in agents
             for _ in range(num_runs))` -- agent-major, single-threaded,
    one draw per job.
    """
    seed_rng = random.Random(base_seed)
    return [(name, seed_rng.randrange(2**32)) for name in strategies for _ in range(runs)]


# --- subcommands ----------------------------------------------------------


def resolve_strategies(requested):
    from main import AGENT_REGISTRY

    names = list(AGENT_REGISTRY.keys())
    if not requested:
        return names
    unknown = [s for s in requested if s not in names]
    if unknown:
        raise SystemExit(f"unknown strategy: {', '.join(unknown)}")
    return list(requested)


def check_seed_minting(rows, strategies, runs, base_seed):
    """Compare the C's own emitted seeds against Python's minting order.

    Returns a list of failure strings.
    """
    expected = mint_expected_seeds(strategies, runs, base_seed)
    actual = [(r["strategy"], int(r["seed"])) for r in rows]
    failures = []
    if len(actual) != len(expected):
        failures.append(f"run count: expected {len(expected)}, farm-c emitted {len(actual)}")
        return failures
    for index, (want, got) in enumerate(zip(expected, actual, strict=True)):
        if want != got:
            failures.append(
                f"job {index}: expected ({want[0]}, {want[1]}), farm-c minted ({got[0]}, {got[1]})"
            )
    return failures


def cmd_check(args):
    strategies = resolve_strategies(args.strategy)
    binary = args.binary
    if not os.path.exists(binary):
        raise SystemExit(f"no farm-c binary at {binary} -- run `make farm-c` in farm-c/")

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "c_runs.csv")
        rows = run_c_batch(
            binary, strategies, args.runs, args.seed, args.days, args.start_money, csv_path
        )

    print(f"farm-c: {len(rows)} runs across {len(strategies)} strategies, base seed {args.seed}")

    seed_failures = check_seed_minting(rows, strategies, args.runs, args.seed)
    if seed_failures:
        print("\nSEED MINTING: FAIL")
        for line in seed_failures[: args.max_report]:
            print("  " + line)
        if len(seed_failures) > args.max_report:
            print(f"  ... and {len(seed_failures) - args.max_report} more")
        # The per-run comparison below still runs and is still meaningful:
        # seeds are read from the CSV, so each pair is compared on the seed
        # the C actually used, whatever Python would have minted.
    else:
        print(f"seed minting: OK (matches runner/batch_run.py for all {len(rows)} jobs)")

    registry, crops, upgrades, config, world = load_python_side(args.days, args.start_money)

    mismatched = []
    for index, row in enumerate(rows):
        strategy = row["strategy"]
        seed = int(row["seed"])
        player = run_python_single(registry, crops, upgrades, config, world, strategy, seed)
        py = python_fields(player, player.day)

        diffs = []
        for name, kind in FIELDS:
            c_value = parse_c_value(row[name], kind)
            py_value = py[name]
            if show(c_value, kind) != show(py_value, kind):
                diffs.append(
                    f"    {name:<26} C={show(c_value, kind)}  python={show(py_value, kind)}"
                )
        if diffs:
            mismatched.append((strategy, seed, diffs))
            print(f"\nFAIL  {strategy} seed={seed}")
            for line in diffs:
                print(line)
        elif args.verbose:
            print(f"ok    {strategy} seed={seed}")

        if args.progress and (index + 1) % 10 == 0:
            sys.stderr.write(f"\r  compared {index + 1}/{len(rows)} runs")
            sys.stderr.flush()
    if args.progress:
        sys.stderr.write("\r" + " " * 40 + "\r")

    print(
        f"\n{len(rows)} runs compared, {len(mismatched)} field sets mismatched, "
        f"{len(FIELDS)} fields per run"
    )
    if mismatched or seed_failures:
        print("PARITY: FAIL")
        if mismatched:
            script = os.path.relpath(os.path.abspath(__file__), REPO_ROOT)
            print(
                f"Localize one with: python3 {script} trace {mismatched[0][0]} {mismatched[0][1]}"
            )
        return 1
    print("PARITY: OK")
    return 0


def cmd_seeds(args):
    strategies = resolve_strategies(args.strategy)
    binary = args.binary
    if not os.path.exists(binary):
        raise SystemExit(f"no farm-c binary at {binary} -- run `make farm-c` in farm-c/")
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "c_runs.csv")
        rows = run_c_batch(
            binary, strategies, args.runs, args.seed, args.days, args.start_money, csv_path
        )
    failures = check_seed_minting(rows, strategies, args.runs, args.seed)
    if failures:
        print(f"SEED MINTING: FAIL ({len(failures)} of {len(rows)} jobs)")
        for line in failures[: args.max_report]:
            print("  " + line)
        if len(failures) > args.max_report:
            print(f"  ... and {len(failures) - args.max_report} more")
        return 1
    print(
        f"SEED MINTING: OK -- {len(rows)} jobs, base seed {args.seed}, "
        f"agent-major order matches runner/batch_run.py"
    )
    return 0


def cmd_trace(args):
    """Localize a divergence to its first simulated day.

    Coarser than `check` by construction: farm-c's per-day line
    (main.c:print_day) prints money at %.2f and rainfall at %.3f, so this
    compares what that line shows, not full precision. It answers "which day
    did they part company", not "are they bit-identical" -- `check` is the
    exact gate. A run can therefore trace clean and still fail `check`, when
    the drift is below the printed precision on every day; that is itself a
    useful signal (sub-cent drift that only shows in the final tally).
    """
    binary = args.binary
    if not os.path.exists(binary):
        raise SystemExit(f"no farm-c binary at {binary} -- run `make farm-c` in farm-c/")
    resolve_strategies([args.strategy])

    proc = subprocess.run(
        [
            binary,
            "single",
            "--strategy",
            args.strategy,
            "--seed",
            str(args.seed),
            "--config",
            CONFIG_DIR,
            "--verbose",
        ],
        cwd=FARM_C_DIR,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"farm-c single failed (exit {proc.returncode})")

    c_days = {}
    c_summary = {}
    for line in proc.stdout.splitlines():
        if line.startswith("day "):
            head, _, rest = line.partition(": ")
            day = int(head[len("day ") :])
            c_days[day] = dict(part.split("=", 1) for part in rest.split(" "))
        elif ": " in line:
            key, _, value = line.partition(": ")
            c_summary[key] = value

    registry, crops, upgrades, config, world = load_python_side(args.days, args.start_money)
    py_days = {}

    def on_day(player):
        weather = player.current_weather
        py_days[player.day] = {
            "money": f"{player.money:.2f}",
            "temperature": f"{weather.get('temperature', 0.0):.2f}",
            "rainfall": f"{weather.get('rainfall', 0.0):.3f}",
            "planted": str(len(player.planted)),
            "inventory": str(len(player.inventory_lots)),
        }

    player = run_python_single(
        registry, crops, upgrades, config, world, args.strategy, args.seed, on_day=on_day
    )

    first_bad = None
    for day in sorted(set(c_days) | set(py_days)):
        c_row = c_days.get(day)
        py_row = py_days.get(day)
        if c_row is None or py_row is None:
            first_bad = day
            side = "farm-c" if py_row is None else "python"
            print(f"day {day}: present in {side} only")
            break
        diffs = [k for k in py_row if c_row.get(k) != py_row[k]]
        if diffs:
            first_bad = day
            print(f"first divergent day: {day}")
            for key in diffs:
                print(f"  {key:<12} C={c_row.get(key)}  python={py_row[key]}")
            break

    if first_bad is None:
        print(f"no divergence in the per-day trace ({len(py_days)} days, money at 2dp)")

    # The final summary farm-c prints *is* full precision (%.17g), so this
    # part of the trace is exact and worth reporting either way.
    exact = {
        "final_money": player.money,
        "revenue": player.total_revenue,
        "expenses": player.total_expenses,
        "lowest_money": player.lowest_money,
        "highest_money": player.highest_money,
    }
    end_diffs = []
    for key, py_value in exact.items():
        if key not in c_summary:
            continue
        if float(c_summary[key]).hex() != float(py_value).hex():
            end_diffs.append(
                f"  {key:<14} C={float(c_summary[key]).hex()}  python={float(py_value).hex()}"
            )
    if end_diffs:
        print("\nfinal state differs (exact):")
        for line in end_diffs:
            print(line)
        return 1
    print("final state: exact match on money/revenue/expenses/lowest/highest")
    return 0 if first_bad is None else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument(
            "--binary", default=DEFAULT_BINARY, help="farm-c binary (default: farm-c/farm-c)"
        )
        p.add_argument(
            "--days",
            type=int,
            default=None,
            help="override simulation_settings.json days on BOTH sides",
        )
        p.add_argument(
            "--start-money", type=float, default=None, help="override start_money on BOTH sides"
        )
        p.add_argument("--max-report", type=int, default=10)

    p_check = sub.add_parser("check", help="run-for-run field parity, farm-c vs Python")
    p_check.add_argument(
        "--runs",
        type=int,
        default=5,
        help="runs per strategy (default 5 = 55 runs, ~1.5s; 50 is still ~15s)",
    )
    p_check.add_argument("--seed", type=int, default=42, help="base seed (default 42)")
    p_check.add_argument("--strategy", action="append", help="restrict to one strategy; repeatable")
    p_check.add_argument("--verbose", action="store_true", help="print passing runs too")
    p_check.add_argument("--progress", action="store_true", help="progress on stderr")
    add_common(p_check)
    p_check.set_defaults(func=cmd_check)

    p_seeds = sub.add_parser("seeds", help="verify seed minting order only (no Python sims)")
    p_seeds.add_argument("--runs", type=int, default=5)
    p_seeds.add_argument("--seed", type=int, default=42)
    p_seeds.add_argument("--strategy", action="append")
    add_common(p_seeds)
    p_seeds.set_defaults(func=cmd_seeds)

    p_trace = sub.add_parser("trace", help="localize a divergence to its first day")
    p_trace.add_argument("strategy")
    p_trace.add_argument("seed", type=int)
    add_common(p_trace)
    p_trace.set_defaults(func=cmd_trace)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
