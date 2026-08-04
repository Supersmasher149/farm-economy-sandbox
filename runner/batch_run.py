"""Run the same economy configuration many times per strategy.

A single base_seed drives a dedicated RNG that mints one per-run seed for
each simulation, so a whole batch is itself reproducible from one seed
while every individual run still has its own recorded seed.
Verbose per-run history is disabled here by default for batch performance.
"""
import random

from metrics.run_results import build_run_result
from runner.single_run import run_single


def run_batch(config: dict, agents: list, crops: list, upgrades: list, watering_settings: dict,
              fertilizer_config: dict, num_runs: int, base_seed=None, world=None):
    results = []
    seed_rng = random.Random(base_seed)
    for agent in agents:
        for _ in range(num_runs):
            run_seed = seed_rng.randrange(2 ** 32)
            player, used_seed, _history = run_single(
                config, agent, crops, upgrades, watering_settings, fertilizer_config,
                seed=run_seed, record_history=False, world=world,
            )
            results.append(build_run_result(player, agent.name, used_seed, player.day, crops, upgrades))
    return results
