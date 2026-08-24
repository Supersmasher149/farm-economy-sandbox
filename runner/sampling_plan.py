"""Versioned seed schedules for a batch.

A batch's per-run seeds are part of its statistical design, not an
implementation detail, so the schedule that mints them is named, versioned,
and recorded in every artifact. Three plans ship:

`legacy-mt19937-v1`
    Exactly what `runner/batch_run.py` has always done: one
    `random.Random(base_seed)` drawn agent-major, `num_runs` seeds per agent
    in registry order. It is the default for a fixed `--runs` batch and is
    **bit-identical to the pre-existing schedule** -- every recorded seed,
    every replay baseline, and farm-c's own minting parity depend on that, so
    this plan is frozen. It requires knowing the run count up front (the
    stream position of agent k's run i depends on how many runs precede it),
    which is why adaptive sampling cannot use it.

`independent-hashed-v1`
    Seed = H(base_seed, plan, strategy, replicate). Independent across
    strategies like the legacy plan, but *addressed* rather than sequential,
    which buys three things the legacy stream cannot give: a block of
    replicates can be minted without knowing the total (adaptive sampling),
    adding or reordering a strategy does not remap anyone else's seeds, and
    any (strategy, replicate) can be reproduced in isolation.

`shared-initial-seed-v1`
    Seed = H(base_seed, plan, replicate) -- every strategy gets the *same*
    run seed for replicate N. This is stage 1-3 of plan Section 7's staged
    common-random-numbers path, and it is deliberately labelled **weak**
    pairing: `simulation/random_events.py` threads one RNG through the whole
    run, so as soon as two strategies make different decisions they consume
    different numbers of draws and their weather diverges from that point on.
    Replicate 1's day 1 weather is shared; day 40's very likely is not.
    `metrics/comparisons.py` therefore *measures* the correlation and the
    realized variance reduction instead of assuming pairing worked. Strong
    CRN (separate environment/policy streams, event-addressed shocks) would
    require changing `RandomEvents`, which would invalidate every replay
    baseline and farm-c's parity, so it is explicitly staged behind this.

Every plan is a pure function of `(base_seed, strategy, replicate)` except the
frozen legacy one, so worker count, dispatch window and execution order cannot
affect which seed a run gets.

`hash()` is never used for addressing: PYTHONHASHSEED randomizes it per
process, so seeds derived from it would not reproduce. blake2b is stable
across processes, machines and Python versions.
"""

import hashlib
import random
from dataclasses import dataclass

SEED_SPACE = 2**32

LEGACY_PLAN = "legacy-mt19937-v1"
INDEPENDENT_PLAN = "independent-hashed-v1"
PAIRED_PLAN = "shared-initial-seed-v1"

# CLI-facing aliases, so `--sampling-plan paired` does not require typing a
# version string that will change.
PLAN_ALIASES = {
    "legacy": LEGACY_PLAN,
    "independent": INDEPENDENT_PLAN,
    "paired": PAIRED_PLAN,
    LEGACY_PLAN: LEGACY_PLAN,
    INDEPENDENT_PLAN: INDEPENDENT_PLAN,
    PAIRED_PLAN: PAIRED_PLAN,
}


@dataclass(frozen=True)
class Job:
    """One planned simulation: which agent, which seed, which replicate."""

    agent: object
    seed: int
    replicate_id: int
    strategy: str


def _hashed_seed(*parts) -> int:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % SEED_SPACE


class SamplingPlan:
    """Base class: mint (agent, seed, replicate) jobs for a block of runs."""

    plan_id = "abstract"
    paired = False
    extendable = False

    def __init__(self, base_seed: int):
        self.base_seed = base_seed

    def jobs(self, agents: list, start_replicate: int, count: int):
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "plan": self.plan_id,
            "base_seed": self.base_seed,
            "paired": self.paired,
            "extendable": self.extendable,
        }


class LegacyMT19937V1(SamplingPlan):
    """The frozen historical schedule. Agent-major, one MT19937 stream.

    Reproduces `random.Random(base_seed).randrange(2**32)` in exactly the
    original order, so a given `--seed` keeps producing the identical batch it
    always has -- including for `farm-c`, whose `rng_randrange_2_32` mints the
    same values in the same order.
    """

    plan_id = LEGACY_PLAN
    extendable = False

    def __init__(self, base_seed: int, num_runs: int):
        super().__init__(base_seed)
        self.num_runs = num_runs

    def jobs(self, agents: list, start_replicate: int = 0, count: int | None = None):
        if start_replicate != 0 or (count is not None and count != self.num_runs):
            raise ValueError(
                f"{self.plan_id} mints a whole batch at once (its stream position depends on "
                "the total run count); use independent-hashed-v1 for block/adaptive sampling"
            )
        seed_rng = random.Random(self.base_seed)
        for agent in agents:
            for replicate in range(self.num_runs):
                yield Job(
                    agent=agent,
                    seed=seed_rng.randrange(SEED_SPACE),
                    replicate_id=replicate,
                    strategy=agent.name,
                )

    def describe(self) -> dict:
        return {**super().describe(), "num_runs": self.num_runs}


class IndependentHashedV1(SamplingPlan):
    """Addressed, independent-per-strategy seeds. The adaptive default."""

    plan_id = INDEPENDENT_PLAN
    extendable = True

    def seed_for(self, strategy: str, replicate: int) -> int:
        return _hashed_seed(self.base_seed, self.plan_id, strategy, replicate)

    def jobs(self, agents: list, start_replicate: int = 0, count: int = 1):
        # Replicate-major so a partial block still covers every strategy
        # equally -- an adaptive stopping rule must never compare a strategy
        # with 750 runs against one with 500.
        for replicate in range(start_replicate, start_replicate + count):
            for agent in agents:
                yield Job(
                    agent=agent,
                    seed=self.seed_for(agent.name, replicate),
                    replicate_id=replicate,
                    strategy=agent.name,
                )


class SharedInitialSeedV1(SamplingPlan):
    """Weak common random numbers: one environment seed per replicate."""

    plan_id = PAIRED_PLAN
    paired = True
    extendable = True

    def seed_for(self, strategy: str, replicate: int) -> int:
        # Deliberately ignores `strategy`: that is the whole point.
        return _hashed_seed(self.base_seed, self.plan_id, replicate)

    def jobs(self, agents: list, start_replicate: int = 0, count: int = 1):
        for replicate in range(start_replicate, start_replicate + count):
            seed = self.seed_for("", replicate)
            for agent in agents:
                yield Job(agent=agent, seed=seed, replicate_id=replicate, strategy=agent.name)

    def describe(self) -> dict:
        return {
            **super().describe(),
            "pairing_strength": "weak",
            "caveat": (
                "Strategies share a run seed, not a weather sequence: differing "
                "decisions consume differing numbers of RNG draws, so the "
                "environments diverge within a run. Measured correlation and "
                "variance reduction are reported per comparison."
            ),
        }


def resolve(name: str, base_seed: int, num_runs: int | None = None) -> SamplingPlan:
    """Build a plan from a CLI name or a full plan id."""
    plan_id = PLAN_ALIASES.get(name)
    if plan_id is None:
        raise ValueError(
            f"unknown sampling plan {name!r}; expected one of {sorted(set(PLAN_ALIASES))}"
        )
    if plan_id == LEGACY_PLAN:
        if num_runs is None:
            raise ValueError("legacy-mt19937-v1 needs num_runs up front")
        return LegacyMT19937V1(base_seed, num_runs)
    if plan_id == INDEPENDENT_PLAN:
        return IndependentHashedV1(base_seed)
    return SharedInitialSeedV1(base_seed)
