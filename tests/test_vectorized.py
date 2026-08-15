"""pytest wrapper around scripts/vectorized_validate.py's checks (component E).

`vectorized/` is optional (see its README's "Why this is a separate tool" and
requirements-fast.txt) -- numpy/numba are not dependencies of the simulator
proper, so this module skips itself when they're absent, the same pattern
tests/test_visualize.py uses for matplotlib. When they *are* installed
(`pip install -r requirements-fast.txt`), this folds all six of
scripts/vectorized_validate.py's checks into the regular `python3 -m pytest`
run instead of leaving them reachable only by remembering to run that script
by hand -- CI's `vectorized` job (.github/workflows/ci.yml) is what makes
sure they're actually exercised somewhere.

The check functions themselves (kernel-vs-reference agreement, chunk-size
independence, storage capacity trim, processing, contracts, upgrades) live in
scripts/vectorized_validate.py, not duplicated here -- see that module's
docstring and vectorized/README.md's "Validation (component E)" section for
what each one actually proves. Each check function already raises a
descriptive AssertionError on its own if kernel and reference disagree, or if
the branch it claims to exercise never actually fired; the `== N` assertions
below just pin the expected combination counts so a check silently running
fewer combinations than intended (e.g. a scoping bug that empties a loop)
fails loudly here too, not just a kernel/reference mismatch.

The classes below cover what `vectorized_validate.py` doesn't: the public
entry points a caller actually uses. Every check above drives
`vectorized.kernel`/`vectorized.reference` directly, one run at a time --
nothing exercised `vectorized.orchestrator.run_millions` (chunking,
memory-budget sizing, multi-chunk `StreamingStats` aggregation,
strategy-weight bucketing) or `vectorized.stats.StreamingStats` (the
hand-rolled Chandan-Golub-LeVeque batch-merge) against a known-correct
answer before this file existed -- both are exactly the kind of glue code a
kernel-vs-reference comparison can't catch, since a bug in either would
still leave the per-run numbers agreeing.
"""

import numpy as np
import pytest

pytest.importorskip("numpy")
pytest.importorskip("numba")

import scripts.vectorized_validate as vv  # noqa: E402
from vectorized import crops  # noqa: E402
from vectorized.orchestrator import (  # noqa: E402
    choose_chunk_size,
    run_isolated_strategy_fallback,
    run_millions,
)
from vectorized.stats import StreamingStats  # noqa: E402

# Same short-but-branch-covering horizon scripts/vectorized_validate.py's
# main() uses -- long enough to exercise every branch repeatedly, short
# enough to stay fast in the regular test suite.
NUM_DAYS = 90


def test_kernel_matches_reference():
    assert vv.check_kernel_matches_reference(NUM_DAYS) == 144


def test_chunk_size_independence():
    assert vv.check_chunk_size_independence(NUM_DAYS) == 3


def test_storage_capacity_trim():
    assert vv.check_storage_capacity_trim(NUM_DAYS) == 27


def test_processing_occurs():
    assert vv.check_processing_occurs(NUM_DAYS) == 27


def test_contracts_occur():
    assert vv.check_contracts_occur(NUM_DAYS) == 27


def test_upgrades_purchased():
    assert vv.check_upgrades_purchased(NUM_DAYS) == 27


class TestStreamingStats:
    """Welford batch-merge vs. plain numpy over the same concatenated data.

    Uneven batch sizes (7, then 1, then 200) deliberately exercise the
    small-n_a/large-n_b and large-n_a/small-n_b ends of the merge formula,
    not just equal-sized chunks.
    """

    def test_matches_numpy_across_uneven_batches(self):
        rng = np.random.default_rng(0)
        batches = [rng.normal(size=n) for n in (7, 1, 200, 50)]
        stats = StreamingStats()
        for batch in batches:
            stats.update(batch)

        combined = np.concatenate(batches)
        assert stats.count == combined.size
        assert stats.mean == pytest.approx(combined.mean(), rel=1e-9)
        assert stats.stddev == pytest.approx(combined.std(ddof=0), rel=1e-9)
        assert stats.minimum == pytest.approx(combined.min())
        assert stats.maximum == pytest.approx(combined.max())

    def test_empty_batch_is_a_no_op(self):
        stats = StreamingStats()
        stats.update(np.array([1.0, 2.0, 3.0]))
        before = stats.as_dict()
        stats.update(np.array([]))
        assert stats.as_dict() == before

    def test_fresh_instance_reports_zero_count(self):
        stats = StreamingStats()
        d = stats.as_dict()
        assert d["count"] == 0
        assert d["min"] is None
        assert d["max"] is None


class TestChooseChunkSize:
    def test_caps_at_max_chunk_when_memory_is_plentiful(self):
        size = choose_chunk_size(num_plots=10, max_memory_gb=64.0, max_chunk=500)
        assert size == 500

    def test_shrinks_under_a_tight_memory_budget(self):
        generous = choose_chunk_size(num_plots=10, max_memory_gb=2.0, max_chunk=100_000)
        tight = choose_chunk_size(num_plots=10, max_memory_gb=0.001, max_chunk=100_000)
        assert 0 < tight < generous

    def test_never_returns_less_than_one(self):
        # An absurdly small budget still has to make forward progress.
        assert choose_chunk_size(num_plots=10, max_memory_gb=1e-12, max_chunk=100_000) == 1


class TestRunMillions:
    """Small, fast batches (a handful of plots/days) -- these test the
    orchestration plumbing, not throughput or economic realism (that's
    scripts/vectorized_benchmark.py's job, and the module docstring's job
    respectively)."""

    def test_result_counts_and_by_strategy_partition(self):
        result = run_millions(
            total_runs=37, num_plots=2, num_days=10, master_seed=7, max_chunk=100_000
        )
        assert result.overall_money.count == 37
        by_strategy_total = sum(s.count for s in result.by_strategy_money.values())
        assert by_strategy_total == 37
        # Every registered strategy actually got at least one run out of 37,
        # given the default equal (1, 1, 1) weighting.
        assert set(result.by_strategy_money) == set(range(len(crops.STRATEGY_NAMES)))
        assert "runs x" in result.summary()

    def test_multi_chunk_matches_single_chunk(self):
        # Forcing max_chunk below total_runs makes run_millions loop over
        # several chunks instead of one. rng.py's chunk-independence
        # property holds at the state/kernel layer *given the same
        # strategy* (check_chunk_size_independence above), but
        # run_millions's own strategy-of-run bucketing is deliberately
        # chunk-local (see orchestrator.py's comment and
        # vectorized/README.md's "RNG strategy" section) -- a different
        # max_chunk assigns a different global-index-to-strategy mapping, so
        # the aggregate money mean is *not* expected to match exactly, only
        # the run count and each chunking's per-strategy proportionality.
        # (An earlier version of this test asserted numerical equality here;
        # a real run falsified it -- see the README section above for why
        # that was the wrong invariant to check.)
        kwargs = dict(total_runs=41, num_plots=2, num_days=10, master_seed=99)
        single = run_millions(max_chunk=100_000, **kwargs)
        chunked = run_millions(max_chunk=9, **kwargs)

        assert single.overall_money.count == chunked.overall_money.count == 41
        for result in (single, chunked):
            by_strategy_total = sum(s.count for s in result.by_strategy_money.values())
            assert by_strategy_total == 41
            # Equal (1, 1, 1) weights over 41 runs: every bucket should land
            # near a third, not be empty or dominate -- the actual property
            # chunk-local bucketing guarantees, regardless of max_chunk.
            for s in result.by_strategy_money.values():
                assert 5 <= s.count <= 25

    def test_strategy_weights_bias_the_mix(self):
        # All-random weighting: every run should land in the "random" bucket.
        result = run_millions(
            total_runs=20,
            num_plots=2,
            num_days=5,
            master_seed=3,
            strategy_weights=(0.0, 0.0, 1.0),
        )
        assert result.by_strategy_money[crops.STRATEGY_RANDOM].count == 20
        assert result.by_strategy_money[crops.STRATEGY_GREEDY].count == 0
        assert result.by_strategy_money[crops.STRATEGY_CONSERVATIVE].count == 0


def test_run_isolated_strategy_fallback_produces_finite_stats():
    # The escape hatch nothing in run_millions calls (see orchestrator.py's
    # docstring) -- exercised directly so it stays a real, working code path.
    stats = run_isolated_strategy_fallback(
        strategy_id=crops.STRATEGY_GREEDY,
        total_runs=5,
        num_plots=2,
        num_days=10,
        master_seed=11,
    )
    assert stats.count == 5
    assert np.isfinite(stats.mean)
    assert np.isfinite(stats.stddev)
