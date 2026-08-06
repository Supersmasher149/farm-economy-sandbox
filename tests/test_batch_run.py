import types

from agents.fast_seller import FastSeller
from agents.profit_optimizer import ProfitOptimizer
from agents.random_agent import RandomAgent
from metrics.aggregate_results import BatchAggregator, aggregate
from metrics.run_results import write_csv
from runner.batch_run import run_batch
from tests.test_engine import (
    CONFIG,
    FERTILIZER_CONFIG,
    WATERING_SETTINGS,
    make_crops,
    make_upgrades,
)

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
        CONFIG,
        AGENTS,
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=5,
        base_seed=123,
        workers=1,
    )
    parallel = run_batch(
        CONFIG,
        AGENTS,
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=5,
        base_seed=123,
        workers=4,
    )

    assert _run_result_tuples(sequential) == _run_result_tuples(parallel)


def test_batch_workers_are_capped_to_job_count():
    crops, upgrades = make_crops(), make_upgrades()

    # 2 jobs (1 agent x 2 runs) requesting 8 workers should not error, and
    # should still match a sequential run.
    sequential = run_batch(
        CONFIG,
        [ProfitOptimizer()],
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=2,
        base_seed=7,
        workers=1,
    )
    over_provisioned = run_batch(
        CONFIG,
        [ProfitOptimizer()],
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=2,
        base_seed=7,
        workers=8,
    )

    assert _run_result_tuples(sequential) == _run_result_tuples(over_provisioned)


def test_run_batch_returns_a_lazy_iterator_not_a_list():
    # run_batch streams results rather than materializing them, so a
    # multi-million-run batch doesn't have to hold everything in memory.
    # Both the sequential (workers=1) and parallel branches must honor this.
    crops, upgrades = make_crops(), make_upgrades()

    sequential = run_batch(
        CONFIG,
        [ProfitOptimizer()],
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=2,
        base_seed=1,
        workers=1,
    )
    parallel = run_batch(
        CONFIG,
        [ProfitOptimizer()],
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=2,
        base_seed=1,
        workers=4,
    )

    for results in (sequential, parallel):
        assert not isinstance(results, list)
        assert isinstance(results, types.GeneratorType)


def test_batch_streams_correctly_across_a_window_boundary():
    # 3 agents x 4 runs = 12 jobs; window_size=5 forces 3 windows (5, 5, 2).
    # The windowed parallel result must still match a sequential run
    # exactly, proving ordering/determinism survives the window boundary.
    crops, upgrades = make_crops(), make_upgrades()
    agents = [ProfitOptimizer(), RandomAgent(), FastSeller()]

    sequential = run_batch(
        CONFIG,
        agents,
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=4,
        base_seed=99,
        workers=1,
    )
    windowed = run_batch(
        CONFIG,
        agents,
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=4,
        base_seed=99,
        workers=3,
        window_size=5,
    )

    assert _run_result_tuples(sequential) == _run_result_tuples(windowed)


def test_streaming_pipeline_matches_full_materialization(tmp_path):
    # Proves the real cmd_batch shape (run_batch -> tee -> write_csv, with a
    # BatchAggregator fed as a side effect) end-to-end: same summary as
    # aggregating a fully materialized list, and correct CSV row count.
    crops, upgrades = make_crops(), make_upgrades()

    reference_results = list(
        run_batch(
            CONFIG,
            AGENTS,
            crops,
            upgrades,
            WATERING_SETTINGS,
            FERTILIZER_CONFIG,
            num_runs=6,
            base_seed=555,
            workers=1,
        )
    )
    reference_summary = aggregate(reference_results)

    streamed_results = run_batch(
        CONFIG,
        AGENTS,
        crops,
        upgrades,
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        num_runs=6,
        base_seed=555,
        workers=1,
    )
    aggregator = BatchAggregator()

    def _tee(stream):
        for r in stream:
            aggregator.add(r)
            yield r

    csv_path = tmp_path / "run_results.csv"
    crop_ids = [c["id"] for c in crops]
    write_csv(_tee(streamed_results), str(csv_path), crop_ids)

    assert aggregator.finalize() == reference_summary
    with open(csv_path) as f:
        row_count = sum(1 for _ in f) - 1  # minus header
    assert row_count == 6 * len(AGENTS)
