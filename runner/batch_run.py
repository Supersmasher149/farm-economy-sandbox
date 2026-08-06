"""Run the same economy configuration many times per strategy.

A single base_seed drives a dedicated RNG that mints one per-run seed for
each simulation, so a whole batch is itself reproducible from one seed
while every individual run still has its own recorded seed.
Verbose per-run history is disabled here by default for batch performance.

Runs are executed across a process pool by default: each (agent, run_seed)
pair is independent, so batches split cleanly across cores. Seed generation
stays single-threaded and sequential so a given base_seed always produces
the same set of per-run seeds regardless of worker count.

The shared config (crops, upgrades, settings, world) is sent to each worker
process once via a pool initializer rather than being re-pickled on every
task, and tasks are dispatched in chunks rather than one at a time -- both
matter because an individual simulated run is cheap, so per-task IPC
overhead would otherwise dominate.

run_batch is a generator: it streams one RunResult at a time instead of
materializing the whole batch, so memory stays bounded for multi-million-run
batches. It also does no work at all until first iterated (that's how Python
generator functions work) -- every current caller iterates immediately after
calling it, so this is safe, but it's a real change from the old eager list
return worth knowing if you add a new caller.

Jobs are submitted to the process pool in bounded windows rather than all at
once: concurrent.futures.Executor.map submits every task eagerly (creating
one Future per job up front, before any results are consumed), so handing it
the full job list for a huge batch would recreate the same unbounded-memory
problem streaming is meant to fix. Pulling jobs off a lazy generator in
window_size-sized slices, against one long-lived pool reused across windows,
keeps peak in-flight jobs/futures bounded independent of total batch size.
"""
import itertools
import os
import random
from concurrent.futures import ProcessPoolExecutor

from metrics.run_results import build_run_result
from runner.single_run import run_single
from simulation.configuration import validate_simulation_config

_worker_config = None


def resolve_base_seed(base_seed=None) -> int:
    """Resolve an omitted batch seed to a fresh, recordable 32-bit seed."""
    if base_seed is None:
        return random.SystemRandom().randrange(2 ** 32)
    return base_seed


def _init_worker(config, crops, upgrades, watering_settings, fertilizer_config, world):
    global _worker_config
    _worker_config = (config, crops, upgrades, watering_settings, fertilizer_config, world)


def _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world):
    try:
        player, used_seed, _history = run_single(
            config, agent, crops, upgrades, watering_settings, fertilizer_config,
            seed=run_seed, record_history=False, world=world,
        )
        return build_run_result(player, agent.name, used_seed, player.day, crops, upgrades)
    except Exception as exc:
        raise RuntimeError(
            f"Batch run failed: strategy={agent.name}, seed={run_seed}: {exc}"
        ) from exc


def _run_in_worker(job):
    agent, run_seed = job
    config, crops, upgrades, watering_settings, fertilizer_config, world = _worker_config
    return _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world)


def run_batch(config: dict, agents: list, crops: list, upgrades: list, watering_settings: dict,
              fertilizer_config: dict, num_runs: int, base_seed=None, world=None, workers=None,
              window_size=None):
    _validate_batch_inputs(config, num_runs, workers)
    return _iter_batch(
        config, agents, crops, upgrades, watering_settings, fertilizer_config,
        num_runs, base_seed, world, workers, window_size,
    )


def _validate_batch_inputs(config: dict, num_runs: int, workers: int | None) -> None:
    validate_simulation_config(config)
    if not isinstance(num_runs, int) or isinstance(num_runs, bool) or num_runs <= 0:
        raise ValueError("num_runs must be a positive integer")
    if workers is not None and (
        not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0
    ):
        raise ValueError("workers must be a positive integer")


def _iter_batch(config: dict, agents: list, crops: list, upgrades: list, watering_settings: dict,
                fertilizer_config: dict, num_runs: int, base_seed=None, world=None, workers=None,
                window_size=None):
    base_seed = resolve_base_seed(base_seed)
    seed_rng = random.Random(base_seed)
    total_jobs = len(agents) * num_runs
    if total_jobs <= 0:
        return

    # Lazy -- a whole batch's (agent, seed) pairs are never held in memory at
    # once. Seeds are still minted single-threaded and in the same
    # agent-major order as before, so a given base_seed keeps producing the
    # same per-run seeds regardless of worker count or window size.
    jobs = (
        (agent, seed_rng.randrange(2 ** 32))
        for agent in agents
        for _ in range(num_runs)
    )

    if workers is None:
        workers = os.cpu_count() or 1
    workers = max(1, min(workers, total_jobs))

    if workers <= 1:
        for agent, run_seed in jobs:
            yield _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world)
        return

    if window_size is None:
        window_size = max(2000, workers * 500)
    window_size = max(1, window_size)

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(config, crops, upgrades, watering_settings, fertilizer_config, world),
    ) as executor:
        while True:
            window = list(itertools.islice(jobs, window_size))
            if not window:
                break
            chunksize = max(1, len(window) // (workers * 4))
            yield from executor.map(_run_in_worker, window, chunksize=chunksize)
