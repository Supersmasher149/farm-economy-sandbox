#!/usr/bin/env python3
"""Local, offline search for config/*.json changes that reduce balance
warnings -- coordinate hill-climbing, using the simulator itself as the
fitness function. No network calls, no LLM, nothing cloud-based.

**Propose-only: this tool never writes to config/*.json.** It searches
in-memory copies of the loaded config, then writes a report to --output-dir
(default reports/auto_balance/) for a human to review and hand-apply -- the
same human-in-the-loop boundary `.claude/skills/balance-check` already keeps
for the manual workflow. There is no --apply flag; no code path in this file
opens a config/*.json file for writing.

The report's "Proposed Diffs" table is a structural baseline -> final-best
diff (one final value per path, complete, never truncated), so applying it
reproduces exactly the configuration that was scored. The accepted-move
history is a separate diagnostics section: those rows are sequential
transitions through the search, and reconstructing a config from a sorted,
truncated subset of them yields something that was never evaluated.

Algorithm: pick a knob (a numeric leaf discovered by walking crops/upgrades/
world), try current +/- a step, keep the candidate if it improves a
continuous "how balanced" score built from metrics.warnings' own
DEFAULT_THRESHOLDS, move to the next knob. On reaching a local optimum,
reshuffle knob order, shrink the step, and resume from the best config found
so far (basin-hopping restarts, not from-scratch ones -- the starting config
is already human-tuned, so re-exploring the whole space from zero each
restart would waste most of the budget). Every candidate evaluation across a
whole search shares one pinned --eval-seed, so score deltas between
candidates are a paired comparison (low variance) even at a small
--runs-per-candidate -- exactly the "same seed while isolating the effect of
one change" property CLAUDE.md's own balance-testing workflow relies on. The
single best candidate found is re-run once at --final-runs under a fresh,
unseeded batch as a confirmation step, mirroring that workflow's own final
unseeded run.

Usage:
    python3 tools/auto_balance.py                       # defaults, ~a few minutes
    python3 tools/auto_balance.py --iterations 150 --restarts 6 --strategies all
    python3 tools/auto_balance.py --files soil.json fertilizer.json
    python3 tools/auto_balance.py --exclude-path soil.json.dynamics

Output: reports/auto_balance/report.md (human-readable, opens with a
## Warnings section in the exact bullet format evaluate_warnings produces,
so `.claude/skills/balance-check/scripts/report_diff.py` reads it too) and
reports/auto_balance/proposed_diffs.json (machine-readable).
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from main import AGENT_REGISTRY, load_config  # noqa: E402
from metrics.aggregate_results import BatchAggregator  # noqa: E402
from metrics.economics_audit import build_economics_audit  # noqa: E402
from metrics.warnings import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    evaluate_warnings,
    runaway_money_multiple,
)
from runner.batch_run import resolve_base_seed, run_batch  # noqa: E402
from simulation.configuration import (  # noqa: E402
    SOIL_DYNAMICS_BOUNDS,
    validate,
    validate_simulation_config,
)

DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "reports", "auto_balance")

# Keys whose subtree is never a tuning knob: identity fields, human text, and
# enum-typed fields that select a code path rather than scale a magnitude.
# `id`/`type` incidentally also excludes crop.unlock_requirement.id/.type and
# every market-channel/recipe id; `min_quality` excludes the quality enum
# wherever it appears (buyers, channels, recipes) -- one flat set covers
# every structural field across config/*.json.
EXCLUDED_KEYS = frozenset(
    {"id", "name", "description", "type", "role", "family", "min_quality", "seed"}
)

# world[key] -> the config/*.json file it was loaded from (see
# main.load_config). crops/upgrades/simulation_settings aren't under world at
# all, so they get their own root names below.
WORLD_FILES = {
    "watering": "watering_settings.json",
    "fertilizer": "fertilizer.json",
    "soil": "soil.json",
    "weather": "weather.json",
    "markets": "markets.json",
    "storage": "storage.json",
    "contracts": "contracts.json",
    "buyers": "buyers.json",
    "processing": "processing.json",
}
ROOT_FILES = {
    "crops": "crops.json",
    "upgrades": "upgrades.json",
    "config": "simulation_settings.json",
    **WORLD_FILES,
}

# Soft weight on economics_audit's static profit-per-cycle check: a large
# fixed cost per crop pushed into the red, not a hard reject, since an
# intentionally-loss-leading early crop can still be net-positive for the
# economy as a whole.
ECON_PENALTY_WEIGHT = 10.0
EPSILON = 1e-6

# A deliberately smaller-than-"everything" default roster: one strategy per
# warning family the objective scores (bankruptcy, dominant/runaway economy,
# crop loss, both upgrade-timing checks), so a default run finishes in
# minutes rather than the ~11x-longer cost of every registered strategy.
# --strategies all opts into the full roster.
DEFAULT_STRATEGIES = (
    "reckless_spender",
    "profit_optimizer",
    "neglectful_grower",
    "no_upgrade_player",
    "upgrade_rusher",
)


# --------------------------------------------------------------------------
# Config tree access. Every candidate is looked up and mutated by path from
# scratch rather than through a closure bound to a particular object, so
# there is nothing to go stale when a candidate is built fresh (see Knob and
# make_candidate below).
# --------------------------------------------------------------------------


def get_root(baseline, root):
    crops, upgrades, config, world = baseline
    if root == "crops":
        return crops
    if root == "upgrades":
        return upgrades
    if root == "config":
        return config
    return world[root]


def get_at(obj, path):
    for key in path:
        obj = obj[key]
    return obj


def set_at(obj, path, value):
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value


@dataclass(frozen=True)
class Knob:
    root: str
    path: tuple
    file: str
    display: str
    baseline_value: float
    is_int: bool
    lo: float
    hi: float

    @property
    def full_path(self) -> str:
        sep = "" if self.display.startswith("[") else "."
        return f"{self.file}{sep}{self.display}"

    def scale(self) -> float:
        """Half-width to derive a mutation step from. Falls back to the
        baseline's own magnitude when a bound is unbounded on one side (e.g.
        disease_growth_per_rainfall has no upper bound in
        SOIL_DYNAMICS_BOUNDS)."""
        if math.isfinite(self.lo) and math.isfinite(self.hi):
            return (self.hi - self.lo) / 2
        return abs(self.baseline_value) if self.baseline_value else 1.0


def _walk_numeric(node, path):
    """Yield (path, value) for every int/float leaf not under an excluded
    key. `type(node) in (int, float)` -- not isinstance -- is what keeps
    bool out: isinstance(True, int) is True in Python, so an isinstance
    check would silently treat flags as tunable numbers."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in EXCLUDED_KEYS:
                continue
            yield from _walk_numeric(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_numeric(value, path + (index,))
    elif type(node) in (int, float):
        yield path, node


def _display_path(root_obj, path) -> str:
    """Human-readable path, using a list-of-dicts element's own `id` instead
    of its numeric index wherever one is available."""
    parts = []
    node = root_obj
    for key in path:
        if isinstance(node, list) and isinstance(key, int):
            item = node[key]
            ident = item.get("id") if isinstance(item, dict) else None
            parts.append(f"[{ident}]" if isinstance(ident, str) else f"[{key}]")
        else:
            parts.append(f".{key}")
        node = node[key]
    return "".join(parts).lstrip(".")


def _bounds_for(root, root_obj, path, value, bound_pct):
    if (
        root == "soil"
        and len(path) >= 2
        and path[0] == "dynamics"
        and path[1] in SOIL_DYNAMICS_BOUNDS
    ):
        lo, hi = SOIL_DYNAMICS_BOUNDS[path[1]]
        return (float("-inf") if lo is None else lo, float("inf") if hi is None else hi)

    if root == "upgrades" and len(path) == 3 and path[1:] == ("effect", "amount"):
        effect = root_obj[path[0]]["effect"]
        if effect.get("type") == "growth_time_reduction":
            # configuration.py enforces an exclusive [0, 1) bound here; 0.999
            # is a practical stand-in validate() still accepts at the edge.
            band = abs(value) * bound_pct
            return max(0.0, value - band), 0.999

    band = abs(value) * bound_pct
    if band == 0:
        # A zero baseline would otherwise freeze the knob at a zero-width
        # band -- fall back to a small absolute one instead.
        band = 1 if isinstance(value, int) else 0.05
    lo, hi = value - band, value + band
    if value >= 0:
        lo = max(0.0, lo)
    else:
        hi = min(0.0, hi)
    return lo, hi


def discover_knobs(baseline, bound_pct: float, files: set | None = None) -> list[Knob]:
    crops, upgrades, config, world = baseline
    roots = [("crops", crops), ("upgrades", upgrades), ("config", config)]
    roots.extend((key, world[key]) for key in world)

    knobs = []
    for root, root_obj in roots:
        file = ROOT_FILES[root]
        if files is not None and file not in files:
            continue
        for path, value in _walk_numeric(root_obj, ()):
            lo, hi = _bounds_for(root, root_obj, path, value, bound_pct)
            knobs.append(
                Knob(
                    root=root,
                    path=path,
                    file=file,
                    display=_display_path(root_obj, path),
                    baseline_value=value,
                    is_int=isinstance(value, int),
                    lo=lo,
                    hi=hi,
                )
            )
    return knobs


def make_candidate(baseline, knob: Knob, new_value):
    """A brand-new object graph, never a mutated-in-place reuse of an object
    already run through run_batch. simulation/derived.py caches derived
    indexes (CropProfile, WorldLookups, WeatherParams, market profiles) keyed
    by id(config_object), not content -- a candidate that shared an object
    identity with something already primed into that cache would silently
    have its mutation ignored. copy.deepcopy() of the whole 4-tuple gives
    every nested dict/list a fresh id(), which is what makes this safe."""
    crops, upgrades, config, world = copy.deepcopy(baseline)
    candidate = (crops, upgrades, config, world)
    set_at(get_root(candidate, knob.root), knob.path, new_value)
    return candidate


# --------------------------------------------------------------------------
# Objective: metrics.warnings' own DEFAULT_THRESHOLDS, as continuous
# distance-past-threshold terms instead of booleans, so hill-climbing has
# gradient before a threshold is actually crossed.
# --------------------------------------------------------------------------


def _term(value: float, threshold: float, direction: str) -> float:
    if threshold == 0:
        return 0.0
    if direction == "above":
        return max(0.0, value - threshold) / abs(threshold)
    return max(0.0, threshold - value) / abs(threshold)


def continuous_penalty(stats: dict, config: dict, thresholds: dict) -> float:
    """Same six rules evaluate_warnings checks, scored continuously."""
    score = 0.0
    for pct in stats["crop_usage_pct"].values():
        score += _term(pct, thresholds["dominant_crop_pct"], "above")
        score += _term(pct, thresholds["dead_crop_pct"], "below")

    score += _term(stats["bankruptcy_rate"], thresholds["high_bankruptcy_pct"], "above")

    first_day = stats["avg_first_upgrade_day"]
    if first_day is not None:
        score += _term(first_day, thresholds["upgrade_too_fast_day"], "below")

    max_unreached_rate = thresholds["upgrade_too_slow_fraction"] * 100
    score += _term(stats["first_upgrade_rate"], 100 - max_unreached_rate, "below")

    multiple = runaway_money_multiple(config, thresholds)
    threshold_money = config["start_money"] * multiple
    score += _term(stats["avg_final_money"], threshold_money, "above")

    # None means no run in this cohort ever had a harvest event to measure
    # loss against (see metrics/aggregate_results.py) -- nothing to score,
    # not an implicit 0% loss.
    crop_loss_rate = stats["avg_crop_loss_rate"]
    if crop_loss_rate is not None:
        score += _term(crop_loss_rate, thresholds["high_crop_loss_rate_pct"], "above")
    return score


def warning_score(summary: dict, config: dict, thresholds: dict) -> float:
    return sum(continuous_penalty(stats, config, thresholds) for stats in summary.values())


def economics_penalty(crops, fertilizer_config, market_config) -> float:
    audit = build_economics_audit(crops, fertilizer_config, market_config)
    return sum(max(0.0, -c["nominal_profit_per_cycle"]) for c in audit["crops"])


def score_candidate(baseline, agents, num_runs, eval_seed, thresholds):
    """Cheapest-check-first: validate() rejects an invalid candidate in
    microseconds without ever spending a batch run on it."""
    crops, upgrades, config, world = baseline
    try:
        validate(crops, upgrades, world)
        validate_simulation_config(config)
    except ValueError:
        return float("inf"), None

    econ_penalty = economics_penalty(crops, world["fertilizer"], world["markets"])

    aggregator = BatchAggregator()
    for result in run_batch(
        config,
        agents,
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        num_runs=num_runs,
        base_seed=eval_seed,
        world=world,
        # A fresh ProcessPoolExecutor per run_batch call would dominate wall
        # time at this num_runs -- parallelism is worth it once, for the
        # final confirmation batch, not hundreds of times in the search loop.
        workers=1,
    ):
        aggregator.add(result)
    summary = aggregator.finalize()
    score = warning_score(summary, config, thresholds) + ECON_PENALTY_WEIGHT * econ_penalty
    return score, summary


# --------------------------------------------------------------------------
# Coordinate hill-climbing with basin-hopping restarts.
# --------------------------------------------------------------------------


@dataclass
class Move:
    iteration: int
    knob: Knob
    old_value: float
    new_value: float
    score_before: float
    score_after: float


def clamp(value, lo, hi):
    return min(max(value, lo), hi)


def search(baseline, knobs, agents, args, thresholds, rng):
    current = baseline
    current_score, current_summary = score_candidate(
        current, agents, args.runs_per_candidate, args.eval_seed, thresholds
    )
    baseline_score = current_score
    moves: list[Move] = []
    step_fraction = args.step_fraction
    budget_per_restart = max(1, args.iterations // (args.restarts + 1))
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    total_visited = 0

    for _restart in range(args.restarts + 1):
        order = list(range(len(knobs)))
        rng.shuffle(order)
        visited_this_restart = 0
        for idx in order:
            if visited_this_restart >= budget_per_restart:
                break
            if deadline and time.monotonic() >= deadline:
                break
            knob = knobs[idx]
            visited_this_restart += 1
            total_visited += 1

            step_size = knob.scale() * step_fraction
            if step_size <= 0:
                continue
            current_value = get_at(get_root(current, knob.root), knob.path)

            for direction in (1, -1):
                new_value = clamp(current_value + direction * step_size, knob.lo, knob.hi)
                if knob.is_int:
                    new_value = int(round(new_value))
                if new_value == current_value:
                    continue
                candidate = make_candidate(current, knob, new_value)
                cand_score, cand_summary = score_candidate(
                    candidate, agents, args.runs_per_candidate, args.eval_seed, thresholds
                )
                if cand_score < current_score - EPSILON:
                    moves.append(
                        Move(
                            len(moves) + 1,
                            knob,
                            current_value,
                            new_value,
                            current_score,
                            cand_score,
                        )
                    )
                    current, current_score, current_summary = candidate, cand_score, cand_summary
                    break  # short-circuit: don't spend a run trying the other direction

        step_fraction *= args.step_decay
        if deadline and time.monotonic() >= deadline:
            break

    return current, current_score, current_summary, moves, total_visited, baseline_score


def confirm(best, all_agents, args, thresholds):
    """Re-run the winning candidate once, at --final-runs, under a fresh
    unseeded batch -- the "confirm it holds under randomized conditions"
    step from CLAUDE.md's balance-testing workflow, applied to the search's
    own output before it's reported as trustworthy. Uses every registered
    strategy regardless of --strategies, so a config tuned against a subset
    still gets checked against the ones it wasn't optimized for."""
    crops, upgrades, config, world = best
    final_seed = resolve_base_seed(None)
    aggregator = BatchAggregator()
    for result in run_batch(
        config,
        all_agents,
        crops,
        upgrades,
        world["watering"],
        world["fertilizer"],
        num_runs=args.final_runs,
        base_seed=final_seed,
        world=world,
    ):
        aggregator.add(result)
    summary = aggregator.finalize()
    return {
        "base_seed": final_seed,
        "runs": args.final_runs,
        "warning_score": round(warning_score(summary, config, thresholds), 4),
        "warnings": evaluate_warnings(summary, config, thresholds),
    }


# --------------------------------------------------------------------------
# Reporting.
# --------------------------------------------------------------------------


def _round_val(value):
    return round(value, 6) if isinstance(value, float) else value


def build_config_diff(baseline, best, knobs, moves):
    """The actual baseline -> final-best difference, one entry per changed path.

    NOT the accepted-move list. A hill-climb revisits knobs: the same path
    can move several times (so the move list holds stale intermediate values
    for it), and can move back to where it started (so it appears in the move
    list while the winning config is unchanged there). Sorting those
    transitions by individual score delta and truncating them, then telling a
    human to apply the survivors, produced a config that was never evaluated
    and did not have the reported final score (CQ-05).

    Reading the final config back out per knob has neither failure mode: each
    path appears at most once, with the value the confirmation run actually
    scored. Only knobs the search could touch are compared, which is exactly
    the set `search` can mutate.
    """
    moves_per_path: dict[tuple, int] = {}
    for move in moves:
        key = (move.knob.root, move.knob.path)
        moves_per_path[key] = moves_per_path.get(key, 0) + 1

    diffs = []
    for knob in knobs:
        old_value = get_at(get_root(baseline, knob.root), knob.path)
        new_value = get_at(get_root(best, knob.root), knob.path)
        if old_value == new_value:
            continue
        diffs.append(
            {
                "file": knob.file,
                "path": knob.display,
                "old_value": _round_val(old_value),
                "new_value": _round_val(new_value),
                # >1 means the search revisited this knob; the move history
                # below shows the intermediate steps it took.
                "moves_applied": moves_per_path.get((knob.root, knob.path), 0),
            }
        )
    diffs.sort(key=lambda d: (d["file"], d["path"]))
    return diffs


def build_move_history(moves, top_n):
    """Accepted search transitions, ranked by individual score improvement.

    Diagnostics only -- see `build_config_diff` for why this must never be
    presented as the change set to apply. Truncation is safe here precisely
    because nothing is meant to be reconstructed from it.
    """
    history = sorted(
        (
            {
                "rank": 0,
                "file": m.knob.file,
                "path": m.knob.display,
                "old_value": _round_val(m.old_value),
                "new_value": _round_val(m.new_value),
                "score_before": round(m.score_before, 4),
                "score_after": round(m.score_after, 4),
                "score_delta": round(m.score_after - m.score_before, 4),
                "iteration": m.iteration,
            }
            for m in moves
        ),
        key=lambda d: d["score_delta"],
    )
    for rank, entry in enumerate(history, start=1):
        entry["rank"] = rank
    return history[:top_n]


def build_diffs_payload(
    args,
    strategy_names,
    tuner_seed,
    baseline_score,
    best_score,
    moves,
    confirmation,
    baseline,
    best,
    knobs,
):
    diffs = build_config_diff(baseline, best, knobs, moves)
    return {
        "eval_seed": args.eval_seed,
        "tuner_seed": tuner_seed,
        "runs_per_candidate": args.runs_per_candidate,
        "strategies": strategy_names,
        "baseline_score": round(baseline_score, 4),
        "final_score": round(best_score, 4),
        "total_moves": len(moves),
        # Complete and never truncated: this is the config that scored
        # `final_score`, so applying a subset of it means something else.
        "diffs": diffs,
        "move_history": build_move_history(moves, args.top_n),
        "confirmation": confirmation,
    }


def render_report(payload: dict) -> str:
    lines = ["# Auto-balance search report", ""]

    lines.append("## Warnings")
    warnings = payload["confirmation"]["warnings"]
    lines.extend(f"- ⚠️ {w}" for w in warnings) if warnings else lines.append("(none)")
    lines.append("")
    lines.append(
        f"Confirmation run: {payload['confirmation']['runs']} runs/strategy across every "
        f"registered strategy, base_seed {payload['confirmation']['base_seed']}, "
        f"warning score {payload['confirmation']['warning_score']}."
    )
    lines.append("")

    lines.append("## Proposed Diffs")
    lines.append(
        f"Search (strategies: {', '.join(payload['strategies'])}, eval_seed "
        f"{payload['eval_seed']}): baseline score {payload['baseline_score']} -> "
        f"final score {payload['final_score']} over {payload['total_moves']} accepted "
        f"moves, landing on {len(payload['diffs'])} changed setting(s)."
    )
    lines.append("")
    lines.append(
        "This is the complete difference between the checked-in config and the "
        "configuration that scored `final_score` -- one final value per path, in "
        "file order. It is not a ranked shortlist: **apply all of it or none of "
        "it**, since a subset is a configuration this search never evaluated."
    )
    lines.append("")
    if payload["diffs"]:
        lines.append("| file | path | old -> new | times tuned |")
        lines.append("|---|---|---|---|")
        for d in payload["diffs"]:
            lines.append(
                f"| {d['file']} | {d['path']} | "
                f"{d['old_value']} -> {d['new_value']} | {d['moves_applied']} |"
            )
    else:
        lines.append("No improving moves found within the search budget.")
    lines.append("")

    lines.append("## Search Diagnostics")
    lines.append(
        f"The {len(payload['move_history'])} highest-scoring of "
        f"{payload['total_moves']} accepted moves, as *sequential transitions* "
        "during the hill climb. A path can appear more than once here, and an "
        "`old -> new` pair may be an intermediate step the final config has since "
        "moved past. Use this to see where the score came from -- never as a "
        "change set to apply; that is the table above."
    )
    lines.append("")
    if payload["move_history"]:
        lines.append("| rank | iteration | file | path | old -> new | score delta |")
        lines.append("|---|---|---|---|---|---|")
        for d in payload["move_history"]:
            lines.append(
                f"| {d['rank']} | {d['iteration']} | {d['file']} | {d['path']} | "
                f"{d['old_value']} -> {d['new_value']} | {d['score_delta']:+.4f} |"
            )
    else:
        lines.append("(no accepted moves)")
    lines.append("")

    lines.append("## How to apply")
    lines.append(
        "This tool never writes to config/*.json. Copy every row of the Proposed "
        "Diffs table into the corresponding config/*.json file by hand, then "
        "re-run the balance-check workflow "
        "(`python3 main.py batch --runs 1000 --seed <fixed-seed>`) to confirm the "
        "effect under the standard workflow.\n\n"
        "Apply and re-check any `soil.json` diffs one at a time rather than all "
        "together: `docs/design/2026-08-04-balance-fix-design.md` and "
        "`2026-08-05-soil-regen-and-reserve-fix.md` document that block as "
        "historically under-constrained, with nonobvious interaction effects. "
        "Note that partial application means the reported `final_score` no longer "
        "applies -- re-check whatever subset you land on."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search config/*.json's numeric knobs for changes that reduce balance "
            "warnings, using the simulator as the fitness function. Propose-only: "
            "never writes config/*.json."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=40,
        help="hill-climb knob visits across all restarts (default 40)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="tuner RNG seed for restart/knob order (default: drawn fresh)",
    )
    parser.add_argument(
        "--eval-seed",
        type=int,
        default=42,
        help="base_seed reused for every candidate's search-phase batch (default 42; pins comparisons)",
    )
    parser.add_argument(
        "--runs-per-candidate",
        type=int,
        default=20,
        help="runs/strategy per candidate during search (default 20)",
    )
    parser.add_argument(
        "--restarts",
        type=int,
        default=3,
        help="basin-hopping restarts after a local optimum (default 3)",
    )
    parser.add_argument(
        "--step-fraction",
        type=float,
        default=0.15,
        help="initial mutation step, as a fraction of each knob's bound half-width (default 0.15)",
    )
    parser.add_argument(
        "--step-decay",
        type=float,
        default=0.6,
        help="step-fraction multiplier applied after each restart (default 0.6)",
    )
    parser.add_argument(
        "--bound-pct",
        type=float,
        default=0.4,
        help="generic +/-fraction band for knobs with no explicit bounds table entry (default 0.4)",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        default=None,
        metavar="FILE",
        help="restrict eligible knobs to these config/*.json filenames",
    )
    parser.add_argument(
        "--exclude-path",
        nargs="+",
        default=(),
        metavar="PATH",
        help="exclude knobs whose full path (e.g. soil.json.dynamics) starts with one of these",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=None,
        metavar="NAME",
        help=f"strategies whose warnings drive the search (default: {' '.join(DEFAULT_STRATEGIES)}); pass 'all' for every registered strategy",
    )
    parser.add_argument(
        "--final-runs",
        type=int,
        default=500,
        help="confirmation batch size on the best candidate found (default 500)",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=None,
        help="optional wall-clock safety valve for the search phase",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"where to write report.md / proposed_diffs.json (default {os.path.relpath(DEFAULT_OUTPUT_DIR, REPO_ROOT)})",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=15,
        help=(
            "how many accepted moves to list in the report's Search Diagnostics "
            "section (default 15); the Proposed Diffs table is always complete"
        ),
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    thresholds = DEFAULT_THRESHOLDS

    crops, upgrades, config, world = load_config()
    baseline = (crops, upgrades, config, world)

    if args.strategies == ["all"]:
        strategy_names = list(AGENT_REGISTRY)
    else:
        strategy_names = list(args.strategies) if args.strategies else list(DEFAULT_STRATEGIES)
    unknown = set(strategy_names) - set(AGENT_REGISTRY)
    if unknown:
        raise SystemExit(f"Unknown strategies: {sorted(unknown)}")
    search_agents = [AGENT_REGISTRY[name]() for name in strategy_names]
    all_agents = [cls() for cls in AGENT_REGISTRY.values()]

    knobs = discover_knobs(baseline, args.bound_pct, files=set(args.files) if args.files else None)
    if args.exclude_path:
        knobs = [k for k in knobs if not any(k.full_path.startswith(p) for p in args.exclude_path)]
    if not knobs:
        raise SystemExit("No tunable knobs discovered -- check --files/--exclude-path.")

    tuner_seed = args.seed if args.seed is not None else resolve_base_seed(None)
    rng = random.Random(tuner_seed)

    print(
        f"auto_balance: {len(knobs)} knobs, {len(search_agents)} strategies "
        f"({', '.join(strategy_names)}), eval_seed={args.eval_seed}, tuner_seed={tuner_seed}"
    )

    best, best_score, _, moves, visited, baseline_score = search(
        baseline, knobs, search_agents, args, thresholds, rng
    )
    print(
        f"search done: {visited} knob-visits, {len(moves)} accepted moves, "
        f"score {baseline_score:.4f} -> {best_score:.4f}"
    )

    print(
        f"confirmation run: {args.final_runs} runs/strategy across all {len(all_agents)} strategies..."
    )
    confirmation = confirm(best, all_agents, args, thresholds)

    payload = build_diffs_payload(
        args,
        strategy_names,
        tuner_seed,
        baseline_score,
        best_score,
        moves,
        confirmation,
        baseline=baseline,
        best=best,
        knobs=knobs,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    diffs_path = os.path.join(args.output_dir, "proposed_diffs.json")
    report_path = os.path.join(args.output_dir, "report.md")
    with open(diffs_path, "w") as f:
        json.dump(payload, f, indent=2)
    with open(report_path, "w") as f:
        f.write(render_report(payload))

    print(f"\nWrote {diffs_path}")
    print(f"Wrote {report_path}")
    print(
        "\nconfig/*.json was not modified -- hand-apply the full Proposed Diffs "
        "table from the report, then re-run the balance-check workflow to confirm."
    )
    if confirmation["warnings"]:
        print("\nConfirmation warnings:")
        for w in confirmation["warnings"]:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
