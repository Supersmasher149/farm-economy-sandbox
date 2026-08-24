"""Tests for runner/sampling_plan.py.

The legacy plan is frozen: `farm-c`'s own seed-minting parity check and every
recorded seed in the repo depend on it producing exactly the values it always
has, so the first test here reproduces that stream independently rather than
comparing the plan to itself.
"""

import random
from types import SimpleNamespace

import pytest

from runner.sampling_plan import (
    INDEPENDENT_PLAN,
    LEGACY_PLAN,
    PAIRED_PLAN,
    SEED_SPACE,
    IndependentHashedV1,
    LegacyMT19937V1,
    SharedInitialSeedV1,
    resolve,
)


def _agents(*names):
    return [SimpleNamespace(name=name) for name in names]


# --------------------------------------------------------------------------
# Legacy plan (frozen)
# --------------------------------------------------------------------------


def test_legacy_plan_reproduces_the_historical_stream_exactly():
    agents = _agents("a", "b", "c")
    plan = LegacyMT19937V1(base_seed=42, num_runs=4)
    minted = [job.seed for job in plan.jobs(agents, 0, 4)]

    rng = random.Random(42)
    expected = [rng.randrange(2**32) for _ in range(12)]
    assert minted == expected, "legacy seeds are a frozen contract with farm-c and every replay"


def test_legacy_plan_is_agent_major_and_numbers_replicates_per_strategy():
    jobs = list(LegacyMT19937V1(1, 3).jobs(_agents("a", "b"), 0, 3))
    assert [job.strategy for job in jobs] == ["a", "a", "a", "b", "b", "b"]
    assert [job.replicate_id for job in jobs] == [0, 1, 2, 0, 1, 2]


def test_legacy_plan_refuses_to_mint_a_partial_block():
    plan = LegacyMT19937V1(1, 10)
    with pytest.raises(ValueError, match="whole batch"):
        list(plan.jobs(_agents("a"), 5, 5))


def test_legacy_plan_is_not_extendable():
    assert LegacyMT19937V1(1, 10).extendable is False


# --------------------------------------------------------------------------
# Addressed plans
# --------------------------------------------------------------------------


def test_independent_plan_gives_each_strategy_its_own_seed():
    plan = IndependentHashedV1(42)
    assert plan.seed_for("a", 0) != plan.seed_for("b", 0)
    assert plan.seed_for("a", 0) != plan.seed_for("a", 1)
    assert 0 <= plan.seed_for("a", 0) < SEED_SPACE


def test_independent_plan_is_invariant_to_roster_changes():
    """Adding a strategy must not remap anyone else's seeds -- the property the
    legacy sequential stream cannot offer."""
    plan = IndependentHashedV1(42)
    before = [job.seed for job in plan.jobs(_agents("a", "b"), 0, 3) if job.strategy == "a"]
    after = [job.seed for job in plan.jobs(_agents("z", "a", "b"), 0, 3) if job.strategy == "a"]
    assert before == after


def test_addressed_plans_extend_block_by_block():
    plan = IndependentHashedV1(7)
    whole = [(j.strategy, j.seed) for j in plan.jobs(_agents("a", "b"), 0, 6)]
    blocks = [(j.strategy, j.seed) for j in plan.jobs(_agents("a", "b"), 0, 2)]
    blocks += [(j.strategy, j.seed) for j in plan.jobs(_agents("a", "b"), 2, 4)]
    assert whole == blocks


def test_addressed_plans_are_replicate_major():
    jobs = list(IndependentHashedV1(7).jobs(_agents("a", "b"), 0, 2))
    assert [job.replicate_id for job in jobs] == [0, 0, 1, 1]
    assert [job.strategy for job in jobs] == ["a", "b", "a", "b"]


def test_paired_plan_shares_one_seed_per_replicate():
    jobs = list(SharedInitialSeedV1(42).jobs(_agents("a", "b", "c"), 0, 2))
    replicate_zero = {job.seed for job in jobs if job.replicate_id == 0}
    replicate_one = {job.seed for job in jobs if job.replicate_id == 1}
    assert len(replicate_zero) == 1
    assert len(replicate_one) == 1
    assert replicate_zero != replicate_one


def test_paired_plan_is_invariant_to_strategy_order_and_roster():
    plan = SharedInitialSeedV1(42)
    forward = [job.seed for job in plan.jobs(_agents("a", "b"), 0, 3)]
    reordered = [job.seed for job in plan.jobs(_agents("b", "a"), 0, 3)]
    extended = [job.seed for job in plan.jobs(_agents("a", "b", "c"), 0, 3) if job.strategy in "ab"]
    assert forward == reordered == extended


def test_paired_plan_declares_its_pairing_strength():
    description = SharedInitialSeedV1(1).describe()
    assert description["paired"] is True
    assert description["pairing_strength"] == "weak"
    assert "diverge" in description["caveat"]


def test_seeds_are_stable_across_processes():
    """blake2b, never hash(): a PYTHONHASHSEED-dependent seed would not
    reproduce, which is the one thing a published seed must do."""
    assert IndependentHashedV1(42).seed_for("profit_optimizer", 17) == (
        IndependentHashedV1(42).seed_for("profit_optimizer", 17)
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_resolve_maps_cli_aliases_to_plan_ids():
    assert resolve("legacy", 1, 5).plan_id == LEGACY_PLAN
    assert resolve("independent", 1).plan_id == INDEPENDENT_PLAN
    assert resolve("paired", 1).plan_id == PAIRED_PLAN
    assert resolve(LEGACY_PLAN, 1, 5).plan_id == LEGACY_PLAN


def test_resolve_rejects_an_unknown_plan_and_a_missing_run_count():
    with pytest.raises(ValueError, match="unknown sampling plan"):
        resolve("sobol", 1)
    with pytest.raises(ValueError, match="needs num_runs"):
        resolve("legacy", 1)


def test_describe_records_what_a_reader_needs():
    description = resolve("independent", 99).describe()
    assert description["plan"] == INDEPENDENT_PLAN
    assert description["base_seed"] == 99
    assert description["extendable"] is True
