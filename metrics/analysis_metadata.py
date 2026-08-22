"""Provenance for one published analysis: everything needed to re-run it.

Plan Section 9's requirement, in one place. The test a field has to pass to
belong here is "could a reader reproduce this number, or correctly refuse to
trust it, without this?" -- which is why the interpreter version and the git
dirty flag are in and, say, the hostname is not.

Two rules the contents follow:

* **Sufficient statistics, never pickles.** Accumulator state is published as
  counts and moments (`metrics/inference.py:MomentAccumulator.snapshot`), not
  as serialized Python objects, so an artifact stays readable by a later
  version of this code and cannot execute anything on load.
* **Derivation rules, not just values.** The per-run seed rule is recorded as
  the named sampling plan, so a reader can re-derive any run's seed rather
  than having to trust a list of 11,000 integers.
"""

import os
import platform
import subprocess
import sys
import time

from metrics.comparisons import COMPARISON_VERSION
from metrics.distributions import EMPIRICAL_QUANTILE_CONVENTION, SKEWNESS_CONVENTION
from metrics.estimands import ESTIMAND_REGISTRY_VERSION
from metrics.inference import INFERENCE_VERSION

ANALYSIS_METADATA_SCHEMA = "farm-analysis-metadata-v1"

# The simulator's RNG identity. Recorded because every seed in this document
# is meaningless without it: `random.Random` is MT19937 and CPython's
# `getrandbits` layout is what farm-c's `src/rng.c` reproduces bit for bit.
RNG_ALGORITHM = "mt19937_cpython_random_random"


def _git_state(repo_dir: str) -> dict:
    """Commit and dirty-tree state, or a recorded reason it is unavailable.

    A batch run from a tarball with no `.git` is a normal thing to do; the
    field says so explicitly instead of silently reporting a null commit that
    reads like a clean checkout.
    """

    def _run(args):
        return subprocess.run(
            args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    try:
        head = _run(["git", "rev-parse", "HEAD"])
        if head.returncode != 0:
            return {"available": False, "reason": head.stderr.strip() or "not a git repository"}
        status = _run(["git", "status", "--porcelain"])
        return {
            "available": True,
            "commit": head.stdout.strip(),
            "dirty": bool(status.stdout.strip()),
            "dirty_files": len([line for line in status.stdout.splitlines() if line.strip()]),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}


def _accelerator_status() -> dict:
    """Which optional accelerators were actually in play.

    Both change *performance* only and are verified bit-exact, but a report
    that cannot say which build produced it cannot be used to investigate a
    suspected accelerator bug.
    """
    status = {"fastplot": False, "compiled": os.environ.get("FARM_COMPILED") or "off"}
    try:
        from simulation import weather

        status["fastplot"] = bool(getattr(weather, "_fastplot", None))
    except Exception:  # pragma: no cover - accelerator is optional by design
        status["fastplot"] = False
    return status


def build(
    *,
    base_seed,
    sampling_plan: dict,
    requested_runs: int,
    realized_runs: int,
    strategies: list,
    config: dict,
    confidence: float,
    analysis_seed=None,
    bootstrap_replications: int | None = None,
    correction_method: str | None = None,
    stopping: dict | None = None,
    stop_reason: str | None = None,
    unmet_criteria=None,
    uncertainty_spec: dict | None = None,
    started_at: float | None = None,
    finished_at: float | None = None,
    repo_dir: str | None = None,
    artifacts: list | None = None,
    accumulator_state: dict | None = None,
    extra: dict | None = None,
) -> dict:
    started_at = started_at if started_at is not None else time.time()
    finished_at = finished_at if finished_at is not None else time.time()
    repo_dir = repo_dir or os.path.dirname(os.path.abspath(__file__))
    document = {
        "schema": ANALYSIS_METADATA_SCHEMA,
        "estimand_registry_version": ESTIMAND_REGISTRY_VERSION,
        "inference_version": INFERENCE_VERSION,
        "comparison_version": COMPARISON_VERSION,
        "seed": {
            "base_seed": base_seed,
            "sampling_plan": sampling_plan,
            "per_run_seed_rule": sampling_plan.get("plan"),
            "rng_algorithm": RNG_ALGORITHM,
            "analysis_seed": analysis_seed,
            "analysis_rng": "blake2b-derived random.Random, independent of the simulation stream",
        },
        "sampling": {
            "requested_runs_per_strategy": requested_runs,
            "realized_runs_per_strategy": realized_runs,
            "strategies": list(strategies),
            "total_runs": realized_runs * len(strategies),
            "stopping": stopping or {"mode": "fixed"},
            "stop_reason": stop_reason or "fixed_sample",
            "unmet_criteria": list(unmet_criteria or []),
        },
        "inference": {
            "confidence": confidence,
            "mean_interval_method": "student_t",
            "proportion_interval_method": "wilson",
            "quantile_interval_method": "percentile_bootstrap",
            "quantile_convention": EMPIRICAL_QUANTILE_CONVENTION,
            "skewness_convention": SKEWNESS_CONVENTION,
            "bootstrap_replications": bootstrap_replications,
            "multiple_comparison_method": correction_method,
        },
        "configuration": {
            "days": config.get("days"),
            "start_money": config.get("start_money"),
            "start_slots": config.get("start_slots"),
            "snapshot_artifact": "config_snapshot.json",
        },
        "uncertainty_specification": uncertainty_spec,
        "environment": {
            "python_version": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "accelerators": _accelerator_status(),
        },
        "provenance": {
            "git": _git_state(repo_dir),
            "command": " ".join(sys.argv),
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "duration_seconds": round(finished_at - started_at, 3),
        },
        "artifacts": list(artifacts or []),
        "accumulator_state": accumulator_state or {},
    }
    if extra:
        document.update(extra)
    return document


def _iso(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))
