# farm-c: the 11 agents, ported to C

This is a self-contained, standalone slice of the pure-C port described in
[`docs/c-port-plan.md`](../docs/c-port-plan.md): the 11 agent strategies in
`../agents/*.py`, plus every shared "pure economics" helper they call, built
against real (not placeholder) C types so `src/agents/*.c` compiles and is
unit-tested in isolation *ahead of* the rest of the engine port (config
loader, `FarmState` day loop, weather, crop growth). No `farm-c` code runs
inside a real batch yet -- there is no engine here, only the agent decision
surface and what it needs.

Also here, as the first piece of the actual engine port (Phase 0 of
`../docs/c-port-plan.md`'s "full engine" follow-up): `src/rng.c`, a
bit-exact MT19937 port of `random.Random`, the single-generator RNG
`../simulation/random_events.py:RandomEvents` wraps and every physics/market/
contract module downstream of it depends on. It doesn't run inside the
agent port above (agents never touch the RNG directly) -- it's staged here
ahead of Phase 1 (weather/crop_growth), which is the first module that
actually calls it.

## Scope boundary

**Faithful, full-fidelity port** (pure functions, no engine/physics
dependency):

- All 18 functions of `../simulation/economy_rules.py` (`src/economy_rules.c`)
  -- the core of every agent's crop/upgrade/fertilizer decisions.
- `../simulation/markets.py`'s `quote`, `best_channel`, and
  `QUALITY_MULTIPLIERS` (`src/markets.c`).
- `../simulation/inventory.py`'s `available_quantity` (`src/inventory.c`).
- `../simulation/derived.py`'s `effective_growth_days` and
  `nutrient_demand_total` (`src/derived.c`) -- the Python versions memoize on
  config-object identity purely because they sit on a per-plot-per-day hot
  path re-reading a mutable-dict config; a static C `ResolvedConfig` needs no
  such cache, so it's a straight recompute here.
- `PlayerState.decision_random` (`src/rng_hash.c` + a vendored minimal
  BLAKE2b, `src/blake2b.c`) -- RandomAgent's hash-based policy stream, ported
  bit-for-bit: same BLAKE2b digest, same Python-`repr()`-shaped payload
  string for the exact four argument shapes `../agents/random_agent.py`
  passes. Verified against `hashlib.blake2b` directly (see git history) for
  BLAKE2b known-answer vectors and all four call shapes.
- All 11 agents (`src/agents/*.c`), function-for-function against their
  Python source, including the exact same constants and tie-break behavior
  (Python's `min`/`max` keep the first element on an exact tie; every C loop
  here replaces its running best only on a *strict* inequality to match).

**Explicitly simplified stand-in** (documented, not silently approximated):

`contracts_is_offer_feasible` and `contracts_forecast_committed_supply`
(`src/contracts.c`) depend on `../simulation/contracts.py`'s
`_future_crop_arrivals` → `_best_possible_grade`, which calls
`crop_growth.harvest_multipliers` -- live weather/soil stress physics this
agents-only port doesn't have. The stand-in
(`best_possible_grade_SIMPLIFIED` in `src/contracts.c`, marked with a
`SIMPLIFIED` comment) assumes every already-planted crop of the matching
item can reach `QUALITY_STANDARD`, dropping the real stress-based grade
ceiling. This only ever *undercounts* forecast risk for above-standard
(premium) contracts -- everything else in `_item_capacity` /
`_future_crop_arrivals` / `_input_supply` / `_schedule_batches` /
`_slot_free_days` (the timeline-aware processing-forecast machinery
`docs/c-port-plan.md` Section 8 warns not to oversimplify) is a faithful,
unsimplified port. Reconcile this one stand-in once `crop_growth`/`weather`
are ported.

**Out of scope entirely:** `update_daily_prices`, `sell`, `generate_offers`,
`accept`, `deliver`, `resolve_expired`, `age_and_spoil` -- no ported agent
calls any of these directly.

## Layout

```
include/            farm_types.h, config.h, state.h, agent.h, economy.h,
                     markets.h, inventory.h, contracts.h, derived.h,
                     rng.h, rng_hash.h, blake2b.h, vec_util.h
src/                 economy_rules.c, markets.c, inventory.c, contracts.c,
                     derived.c, rng.c, rng_hash.c, blake2b.c, config.c,
                     state.c, vec_util.c, agent.c, agent_registry.c
src/agents/          base.c (shared defaults + route_sales_by_best_price),
                     one file per agent, matching ../agents/*.py 1:1
tests/               test_agents.c (fixture-driven parity test for the
                     agent port), test_rng.c (same, for rng.c),
                     fixtures/{agents,rng}.json (generated, checked in),
                     third_party/cJSON.{h,c} (vendored, MIT -- fixture
                     parsing only, not a production config loader)
```

## Building and testing

```bash
cd farm-c
make fixtures      # regenerates tests/fixtures/agents.json from the real
                    # Python agents (needs the repo's venv; see
                    # ../tools/export_agent_fixtures.py)
make fixtures-rng   # regenerates tests/fixtures/rng.json from CPython's
                    # random.Random (see ../tools/export_rng_fixtures.py)
make test           # builds and runs every tests/test_* binary under
                    # -fsanitize=address,undefined
```

`make test` alone (no Python needed) re-runs against whatever fixtures are
already checked in.

Every compile/link rule builds with `-ffp-contract=off`. Without it, a
compiler may legally fuse an expression shaped like `a + (b - a) * x` (e.g.
`rng_uniform`) into a single-rounded FMA instead of two separately-rounded
IEEE-754 operations -- Python never does this, so it silently produces
1-ulp divergences that only `-ffp-contract=off` prevents. This is exactly
what caught `rng_uniform`/`rng_roll_price` drifting from the Python oracle
in ~2% of `test_rng.c`'s cases before the flag was added (see
`docs/c-port-plan.md` Section 7, and `../simulation/_fastplotmodule.c`'s
header comment for the same requirement on the existing weather/crop-growth
kernel).

## Verification

There is no engine here yet, so there's no golden-replay run to check
against (contrast `.claude/skills/replay-guard`, which is what verifies
determinism once `simulation/` itself is touched). Verification is
fixture-based instead, with the real Python agents as the oracle:

1. `tools/export_agent_fixtures.py` builds a handful of `PlayerState`
   scenarios in the same directly-constructed style as
   `tests/test_strategy_controls.py`'s `policy_inputs()`, runs all 11 real
   agent classes' full decision surface (`choose_crop`, `should_buy_upgrade`,
   `should_water`, `should_fertilize`, `choose_contracts`,
   `choose_contract_deliveries`, `choose_processing`, `choose_sales`,
   `should_use_fertilizer`) against each scenario, and records the
   config + scenarios + expected outputs as JSON.
2. `tests/test_agents.c` loads that JSON, builds the equivalent
   `FarmState`/`ResolvedConfig`, calls the matching C vtable function, and
   asserts the decision matches -- currently **408 checks, 0 failed**
   (396 fixture cases across 4 scenarios × 11 agents × 9 methods, plus 12
   vtable function-pointer identity checks for the three control agents that
   subclass `ProfitOptimizer` in Python -- see `agent.h`'s header comment).
3. Independently of the fixture round-trip, a one-off spot-check loaded the
   **real** `config/crops.json` (not the synthetic fixture world) into both
   languages and compared day-0 `choose_crop` for six agents directly against
   the real Python classes -- exact match on every one.
4. `src/rng.c` is verified the same way, against `random.Random` as the
   oracle instead of the agents: `tools/export_rng_fixtures.py` seeds a real
   `RandomEvents` for 8 seeds (chosen to cover both the single- and
   two-32-bit-word branches of Python's integer seeding) and records a
   long, fixed-order, 2800-call-per-seed sequence across all 7
   `RandomEvents` operations; `tests/test_rng.c` replays the identical
   sequence through one `FarmRng` seeded the same way and asserts every
   result matches with `==`, not an epsilon -- currently **22400 checks, 0
   failed**. The sequence length deliberately crosses MT19937's 624-word
   regeneration boundary several times per seed, and includes `choice`
   calls over a length-1 sequence to force `_randbelow`'s rejection-sampling
   loop to actually reject and redraw.

## Known simplifications / follow-ups

- The `_best_possible_grade` stand-in in `src/contracts.c` (see Scope
  boundary above).
- `RandomAgent`'s repr-formatting helper (`src/rng_hash.c`) implements
  Python's string-repr quoting algorithm in full, but is not a general
  `repr()` -- it only needs to (and only claims to) reproduce the tuple
  shapes `agents/random_agent.py`'s four call sites actually pass.
- Config loading here is fixture-JSON-only (`tests/test_agents.c` +
  vendored `cJSON`), not a production loader for `config/*.json` --
  `docs/c-port-plan.md` Section 10 covers that decision for the full port.
