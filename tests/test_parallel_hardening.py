"""Hardening tests for batch parallelism.

tests/test_batch_run.py already pins that a parallel batch matches a
sequential one for a given base_seed, plus worker capping, streaming, and
window boundaries. These cover the structural reasons that equivalence could
silently stop holding:

- runner.batch_run reuses a single agent *instance* across every sequential
  run of that agent, but pickles a fresh copy per parallel job. Any agent
  that grew mutable per-run state would therefore behave differently under
  `--workers 1` than under the default pool -- and the existing equivalence
  test would only catch it if that state happened to change the outcome of
  one of its five runs.
- simulation.derived's module-level caches are keyed on id() and outlive the
  run that created them, so a process that has already executed other runs
  must still produce identical output for a given seed.
- multiprocessing uses "spawn" here, so workers get fresh interpreters with
  their own PYTHONHASHSEED. Anything that reached builtin hash() ordering
  would diverge only in the parallel branch.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

from main import AGENT_REGISTRY
from runner.batch_run import run_batch
from runner.single_run import run_single
from simulation import derived
from tests.test_engine import (
    CONFIG,
    FERTILIZER_CONFIG,
    WATERING_SETTINGS,
    make_crops,
    make_upgrades,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tuples(results):
    return [
        (r.strategy, r.seed, r.final_money, r.total_revenue, r.crops_planted, r.bankrupt)
        for r in results
    ]


def _batch(agents, *, workers, num_runs=4, base_seed=321, **kwargs):
    return _tuples(
        run_batch(
            CONFIG,
            agents,
            make_crops(),
            make_upgrades(),
            WATERING_SETTINGS,
            FERTILIZER_CONFIG,
            num_runs=num_runs,
            base_seed=base_seed,
            workers=workers,
            **kwargs,
        )
    )


# --- agents must stay stateless across runs -------------------------------


@pytest.mark.parametrize("agent_cls", list(AGENT_REGISTRY.values()), ids=list(AGENT_REGISTRY))
def test_shipped_agents_carry_no_per_run_state(agent_cls):
    """An agent instance must be unchanged by running a simulation.

    Sequential batches reuse one instance for every run of a strategy while
    parallel batches get a fresh unpickled copy per job. Per-instance state
    would make those two paths disagree, so agents must keep their policy in
    class attributes and their per-run state on PlayerState.
    """
    agent = agent_cls()
    before = copy.deepcopy(vars(agent))

    run_single(
        CONFIG,
        agent,
        make_crops(),
        make_upgrades(),
        WATERING_SETTINGS,
        FERTILIZER_CONFIG,
        seed=42,
    )

    assert vars(agent) == before, (
        f"{agent_cls.__name__} mutated its own instance during a run; sequential and "
        "parallel batches would diverge because only the sequential path reuses the instance"
    )


def test_sequential_batch_is_invariant_to_agent_instance_reuse():
    """Reusing one agent instance across runs must match a fresh one per run.

    This is the sequential half of the parallel/sequential contract: the pool
    effectively gets a fresh agent per job, so a reused instance must produce
    the same results as fresh ones for the equivalence to be real rather than
    coincidental.
    """
    reused = AGENT_REGISTRY["profit_optimizer"]()
    crops, upgrades = make_crops(), make_upgrades()

    reused_results = []
    fresh_results = []
    for seed in (11, 22, 33):
        player, _seed, _history = run_single(
            CONFIG, reused, crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=seed
        )
        reused_results.append((player.money, player.total_revenue, player.total_planted))

        player, _seed, _history = run_single(
            CONFIG,
            AGENT_REGISTRY["profit_optimizer"](),
            crops,
            upgrades,
            WATERING_SETTINGS,
            FERTILIZER_CONFIG,
            seed=seed,
        )
        fresh_results.append((player.money, player.total_revenue, player.total_planted))

    assert reused_results == fresh_results


class _PlantsOnceAgent:
    """Deliberately stateful probe (#27): plants only on its first
    `choose_crop()` call, ever. A batch runner that reuses one instance
    across a strategy's runs would then plant on run 1 and go idle for
    every run after it; independent runs must each get their own instance.
    """

    name = "plants_once_probe"
    description = "Test-only agent that mutates per-instance state to expose shared-instance reuse."
    watering_diligence = 1.0

    def __init__(self):
        self.already_planted = False

    def choose_crop(self, player, crops, crops_by_id, upgrades_by_id):
        if self.already_planted:
            return None
        candidates = [c for c in crops if player.money >= c["seed_cost"]]
        if not candidates:
            return None
        self.already_planted = True
        return candidates[0]

    def should_buy_upgrade(self, player, upgrade):
        return False

    def should_water(self, player, planted, crop):
        return True

    def should_fertilize(self, player, planted, crop, fertilizer_config):
        return False

    def choose_contracts(self, player, offers):
        return []

    def choose_contract_deliveries(self, player):
        return []

    def choose_processing(self, player, recipes, items_by_id):
        return []

    def choose_sales(self, player, channels, items_by_id):
        return [
            {"item_id": lot.item_id, "quantity": lot.quantity, "channel_id": "spot"}
            for lot in player.inventory_lots
        ]

    def should_use_fertilizer(self, player, crop, fertilizer_config):
        return False


def test_sequential_batch_gives_every_run_its_own_stateful_agent_instance():
    results = _batch([_PlantsOnceAgent()], workers=1, num_runs=3)
    planted_counts = [r[4] for r in results]  # crops_planted, per _tuples()

    # Before the #27 fix, one shared instance meant only the first run ever
    # saw already_planted == False, so this was [>=1, 0, 0] under
    # `workers=1` -- diverging from the fresh-instance-per-job parallel path.
    assert all(count >= 1 for count in planted_counts), (
        f"expected every run to get a fresh, unplanted agent instance, got {planted_counts}"
    )


def test_sequential_and_parallel_agree_for_a_stateful_agent():
    sequential = _batch([_PlantsOnceAgent()], workers=1, num_runs=4)
    parallel = _batch([_PlantsOnceAgent()], workers=3, num_runs=4)
    assert sequential == parallel


# --- worker-count invariance ----------------------------------------------


def test_results_are_identical_across_every_worker_count():
    """Not just 1-vs-4: any worker count must give byte-identical results.

    Worker count changes chunksize and how jobs are distributed, so pinning a
    single parallel configuration would leave the general property untested.
    """
    agents = [AGENT_REGISTRY["profit_optimizer"](), AGENT_REGISTRY["random_agent"]()]
    baseline = _batch(agents, workers=1)

    for workers in (2, 3, 5, 7):
        assert _batch(agents, workers=workers) == baseline, (
            f"workers={workers} diverged from the sequential baseline"
        )


def test_per_run_seeds_do_not_depend_on_worker_count():
    """Seeds are minted single-threaded before dispatch, so the seed sequence
    itself -- not merely the outcomes -- must be worker-count independent.
    """
    agents = [AGENT_REGISTRY["fast_seller"](), AGENT_REGISTRY["profit_optimizer"]()]
    sequential = [(row[0], row[1]) for row in _batch(agents, workers=1)]

    for workers in (2, 4):
        assert [(row[0], row[1]) for row in _batch(agents, workers=workers)] == sequential


def test_single_job_windows_still_match_sequential():
    """window_size=1 forces one window per job and chunksize 1 -- the extreme
    opposite of the default batching, and the case most likely to expose an
    ordering assumption in the streaming loop.
    """
    agents = [AGENT_REGISTRY["profit_optimizer"](), AGENT_REGISTRY["fast_seller"]()]
    assert _batch(agents, workers=3, window_size=1) == _batch(agents, workers=1)


# --- derived caches must not leak between runs in one process -------------


def test_repeated_seed_is_unaffected_by_other_runs_in_the_same_process():
    """simulation.derived caches on id() and outlives the run that filled it.

    A worker process executes many runs against one config object, so a seed
    replayed after other work must still produce identical output.
    """
    agent_cls = AGENT_REGISTRY["profit_optimizer"]
    crops, upgrades = make_crops(), make_upgrades()

    first, _seed, _history = run_single(
        CONFIG, agent_cls(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=4242
    )

    # Churn: other seeds, other strategies, and fresh config objects (new
    # id()s, hence new cache entries) between the two runs of seed 4242.
    for seed in range(5):
        run_single(
            CONFIG,
            AGENT_REGISTRY["random_agent"](),
            make_crops(),
            make_upgrades(),
            WATERING_SETTINGS,
            FERTILIZER_CONFIG,
            seed=seed,
        )

    second, _seed, _history = run_single(
        CONFIG, agent_cls(), crops, upgrades, WATERING_SETTINGS, FERTILIZER_CONFIG, seed=4242
    )

    assert (first.money, first.total_revenue, first.crop_plant_counts) == (
        second.money,
        second.total_revenue,
        second.crop_plant_counts,
    )


def test_upgrade_keyed_lookups_do_not_leak_across_runs():
    """WorldLookups caches storage/processing capacity per owned-upgrade set,
    on an object shared by every run using the same config. A run that buys
    upgrades must not change what a later run with none sees.
    """
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})
    upgrades_by_id = {
        "storage_1": {
            "effect": {"type": "storage", "capacity_bonus": 50, "shelf_life_multiplier": 2}
        }
    }

    before = lookups.effective_storage(world["storage"], set(), upgrades_by_id)
    with_upgrade = lookups.effective_storage(world["storage"], {"storage_1"}, upgrades_by_id)
    after = lookups.effective_storage(world["storage"], set(), upgrades_by_id)

    # The owned-upgrade half of the key has to be asserted on the *upgraded*
    # lookup: if it were dropped, that call would silently return the
    # already-cached un-upgraded value and a before == after check alone
    # would still pass.
    assert before["capacity"] == 100
    assert with_upgrade["capacity"] == 150
    assert with_upgrade["shelf_life_multiplier"] == 2
    assert after == before


def test_upgrade_keyed_processing_capacity_does_not_leak_across_runs():
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})
    upgrades_by_id = {"processing_1": {"effect": {"type": "processing_capacity", "amount": 3}}}
    config = world["processing"]

    before = lookups.processing_capacity(config, set(), upgrades_by_id)
    with_upgrade = lookups.processing_capacity(config, {"processing_1"}, upgrades_by_id)
    after = lookups.processing_capacity(config, set(), upgrades_by_id)

    assert before == 2
    assert with_upgrade == 5
    assert after == 2


def test_derived_cache_eviction_does_not_change_results():
    """The caches clear wholesale at _MAX_ENTRIES. Recomputed values must be
    identical to cached ones, or a long-lived worker would drift mid-batch.
    """
    crops = make_crops()
    cached = derived.cheapest_seed_cost(crops)

    for _ in range(derived._MAX_ENTRIES + 1):
        derived.cheapest_seed_cost(make_crops())

    assert derived.cheapest_seed_cost(crops) == cached


def test_mutating_a_returned_storage_dict_cannot_corrupt_the_cache():
    world = _minimal_world()
    lookups = derived.world_lookups(world, {})

    first = lookups.effective_storage(world["storage"], set(), {})
    first["capacity"] = -999
    second = lookups.effective_storage(world["storage"], set(), {})

    assert second["capacity"] == 100


def _minimal_world() -> dict:
    return {
        "watering": {},
        "fertilizer": {},
        "storage": {"capacity": 100, "shelf_life_multiplier": 1.0, "daily_cost": 1.0},
        "weather": {},
        "markets": {"channels": [], "default_variation": 0.12},
        "contracts": {},
        "buyers": [],
        "processing": {"base_capacity": 2, "products": [], "recipes": []},
        "soil": {},
    }


# --- cross-process determinism (spawned workers get new hash seeds) -------

_BATCH_SNIPPET = """
import json, sys
sys.path.insert(0, {repo!r})
from main import AGENT_REGISTRY, load_config
from runner.batch_run import run_batch

crops, upgrades, config, world = load_config()
config = dict(config, days=40)
agents = [AGENT_REGISTRY["random_agent"](), AGENT_REGISTRY["profit_optimizer"]()]
rows = [
    (r.strategy, r.seed, round(r.final_money, 6), r.crops_planted, r.bankrupt)
    for r in run_batch(
        config, agents, crops, upgrades, world["watering"], world["fertilizer"],
        num_runs=3, base_seed=2024, world=world, workers={workers},
    )
]
print(json.dumps(rows))
"""


def _batch_in_subprocess(hash_seed: str, workers: int):
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    completed = subprocess.run(
        [sys.executable, "-c", _BATCH_SNIPPET.format(repo=REPO_ROOT, workers=workers)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_batch_is_identical_across_interpreter_hash_seeds():
    """PYTHONHASHSEED randomizes str/bytes hashing per interpreter, and spawn
    gives every pool worker a fresh one. A batch must not depend on it in
    either the sequential or the parallel branch.
    """
    reference = _batch_in_subprocess("0", workers=1)

    assert _batch_in_subprocess("12345", workers=1) == reference
    assert _batch_in_subprocess("0", workers=4) == reference
    assert _batch_in_subprocess("12345", workers=4) == reference
