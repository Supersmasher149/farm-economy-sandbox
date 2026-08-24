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
    python3 .claude/skills/c-parity/scripts/c_parity.py baseline
    python3 .claude/skills/c-parity/scripts/c_parity.py payload STRATEGY SEED --day N
"""

import argparse
import csv
import hashlib
import json
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
# The baseline `farm-c golden capture` writes and `farm-c golden check`
# re-checks without needing Python at all. `baseline` below is the other half
# of that gate: it re-derives the same numbers from the live Python modules,
# so the committed file is a statement about Python's behavior and not just
# about the C agreeing with its own past self.
BASELINE_PATH = os.path.join(FARM_C_DIR, "tests", "golden_baseline.json")

sys.path.insert(0, REPO_ROOT)


# --- the trajectory payload: a byte-for-byte mirror of C -------------------
#
# `farm-c/src/trajectory.c:trajectory_day_payload` builds this same text from
# a `FarmState` at the end of every simulated day, chains a blake2b-8 over it,
# and records the result in farm-c/tests/golden_baseline.json. This function
# builds it from a Python `PlayerState`. The two must agree byte for byte --
# that equality is what makes the committed C baseline a statement about
# *Python's* behavior rather than just about the C's self-consistency, and
# it is checked directly by `check --trajectory`.
#
# Read trajectory.h before editing either side. The two rules that make the
# format reproducible in both languages:
#
#   * Dict-shaped state is emitted sorted by key with default values skipped,
#     because Python's dicts are sparse in a way the C's dense arrays cannot
#     reproduce (markets.py:36 creates a 0.0 `market_supply` entry for every
#     item it loops over; the C cannot tell that from untouched).
#   * Floats are emitted as float.hex() -- exact, and unlike `==` it keeps
#     -0.0 distinct from 0.0.
#
# Anything that could legitimately be 0.0 is in the fixed scalar section
# instead, where it is always emitted.

QUALITY_ORDER = {"rejected": 0, "processing": 1, "standard": 2, "premium": 3}
ITEM_TYPE_ORDER = {"crop": 0, "product": 1}


def _hex(value):
    """float.hex(), which py_float_hex reproduces exactly (bit-pattern based,
    not libc "%a" -- see pyfloat.h)."""
    return float(value).hex()


def _sorted_map(mapping, render, default):
    """Sorted, default-skipped rendering of one dict-shaped field."""
    parts = []
    for key in sorted(mapping):
        value = mapping[key]
        if value == default:
            continue
        parts.append(f" {key}={render(value)}")
    return "".join(parts)


def _optint(label, value):
    return f" {label}={value}" if value is not None else f" {label}=-"


def _day_payload(player) -> str:
    """The exact bytes trajectory.c hashes for this day."""
    weather = player.current_weather or {}
    out = []

    out.append(f"day {player.day}\n")

    season = weather.get("season")
    line = f"weather season={season if season is not None else '-'}"
    if weather:
        line += f" temperature={_hex(weather.get('temperature', 0.0))}"
        line += f" rainfall={_hex(weather.get('rainfall', 0.0))}"
        line += f" evaporation={_hex(weather.get('evaporation', 0.0))}"
    out.append(line + "\n")

    # highest_money stays None-able: economy_rules reads "never sold anything
    # yet" as a real state, so it must not be coerced to 0.0 here.
    highest = "-" if player.highest_money is None else _hex(player.highest_money)
    out.append(
        f"cash money={_hex(player.money)} revenue={_hex(player.total_revenue)}"
        f" expenses={_hex(player.total_expenses)} reputation={_hex(player.reputation)}"
        f" lowest={_hex(player.lowest_money or 0.0)} highest={highest}"
        f" processing_revenue={_hex(player.processing_revenue)}"
        f" contract_penalties={_hex(player.contract_penalties)}"
        f" contract_revenue={_hex(player.revenue_by_channel.get('contract', 0.0))}\n"
    )

    reason = player.bankruptcy_reason if player.bankruptcy_reason is not None else "-"
    out.append(
        f"farm bankrupt={1 if player.bankrupt else 0}"
        + _optint("bankruptcy_day", player.bankruptcy_day)
        + f" reason={reason} slots={player.slots_total} planted={len(player.planted)}"
        f" fertilizer={player.fertilizer_inventory}"
        f" water_units={_hex(player.water_units)}\n"
    )

    out.append(
        f"tally planted={player.total_planted} harvested={player.total_harvested}"
        f" sold={player.total_sold} spoiled={player.total_spoiled}"
        f" processed={player.total_processed} waterings={player.total_waterings}"
        f" harvest_events={player.total_harvest_events} lost={player.total_crops_lost}"
        f" fert_bought={player.total_fertilizer_bought}"
        f" fert_applied={player.total_fertilizer_applied}"
        f" contracts_done={player.contracts_completed}"
        f" contracts_failed={player.contracts_failed} idle={player.idle_days}"
        f" slot_days={player.slot_days} occupied={player.occupied_slot_days}\n"
    )

    out.append("expenses" + _sorted_map(player.expenses_by_category, _hex, 0.0) + "\n")
    out.append("quality" + _sorted_map(player.quality_harvested, str, 0) + "\n")
    out.append("losses" + _sorted_map(player.losses_by_cause, str, 0) + "\n")

    out.append("prices" + _sorted_map(player.market_prices, _hex, 0.0) + "\n")
    out.append("supply" + _sorted_map(player.market_supply, _hex, 0.0) + "\n")
    out.append("seeds" + _sorted_map(player.seed_inventory, str, 0) + "\n")
    out.append("plant_counts" + _sorted_map(player.crop_plant_counts, str, 0) + "\n")
    out.append("buyers" + _sorted_map(player.buyer_relationships, _hex, 0.0) + "\n")

    # The C keeps the "contract" pseudo-channel in its own scalar (emitted on
    # the cash line above), so it is excluded here to match; every remaining
    # key is a real config channel.
    channel_revenue = {
        key: value for key, value in player.revenue_by_channel.items() if key != "contract"
    }
    out.append("channel_revenue" + _sorted_map(channel_revenue, _hex, 0.0) + "\n")
    out.append("channel_used" + _sorted_map(player.channel_capacity_used, str, 0) + "\n")

    upgrades = []
    for upgrade_id in sorted(player.upgrades_owned):
        day = player.upgrade_purchase_days.get(upgrade_id)
        upgrades.append(f" {upgrade_id}@{day if day is not None else '-'}")
    out.append("upgrades" + "".join(upgrades) + "\n")

    # Positional from here down: order is part of the comparison, because a
    # reordered inventory changes which lot a FIFO consume takes.
    for index, lot in enumerate(player.inventory_lots):
        effective = lot.effective_shelf_life_days
        out.append(
            f"lot {index} item={lot.item_id} qty={lot.quantity}"
            f" quality={QUALITY_ORDER[lot.quality]} produced={lot.produced_day}"
            f" age={lot.age_days} shelf={lot.shelf_life_days}"
            + _optint("eff_shelf", effective if effective else None)
            + f" type={ITEM_TYPE_ORDER[lot.item_type]} unit_cost={_hex(lot.unit_cost)}\n"
        )

    for index, job in enumerate(player.processing_jobs):
        out.append(
            f"job {index} recipe={job.recipe_id} out={job.output_item_id}"
            f" qty={job.output_quantity} done={job.completion_day}"
            f" shelf={job.shelf_life_days} unit_cost={_hex(job.unit_cost)}\n"
        )

    for label, contracts in (
        ("active", player.active_contracts),
        ("offer", player.contract_offers),
    ):
        for index, contract in enumerate(contracts):
            out.append(
                f"{label} {index} buyer={contract.buyer_id} item={contract.item_id}"
                f" qty={contract.quantity} delivered={contract.delivered}"
                f" minq={QUALITY_ORDER[contract.min_quality]}"
                f" price={_hex(contract.unit_price)} penalty={_hex(contract.penalty_rate)}"
                f" offered={contract.offered_day} deadline={contract.deadline_day}"
                f" accepted={1 if contract.accepted else 0}"
                f" resolved={1 if contract.resolved else 0}\n"
            )

    for index, crop in enumerate(player.planted):
        plot_index = crop.plot_index if crop.plot_index is not None else -1
        out.append(
            f"crop {index} item={crop.crop_id} planted={crop.day_planted}"
            f" grow={crop.growth_days_required} watered={crop.last_watered_day}"
            f" neglect={crop.neglect_days} fert={1 if crop.fertilized else 0}"
            f" plot={plot_index} accrued={_hex(crop.accrued_cost)}"
            f" water_stress={_hex(crop.water_stress)}"
            f" nutrient_stress={_hex(crop.nutrient_stress)}"
            f" temperature_stress={_hex(crop.temperature_stress)}"
            f" pest_stress={_hex(crop.pest_stress)}"
            f" disease_stress={_hex(crop.disease_stress)}\n"
        )

    # The C stores a plot's crop as an index into its `planted` vector; Python
    # holds the object itself. Recovering the position by identity compares
    # the C's plot<->planted bookkeeping against Python's object graph rather
    # than just re-emitting the crop's fields a second time.
    planted_positions = {id(crop): index for index, crop in enumerate(player.planted)}
    for index, plot in enumerate(player.plots):
        family = plot.previous_crop_family if plot.previous_crop_family is not None else "-"
        crop_index = planted_positions.get(id(plot.crop)) if plot.crop is not None else None
        out.append(
            f"plot {index} moisture={_hex(plot.moisture)} nitrogen={_hex(plot.nitrogen)}"
            f" phosphorus={_hex(plot.phosphorus)} potassium={_hex(plot.potassium)}"
            f" ph={_hex(plot.ph)} soil_health={_hex(plot.soil_health)}"
            f" pest_pressure={_hex(plot.pest_pressure)}"
            f" disease_pressure={_hex(plot.disease_pressure)}"
            f" family={family}" + _optint("crop", crop_index) + "\n"
        )

    return "".join(out)


class Trajectory:
    """Chained blake2b-8 over every day's payload.

    Chained rather than a hash of the concatenation so the first differing
    per-day digest is the first day that actually diverged -- the same
    property `farm-c golden trace` relies on, and the same construction as
    replay-guard's _Trajectory.
    """

    def __init__(self):
        self.running = b""
        self.per_day = []

    def __call__(self, player) -> None:
        payload = _day_payload(player).encode("utf-8")
        self.running = hashlib.blake2b(self.running + payload, digest_size=8).digest()
        self.per_day.append(self.running.hex())

    @property
    def digest(self) -> str:
        return self.running.hex()


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


def python_baseline_record(player, trajectory):
    """The Python-side mirror of src/golden.c:build_record.

    Same field names, same order-independent string forms: floats as
    float.hex(), a missing optional as "-". Anything added there gets added
    here or `baseline` silently stops checking it -- which is why the
    comparison below reports fields present in the baseline but absent here.
    """
    record = {
        "trajectory": trajectory.digest,
        "trajectory_days": str(len(trajectory.per_day)),
        "days_simulated": str(player.day),
        "final_money": _hex(player.money),
        "total_revenue": _hex(player.total_revenue),
        "total_expenses": _hex(player.total_expenses),
        # snapshot_result's literal subtraction, not an equivalent.
        "net_profit": _hex(player.total_revenue - player.total_expenses),
        "lowest_money": _hex(player.lowest_money or 0.0),
        "highest_money": "-" if player.highest_money is None else _hex(player.highest_money),
        "reputation": _hex(player.reputation),
        "contract_penalties": _hex(player.contract_penalties),
        "processing_revenue": _hex(player.processing_revenue),
        "total_planted": str(player.total_planted),
        "total_harvested": str(player.total_harvested),
        "total_sold": str(player.total_sold),
        "total_spoiled": str(player.total_spoiled),
        "total_processed": str(player.total_processed),
        "total_waterings": str(player.total_waterings),
        "total_fertilizer_applied": str(player.total_fertilizer_applied),
        "total_crops_lost": str(player.total_crops_lost),
        "idle_days": str(player.idle_days),
        "contracts_completed": str(player.contracts_completed),
        "contracts_failed": str(player.contracts_failed),
        "bankrupt": "true" if player.bankrupt else "false",
        "bankruptcy_day": "-" if player.bankruptcy_day is None else str(player.bankruptcy_day),
    }
    return record


def cmd_baseline(args):
    """Verify farm-c's committed golden baseline against the Python oracle.

    `farm-c golden check` proves the C still reproduces the committed file.
    This proves the committed file is what *Python* produces -- without it,
    a capture taken from an already-broken C would lock the drift in and
    every later check would happily agree with it.
    """
    path = args.baseline
    if not os.path.exists(path):
        raise SystemExit(f"no baseline at {path} -- run `make golden-capture` in farm-c/")
    with open(path) as handle:
        document = json.load(handle)
    runs = document.get("runs")
    if not isinstance(runs, dict):
        raise SystemExit(f'{path} has no "runs" object')

    meta = document.get("_meta", {})
    print(
        f"{path}: {len(runs)} combos, captured with {meta.get('compiler', '?')} "
        f"on {meta.get('platform', '?')}"
    )

    registry, crops, upgrades, config, world = load_python_side(args.days, args.start_money)

    failures = []
    for index, key in enumerate(sorted(runs)):
        strategy, _, seed_text = key.rpartition(":")
        if strategy not in registry:
            failures.append((key, [f"    unknown strategy {strategy!r} (removed since capture?)"]))
            continue
        trajectory = Trajectory()
        player = run_python_single(
            registry, crops, upgrades, config, world, strategy, int(seed_text), on_day=trajectory
        )
        actual = python_baseline_record(player, trajectory)
        expected = runs[key]

        diffs = []
        for field in sorted(set(expected) | set(actual)):
            want = expected.get(field)
            got = actual.get(field)
            if got is None:
                diffs.append(f"    {field:<24} in baseline but not checked by this script")
            elif want != got:
                diffs.append(f"    {field:<24} baseline={want}  python={got}")
        if diffs:
            failures.append((key, diffs))
            print(f"\nFAIL  {key}")
            for line in diffs:
                print(line)
        elif args.verbose:
            print(f"ok    {key}")

        if args.progress and (index + 1) % 5 == 0:
            sys.stderr.write(f"\r  checked {index + 1}/{len(runs)} combos")
            sys.stderr.flush()
    if args.progress:
        sys.stderr.write("\r" + " " * 40 + "\r")

    print(f"\n{len(runs)} combos checked, {len(failures)} diverged")
    if failures:
        print("BASELINE: FAIL")
        script = os.path.relpath(os.path.abspath(__file__), REPO_ROOT)
        strategy, _, seed_text = failures[0][0].rpartition(":")
        print(
            f"\nIf only `trajectory` differs, the two agree on the final tally but took\n"
            f"different routes. Bisect it to a day:\n"
            f"  cd farm-c && ./farm-c golden trace {strategy} {seed_text}\n"
            f"then diff the exact hashed bytes for the first differing day N:\n"
            f"  cd farm-c && ./farm-c golden payload {strategy} {seed_text} --day N\n"
            f"  python3 {script} payload {strategy} {seed_text} --day N"
        )
        return 1
    print("BASELINE: OK -- the committed C baseline is what Python produces")
    return 0


def cmd_payload(args):
    """Print Python's exact hashed bytes for one day.

    The counterpart to `farm-c golden payload`; diff the two to turn a
    digest mismatch into a one-line difference.
    """
    resolve_strategies([args.strategy])
    registry, crops, upgrades, config, world = load_python_side(args.days, args.start_money)
    captured = []

    def on_day(player):
        if len(captured) < args.day:
            captured.append(_day_payload(player))

    run_python_single(
        registry, crops, upgrades, config, world, args.strategy, args.seed, on_day=on_day
    )
    if len(captured) < args.day:
        raise SystemExit(f"day {args.day} out of range (run simulated {len(captured)} days)")
    sys.stdout.write(captured[args.day - 1])
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

    p_baseline = sub.add_parser(
        "baseline", help="verify farm-c's committed golden baseline against Python"
    )
    p_baseline.add_argument("--baseline", default=BASELINE_PATH)
    p_baseline.add_argument("--verbose", action="store_true", help="print passing combos too")
    p_baseline.add_argument("--progress", action="store_true", help="progress on stderr")
    add_common(p_baseline)
    p_baseline.set_defaults(func=cmd_baseline)

    p_payload = sub.add_parser("payload", help="print Python's hashed bytes for one day")
    p_payload.add_argument("strategy")
    p_payload.add_argument("seed", type=int)
    p_payload.add_argument("--day", type=int, required=True)
    add_common(p_payload)
    p_payload.set_defaults(func=cmd_payload)

    p_trace = sub.add_parser("trace", help="localize a divergence to its first day")
    p_trace.add_argument("strategy")
    p_trace.add_argument("seed", type=int)
    add_common(p_trace)
    p_trace.set_defaults(func=cmd_trace)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
