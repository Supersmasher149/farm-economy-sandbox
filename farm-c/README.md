# farm-c: a pure-C port of the farm-economy simulator (in progress)

This is a self-contained, standalone build of the pure-C port described in
[`docs/c-port-plan.md`](../docs/c-port-plan.md), built up phase by phase
against real (not placeholder) C types and checked at every phase against
the real Python modules as the oracle -- see
`.claude/plans/shimmying-singing-dragonfly.md` for the phase plan this
follows. It started as just the 11 agent strategies in `../agents/*.py`
(the decision surface) plus every shared "pure economics" helper they call,
and has since grown the RNG, physics, and state-mutation layers underneath
them (Phases 0-2, described below). No `farm-c` code runs inside a real
batch yet -- there is still no config loader or day-loop engine wiring
these pieces together (Phases 3-4), so this remains a standalone,
independently-tested build rather than a drop-in replacement for
`simulation/`.

The three phases so far:

- **Phase 0**, `src/rng.c`: a bit-exact MT19937 port of `random.Random`, the
  single-generator RNG `../simulation/random_events.py:RandomEvents` wraps
  and every physics/market/contract module downstream of it depends on. It
  doesn't run inside the agent port above (agents never touch the RNG
  directly) -- it was staged ahead of Phase 1 as the first module that
  actually calls it.
- **Phase 1**, `src/crop_growth.c` + `src/weather.c` (+ `src/pyfloat.c` for
  shared float-semantics helpers): a bit-exact port of
  `../simulation/crop_growth.py` and `../simulation/weather.py` --
  per-plot daily stress accumulation, harvest yield/quality multipliers and
  grading, and season/weather generation. `crop_growth_update_stress` and
  `weather_apply`'s per-plot loop reuse the arithmetic already proven in
  `../simulation/_fastplotmodule.c` (same Neumaier summation, same literal
  `max`/`min` clamp forms); `harvest_multipliers`, `quality_grade`, and
  `compute_harvest_outcome` are fresh ports, since the fastplot kernel
  doesn't cover them. `weather.py`'s `round(x, ndigits)` calls are ported as
  a `snprintf("%.*f")` + `strtod` round-trip (`pyfloat.c:py_round_ndigits`)
  rather than a from-scratch port of CPython's David Gay dtoa -- both target
  libcs implement correctly-rounded decimal conversion, which is the
  property CPython's own dtoa/strtod pair relies on, and this is checked
  against real recorded `round()` output rather than assumed (see
  Verification below).
- **Phase 2**, `src/actions.c` (new) plus extensions to `src/inventory.c`,
  `src/markets.c`, `src/contracts.c`, and a new `src/processing.c`: every
  state-*mutating* function the modern (`world`-driven) `run_day` path
  calls -- buying/planting/watering/fertilizing/harvesting crops, buying
  upgrades, FEFO inventory consumption, storage aging/spoilage/capacity
  enforcement, daily price updates, channel sales, processing jobs starting
  and completing, and the full contract lifecycle (offer generation,
  accept, deliver, expiry resolution). `FarmState` (`state.h`) grew the
  rest of `PlayerState`'s fields these need (market supply/revenue-by-
  channel, the various running totals, quality/loss breakdowns, ...), and
  `config.h` grew the config fields Phase 0/1 never needed (buyer offer-
  generation terms, storage/markets top-level config, recipe shelf life,
  fertilizer nutrients-added, ...). This also let `contracts.c` retire its
  one documented simplification: `_best_possible_grade`'s
  `QUALITY_STANDARD` stand-in now calls the real
  `crop_growth_harvest_multipliers`/`crop_growth_quality_grade`, since
  Phase 1 supplies the stress physics it needs.

Still doesn't run inside a real batch -- there is no `FarmState` day loop
wiring these together yet (that's Phase 4, after Phase 3's config loader).

## Scope boundary

**Faithful, full-fidelity port.** Everything below matches its Python
source function-for-function, including tie-break behavior and rounding.
Phases 0-1's functions are pure (no `FarmState` mutation); Phase 2's are
the state-*mutating* functions the same "modern `run_day` path only" scope
decision includes -- see the Phase 2 paragraph above:

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

- `../simulation/crop_growth.py`'s `update_crop_stress`, `harvest_multipliers`,
  `quality_grade`, and `compute_harvest_outcome` (`src/crop_growth.c`).
- `../simulation/weather.py`'s `season_for_day`, `generate_weather`, and the
  plain (non-`_fastplot`-accelerated) body of `apply_weather`
  (`src/weather.c`) -- ported line-for-line from the reference Python loop,
  not from the optional accelerator, so every conditional field write
  matches Python's `if regen_x: plot.x = ...` exactly.
- `../simulation/actions.py`'s modern-path functions -- `buy_seeds`,
  `plant_seed`, `water_crop`, `buy_fertilizer`, `fertilize_crop`,
  `harvest_mature`, `buy_upgrade`, `do_nothing` (`src/actions.c`).
  `water_farm`/`sell_all` back only the legacy (`world=None`) `run_day`
  path this port doesn't target (see `docs/c-port-plan.md`'s scope
  decision) and are intentionally not ported.
- `../simulation/inventory.py`'s remaining functions -- `consume` (FEFO),
  `capture_storage_liability`, `collect_storage_liability`,
  `enforce_storage_capacity`, `age_and_spoil` (`src/inventory.c`).
- `../simulation/markets.py`'s `update_daily_prices` and `sell`
  (`src/markets.c`).
- `../simulation/processing.py`'s `start_job` and `complete_jobs`
  (`src/processing.c`, new).
- `../simulation/contracts.py`'s day-loop mutators -- `generate_offers`,
  `accept`, `deliver`, `resolve_expired`, `visible_offers`,
  `offer_expiry_day`, `is_offer_expired` (`src/contracts.c`). Also,
  `_best_possible_grade` (used by `_future_crop_arrivals`, and therefore by
  the already-ported `contracts_is_offer_feasible`/
  `contracts_forecast_committed_supply`) is no longer the `QUALITY_STANDARD`
  stand-in the agent-decision slice shipped with -- it now calls the real
  `crop_growth_harvest_multipliers`/`crop_growth_quality_grade`, since
  Phase 1 supplies the stress physics it needs. This retires the one
  documented simplification the agent-only slice carried.

## Layout

```
include/            farm_types.h, config.h, state.h, agent.h, economy.h,
                     markets.h, inventory.h, contracts.h, derived.h,
                     rng.h, rng_hash.h, blake2b.h, vec_util.h, pyfloat.h,
                     crop_growth.h, weather.h, actions.h, processing.h
src/                 economy_rules.c, markets.c, inventory.c, contracts.c,
                     derived.c, rng.c, rng_hash.c, blake2b.c, config.c,
                     state.c, vec_util.c, agent.c, agent_registry.c,
                     pyfloat.c, crop_growth.c, weather.c, actions.c,
                     processing.c
src/agents/          base.c (shared defaults + route_sales_by_best_price),
                     one file per agent, matching ../agents/*.py 1:1
tests/               test_agents.c (fixture-driven parity test for the
                     agent port), test_rng.c (same, for rng.c),
                     test_physics.c (same, for crop_growth.c/weather.c),
                     test_mutation.c (same, for actions.c/inventory.c/
                     markets.c/processing.c/contracts.c's mutators),
                     fixtures/{agents,rng,physics,mutation}.json (generated,
                     checked in), third_party/cJSON.{h,c} (vendored, MIT --
                     fixture parsing only, not a production config loader)
```

## Building and testing

```bash
cd farm-c
make fixtures          # regenerates tests/fixtures/agents.json from the
                        # real Python agents (needs the repo's venv; see
                        # ../tools/export_agent_fixtures.py)
make fixtures-rng      # regenerates tests/fixtures/rng.json from CPython's
                        # random.Random (see ../tools/export_rng_fixtures.py)
make fixtures-physics  # regenerates tests/fixtures/physics.json from the
                        # real crop_growth.py/weather.py (see
                        # ../tools/export_physics_fixtures.py)
make fixtures-mutation # regenerates tests/fixtures/mutation.json from the
                        # real actions.py/inventory.py/markets.py/
                        # processing.py/contracts.py (see
                        # ../tools/export_mutation_fixtures.py)
make test              # builds and runs every tests/test_* binary under
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
fixture-based instead, with the real Python modules as the oracle:

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
5. `src/crop_growth.c`/`src/weather.c` are verified the same way, against
   the real `simulation/crop_growth.py`/`simulation/weather.py` as the
   oracle: `tools/export_physics_fixtures.py` builds a small synthetic crop
   catalog plus hand-picked edge-case scenarios (moisture/pH/temperature on
   both sides of a crop's tolerance band, clamp-saturating accumulated
   stress, fertilized vs. not, same-family rotation penalty vs. mismatch vs.
   no-family, `plot=None`, a 40-seed sweep of `compute_harvest_outcome`, and
   a full `apply_weather` day over fallow/growing/regen/no-regen plots) and
   records every input/output as `float.hex()`; `tests/test_physics.c`
   replays each case and asserts `==` -- currently **542 checks, 0 failed**.
   `compute_harvest_outcome`'s cases are RNG-adjacent: each uses a fresh
   seed, and the C side seeds a fresh `FarmRng` with the same seed rather
   than replaying raw draws, relying on Phase 0's already-proven bit-exact
   RNG. `py_round_ndigits` (weather.py's `round(x, ndigits)`) was
   additionally stress-tested standalone against 200,000 random values
   across `round(x, 2)`/`round(x, 3)` with 0 mismatches, beyond what the
   fixture set alone happens to exercise.
6. `src/actions.c`/`src/inventory.c`/`src/markets.c`/`src/processing.c`/
   `src/contracts.c`'s Phase 2 mutators are verified the same way, against
   the real Python modules as the oracle, but with a broader fixture shape
   than Phases 0-1 use: `tools/export_mutation_fixtures.py` builds one
   shared synthetic world (2 crops, a product, a recipe, 2 upgrades, 2
   channels, 2 buyers) and 51 scenarios across the 22 ported functions
   (success and rejection paths, FEFO tie-breaks, expired/insufficient/
   over-capacity cases, contract accept/deliver/expiry, ...), and for each
   one records a *full snapshot* of every mutable `PlayerState` field
   before and after calling the real function -- not just that function's
   own documented return value -- so `tests/test_mutation.c` catches an
   incidental extra mutation the C port gets wrong, not only the ones a
   docstring happened to think to check. Currently **4552 checks, 0
   failed**. Two real bugs were caught and fixed this way before the suite
   went green: `channel.get("daily_capacity", quantity)` in `sell` treats
   an explicit `None` differently from an absent key (a Python gotcha, not
   a port bug -- fixed in the fixture, not the C), and the test harness's
   own `scatter_double` helper was reading `cJSON`'s `valuedouble` on
   `float.hex()`-encoded *string* nodes instead of parsing `valuestring`
   with `strtod` (a fixture-loader bug, not a `src/` one -- caught because
   the snapshot diff surfaced a stale `market_supply`/`buyer_relationships`
   read that a return-value-only check would have missed entirely).

## Known simplifications / follow-ups

- `RandomAgent`'s repr-formatting helper (`src/rng_hash.c`) implements
  Python's string-repr quoting algorithm in full, but is not a general
  `repr()` -- it only needs to (and only claims to) reproduce the tuple
  shapes `agents/random_agent.py`'s four call sites actually pass.
- Config loading here is fixture-JSON-only (each `tests/test_*.c`'s own
  `load_config` + vendored `cJSON`), not a production loader for
  `config/*.json` -- `docs/c-port-plan.md` Section 10 covers that decision
  for the full port (Phase 3).
- `py_round_ndigits` (`src/pyfloat.c`) relies on the host libc's `%f`
  formatting and `strtod` both being correctly-rounded, rather than porting
  CPython's dtoa outright. Verified on this repo's development platform
  (see Verification above); has not been checked against a libc that isn't
  correctly-rounded for decimal conversion, which would be a real
  platform-portability gap if this port is ever built somewhere else.
