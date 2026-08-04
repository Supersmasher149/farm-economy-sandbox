"""Run the same economy configuration many times per strategy.

A single base_seed drives a dedicated RNG that mints one per-run seed for
each simulation, so a whole batch is itself reproducible from one seed
while every individual run still has its own recorded seed.
Verbose per-run history is disabled here by default for batch performance.

Runs are executed across a process pool by default: each (agent, run_seed)
pair is independent, so batches split cleanly across cores. Seed generation
stays single-threaded and sequential so a given base_seed always produces
the same set of per-run seeds regardless of worker count.
"""
import os
import random
from concurrent.futures import ProcessPoolExecutor

from metrics.run_results import build_run_result
from runner.single_run import run_single


def _run_one(args):
    config, agent, crops, upgrades, watering_settings, fertilizer_config, run_seed, world = args
    player, used_seed, _history = run_single(
        config, agent, crops, upgrades, watering_settings, fertilizer_config,
        seed=run_seed, record_history=False, world=world,
    )
    return build_run_result(player, agent.name, used_seed, player.day, crops, upgrades)


def run_batch(config: dict, agents: list, crops: list, upgrades: list, watering_settings: dict,
              fertilizer_config: dict, num_runs: int, base_seed=None, world=None, workers=None):
    seed_rng = random.Random(base_seed)
    jobs = [
        (config, agent, crops, upgrades, watering_settings, fertilizer_config, seed_rng.randrange(2 ** 32), world)
        for agent in agents
        for _ in range(num_runs)
    ]

    if workers is None:
        workers = os.cpu_count() or 1

    if workers <= 1 or len(jobs) <= 1:
        return [_run_one(job) for job in jobs]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(_run_one, jobs))
