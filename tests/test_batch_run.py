from agents.profit_optimizer import ProfitOptimizer
from agents.random_agent import RandomAgent
from runner.batch_run import run_batch
from tests.test_engine import CONFIG, FERTILIZER_CONFIG, WATERING_SETTINGS, make_crops, make_upgrades

# RandomAgent is included deliberately: its choices used to be derived from
# Python's builtin hash(), which is randomized per interpreter process, so a
# parallel batch could silently diverge from a sequential one for the same
# seed even though every recorded per-run seed matched.
AGENTS = [ProfitOptimizer(), RandomAgent()]


def _run_result_tuples(results):
    return [
        (r.strategy, r.seed, r.final_money, r.total_revenue, r.crops_planted, r.bankrupt)
        for r in results
    ]


def test_parallel_batch_matches_sequential_batch():
    crops, upgrades = make_crops(), make_upgrades()

    sequential = run_batch(
        CONFIG, AGENTS, crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG,
        num_runs=5, base_seed=123, workers=1,
    )
    parallel = run_batch(
        CONFIG, AGENTS, crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG,
        num_runs=5, base_seed=123, workers=4,
    )

    assert _run_result_tuples(sequential) == _run_result_tuples(parallel)


def test_batch_workers_are_capped_to_job_count():
    crops, upgrades = make_crops(), make_upgrades()

    # 2 jobs (1 agent x 2 runs) requesting 8 workers should not error, and
    # should still match a sequential run.
    sequential = run_batch(
        CONFIG, [ProfitOptimizer()], crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG,
        num_runs=2, base_seed=7, workers=1,
    )
    over_provisioned = run_batch(
        CONFIG, [ProfitOptimizer()], crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG,
        num_runs=2, base_seed=7, workers=8,
    )

    assert _run_result_tuples(sequential) == _run_result_tuples(over_provisioned)
