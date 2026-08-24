"""End-to-end checks that the statistical layer changed nothing it must not.

The load-bearing claim of this whole feature is that inference rides *beside*
the simulation rather than inside it: same seeds, same runs, same recorded
outcomes, with intervals added. These tests try to falsify that claim, and
then check the four renderers agree about every number they share.
"""

import json
import os
import random
from types import SimpleNamespace

import pytest

import main
from metrics import distributions, view
from metrics.aggregate_results import BatchAggregator
from runner.batch_run import run_batch
from runner.sampling_plan import IndependentHashedV1, LegacyMT19937V1, SharedInitialSeedV1


def _artifact(run_dir, name):
    """Read one published artifact through its `latest` symlink."""
    with open(os.path.join(run_dir, name)) as handle:
        return json.load(handle) if name.endswith(".json") else handle.read()


@pytest.fixture
def tiny_world():
    crops, upgrades, config, world = main.load_config()
    config = dict(config)
    config["days"] = 12
    return crops, upgrades, config, world


def _run_two_strategies(tiny_world, seed, workers=1, plan=None, runs=4):
    crops, upgrades, config, world = tiny_world
    agents = [main.AGENT_REGISTRY[name]() for name in ("profit_optimizer", "fast_seller")]
    return list(
        run_batch(
            config,
            agents,
            crops,
            upgrades,
            world["watering"],
            world["fertilizer"],
            num_runs=runs,
            base_seed=seed,
            world=world,
            workers=workers,
            sampling_plan=plan,
        )
    )


# --------------------------------------------------------------------------
# Nothing about the simulation moved
# --------------------------------------------------------------------------


def test_default_batch_still_mints_the_historical_seed_stream(tiny_world):
    """A fixed --runs batch must produce exactly the seeds it always has: the
    replay baselines and farm-c's minting parity both depend on it."""
    results = _run_two_strategies(tiny_world, seed=4242, runs=3)
    expected_rng = random.Random(4242)
    expected = [expected_rng.randrange(2**32) for _ in range(6)]
    assert [r.seed for r in results] == expected


def test_results_are_identical_with_and_without_an_explicit_legacy_plan(tiny_world):
    implicit = _run_two_strategies(tiny_world, seed=99)
    explicit = _run_two_strategies(tiny_world, seed=99, plan=LegacyMT19937V1(99, 4))
    assert [(r.strategy, r.seed, r.final_money) for r in implicit] == [
        (r.strategy, r.seed, r.final_money) for r in explicit
    ]


def test_analysis_consumes_no_simulation_rng_draws(tiny_world):
    """Running the full analysis stack between two identical batches must not
    change the second one -- inference draws only from its own Random."""
    first = _run_two_strategies(tiny_world, seed=7)

    aggregator = BatchAggregator()
    for result in first:
        aggregator.add(result)
    aggregator.finalize()
    from metrics.comparisons import compare_all_pairs
    from metrics.inference import bootstrap_interval

    bootstrap_interval([r.final_money for r in first], analysis_seed=1, replications=50)
    by_strategy = {}
    for result in first:
        by_strategy.setdefault(result.strategy, []).append(result)
    compare_all_pairs(by_strategy, estimand_ids=["expected_final_money"])

    second = _run_two_strategies(tiny_world, seed=7)
    assert [(r.strategy, r.seed, r.final_money) for r in first] == [
        (r.strategy, r.seed, r.final_money) for r in second
    ]


def test_replicate_ids_do_not_depend_on_worker_count(tiny_world):
    sequential = _run_two_strategies(tiny_world, seed=11, workers=1, plan=IndependentHashedV1(11))
    parallel = _run_two_strategies(tiny_world, seed=11, workers=2, plan=IndependentHashedV1(11))
    assert [(r.strategy, r.replicate_id, r.seed) for r in sequential] == [
        (r.strategy, r.replicate_id, r.seed) for r in parallel
    ]


def test_parallel_and_sequential_inference_agree(tiny_world):
    sequential = _run_two_strategies(tiny_world, seed=13, workers=1)
    parallel = _run_two_strategies(tiny_world, seed=13, workers=2)

    def _inference(results):
        aggregator = BatchAggregator()
        for result in results:
            aggregator.add(result)
        return {strategy: stats["inference"] for strategy, stats in aggregator.finalize().items()}

    assert _inference(sequential) == _inference(parallel)


def test_paired_plan_gives_matching_replicate_seeds_across_strategies(tiny_world):
    results = _run_two_strategies(tiny_world, seed=17, plan=SharedInitialSeedV1(17))
    by_replicate = {}
    for result in results:
        by_replicate.setdefault(result.replicate_id, set()).add(result.seed)
    assert all(len(seeds) == 1 for seeds in by_replicate.values())
    assert len(by_replicate) == 4


# --------------------------------------------------------------------------
# One published set, one set of numbers
# --------------------------------------------------------------------------


@pytest.fixture
def published_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        main,
        "AGENT_REGISTRY",
        {
            "profit_optimizer": main.AGENT_REGISTRY["profit_optimizer"],
            "fast_seller": main.AGENT_REGISTRY["fast_seller"],
        },
    )
    main.cmd_batch(
        SimpleNamespace(
            runs=5,
            seed=2024,
            workers=1,
            progress=False,
            days=12,
            start_money=None,
            charts=False,
            bootstrap_replications=80,
        )
    )
    run_dir = os.path.realpath(os.path.join(str(tmp_path), "latest"))
    return str(tmp_path), run_dir


def test_batch_publishes_every_declared_artifact(published_batch):
    reports_dir, run_dir = published_batch
    for name in main.ARTIFACT_NAMES:
        assert os.path.exists(os.path.join(run_dir, name)), name
        link = os.path.join(reports_dir, name)
        assert os.path.islink(link) and os.path.exists(link)


def test_summary_carries_estimand_metadata_and_intervals(published_batch):
    _, run_dir = published_batch
    document = view.load_run(run_dir)
    assert document["estimand_registry_version"].startswith("farm-estimands-")
    assert document["sampling_plan"]["plan"] == "legacy-mt19937-v1"
    assert document["stop_reason"] == "fixed_sample"
    assert "expected_final_money" in document["estimands"]
    for stats in document["strategies"].values():
        estimate = stats["inference"]["expected_final_money"]
        assert estimate["n"] == stats["num_runs"]
        assert estimate["lower"] <= estimate["value"] <= estimate["upper"]
        assert estimate["method"] == "student_t"


def test_descriptive_values_are_unchanged_by_the_inference_block(published_batch):
    """The interval sits beside the number, never in place of it: the mean the
    aggregator publishes must still be the mean the estimand brackets."""
    _, run_dir = published_batch
    document = view.load_run(run_dir)
    for stats in document["strategies"].values():
        assert stats["inference"]["expected_final_money"]["value"] == pytest.approx(
            stats["avg_final_money"], rel=1e-9
        )
        assert stats["inference"]["bankruptcy_probability"]["value"] == pytest.approx(
            stats["bankruptcy_rate"] / 100.0, rel=1e-9
        )


def test_exact_distributions_match_the_published_csv(published_batch):
    _, run_dir = published_batch
    document = _artifact(run_dir, "distributions.json")
    observations = distributions.load_observations(os.path.join(run_dir, "run_results.csv"))
    for strategy, entry in document["strategies"].items():
        values = [o.final_money for o in observations[strategy]]
        assert entry["cohorts"]["all_runs"]["count"] == len(values)
        assert entry["cohorts"]["all_runs"]["quantiles"]["p50"] == pytest.approx(
            distributions.quantile(values, 0.5)
        )


def test_report_view_and_summary_agree_on_the_same_estimate(published_batch):
    _, run_dir = published_batch
    document = view.load_run(run_dir)
    report = _artifact(run_dir, "summary_report.md")
    rendered = view.render_intervals(document["strategies"], ["expected_final_money"])

    assert "## Confidence Intervals" in report
    assert "## Statistical Design" in report
    for strategy, stats in document["strategies"].items():
        value = stats["inference"]["expected_final_money"]["value"]
        formatted = f"{value:,.4f}".rstrip("0").rstrip(".")
        assert strategy in rendered
        # The markdown table rounds for display; check the leading digits of
        # the same canonical value appear in both renderings.
        assert formatted.split(".")[0] in report
        assert formatted.split(".")[0] in rendered


def test_comparisons_document_records_pairing_and_family(published_batch):
    _, run_dir = published_batch
    document = _artifact(run_dir, "comparisons.json")
    assert document["pairing"] == "independent"
    pairs = document["estimands"]["expected_final_money"]
    assert len(pairs) == 1  # two strategies -> one pair
    assert pairs[0]["family_size"] == 1
    assert pairs[0]["correction"] == "holm"


def test_analysis_metadata_records_provenance(published_batch):
    _, run_dir = published_batch
    document = _artifact(run_dir, "analysis_metadata.json")
    assert document["schema"] == "farm-analysis-metadata-v1"
    assert document["seed"]["base_seed"] == 2024
    assert document["seed"]["per_run_seed_rule"] == "legacy-mt19937-v1"
    assert document["sampling"]["requested_runs_per_strategy"] == 5
    assert document["sampling"]["realized_runs_per_strategy"] == 5
    assert document["inference"]["confidence"] == 0.95
    assert document["environment"]["python_version"]
    assert "git" in document["provenance"]
    # Sufficient statistics, not pickles.
    state = document["accumulator_state"]["profit_optimizer"]["expected_final_money"]
    assert set(state) >= {"count", "mean", "m2", "variance"}


def test_fixed_batch_convergence_document_has_one_terminal_look(published_batch):
    _, run_dir = published_batch
    document = _artifact(run_dir, "convergence.json")
    assert document["design"]["mode"] == "fixed"
    assert len(document["checkpoints"]) == 1
    assert document["realized_runs_per_strategy"] == 5


def test_adaptive_batch_publishes_a_checkpoint_history(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        main, "AGENT_REGISTRY", {"profit_optimizer": main.AGENT_REGISTRY["profit_optimizer"]}
    )
    main.cmd_batch(
        SimpleNamespace(
            runs=4,
            seed=31,
            workers=1,
            progress=False,
            days=10,
            start_money=None,
            charts=False,
            distributions=False,
            comparisons=False,
            min_runs=4,
            max_runs=12,
            checkpoint_runs=4,
            target_relative_half_width=0.9,
        )
    )
    run_dir = os.path.realpath(os.path.join(str(tmp_path), "latest"))
    convergence = _artifact(run_dir, "convergence.json")
    summary = _artifact(run_dir, "summary.json")
    assert summary["sampling_plan"]["plan"] == "independent-hashed-v1"
    assert summary["stop_reason"] in (
        "precision_reached",
        "max_runs_reached",
        "rare_event_minimum_unmet",
    )
    assert convergence["design"]["checkpoint_schedule"] == [4, 8, 12]
    assert convergence["checkpoints"]
    assert summary["num_runs"] == convergence["realized_runs_per_strategy"]
    csv_rows = distributions.load_observations(os.path.join(run_dir, "run_results.csv"))
    assert len(csv_rows["profit_optimizer"]) == summary["num_runs"]


def test_adaptive_mode_rejects_the_legacy_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPORTS_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="adaptive sampling does not know"):
        main.cmd_batch(
            SimpleNamespace(
                runs=4,
                seed=1,
                workers=1,
                progress=False,
                days=10,
                start_money=None,
                charts=False,
                max_runs=8,
                min_runs=4,
                checkpoint_runs=4,
                target_half_width=1.0,
                sampling_plan="legacy",
            )
        )


def test_adaptive_flags_without_a_target_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REPORTS_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="needs at least one precision target"):
        main.cmd_batch(
            SimpleNamespace(
                runs=4,
                seed=1,
                workers=1,
                progress=False,
                days=10,
                start_money=None,
                charts=False,
                max_runs=8,
            )
        )
