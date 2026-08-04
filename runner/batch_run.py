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
"""
import os
import random
from concurrent.futures import ProcessPoolExecutor

from metrics.run_results import build_run_result
from runner.single_run import run_single

_worker_config = None


def _init_worker(config, crops, upgrades, watering_settings, fertilizer_config, world):
    global _worker_config
    _worker_config = (config, crops, upgrades, watering_settings, fertilizer_config, world)


def _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world):
    player, used_seed, _history = run_single(
        config, agent, crops, upgrades, watering_settings, fertilizer_config,
        seed=run_seed, record_history=False, world=world,
    )
    return build_run_result(player, agent.name, used_seed, player.day, crops, upgrades)


def _run_in_worker(job):
    agent, run_seed = job
    config, crops, upgrades, watering_settings, fertilizer_config, world = _worker_config
    return _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world)


def run_batch(config: dict, agents: list, crops: list, upgrades: list, watering_settings: dict,
              fertilizer_config: dict, num_runs: int, base_seed=None, world=None, workers=None):
    seed_rng = random.Random(base_seed)
    jobs = [
        (agent, seed_rng.randrange(2 ** 32))
        for agent in agents
        for _ in range(num_runs)
    ]

    if not jobs:
        return []

    if workers is None:
        workers = os.cpu_count() or 1
    workers = max(1, min(workers, len(jobs)))

    if workers <= 1:
        return [
            _execute(agent, run_seed, config, crops, upgrades, watering_settings, fertilizer_config, world)
            for agent, run_seed in jobs
        ]

    chunksize = max(1, len(jobs) // (workers * 4))
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(config, crops, upgrades, watering_settings, fertilizer_config, world),
    ) as executor:
        return list(executor.map(_run_in_worker, jobs, chunksize=chunksize))
