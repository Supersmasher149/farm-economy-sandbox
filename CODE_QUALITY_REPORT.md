# Code Quality Report

**Project:** farm-economy-sandbox  
**Audit date:** 2026-08-09  
**Scope:** Production Python, agents, simulation logic, batch/reporting pipeline, tests, configuration validation, optional accelerators, CI, and developer tooling.

## Executive Summary

The repository has a strong quality foundation: deterministic random-number handling is explicit, the engine/agent mutation boundary is generally respected, replay protection is unusually thorough, Ruff reports no lint or formatting defects, and 419 of 421 tests pass.

The current release gate is nevertheless red. Two checked-in balance-policy tests conflict with the checked-in configuration and golden replay baseline. The audit also found correctness defects in contract feasibility forecasting and quality-specific sales, plus operational risks in auto-balance proposals, process-pool agent isolation, and multi-file report publication.

### Quality Snapshot

| Area | Assessment | Notes |
|---|---|---|
| Correctness | Needs attention | Contract timing and quality-specific sales can produce behavior different from agent forecasts. |
| Determinism | Strong, with one latent risk | Golden replay passes; chunked process-pool work can reuse a stateful agent instance. |
| Tests | Broad but currently failing | 419 passed, 2 failed; optional visualization and several failure paths lack coverage. |
| Maintainability | Good overall | Clear module boundaries and extensive rationale, but duplicated date arithmetic and dictionary-shaped interfaces create drift. |
| Tooling/CI | Good baseline | Ruff and multi-version tests exist; packaging, compiled-build safety, and supported-version coverage need work. |

## Verification Results

| Command | Result |
|---|---|
| `python3 -m pytest -p no:cacheprovider` | **Failed:** 419 passed, 2 failed in 8.03s |
| `ruff check --no-cache` | **Passed** |
| `ruff format --check --no-cache` | **Passed:** 92 files already formatted |
| `python3 .claude/skills/replay-guard/scripts/golden_replay.py check` | **Passed:** all 44 strategy/seed combinations |

## Findings

### CQ-01: The checked-in release gate conflicts with current balance data

**Severity:** Critical  
**Category:** Tests / configuration governance

`tests/test_balance_policies.py:157` requires the optimizer's average failed contracts to be at most 1.0, but the deterministic batch produces 1.79. `tests/test_balance_policies.py:195-200` requires `FastSeller` and `ProfitOptimizer` to survive and grow cash over 2,000 days, but `FastSeller` goes bankrupt on day 19. The checked-in golden baseline accepts that bankruptcy, while the test and its design rationale require survival.

**Impact:** CI is red and the repository has no unambiguous authority for intended balance behavior. A developer cannot tell whether configuration, tests, or the golden baseline should change.

**Recommendation:** Make an explicit balance-policy decision. If survival remains required, rebalance configuration and deliberately recapture the golden baseline. If current bankruptcy is intentional, update the stale policy tests and mark the historical design result as superseded. Keep exact replay baselines separate from documented balance-policy thresholds.

### CQ-02: Production forecasts include work on a day the simulator never executes

**Severity:** High  
**Category:** Correctness / contracts

`simulation/contracts.py:161-170` caps production at `player.total_days`, but `runner/single_run.py:51-54` executes days `0` through `total_days - 1`. As a result, crop harvests and processing completions on day `total_days` can be counted even though that day is never run. `simulation/contracts.py:390-397` also compares existing jobs to the uncapped contract deadline. The correct `total_days - 1` boundary is already encoded for `ProfitOptimizer` and tested at `tests/test_strategy_controls.py:175-196`.

**Impact:** Agents can accept infeasible contracts, stop planting too early, or spend toward production that can never complete. This distorts penalties, reputation, and balance metrics.

**Recommendation:** Introduce one authoritative last-executable-day helper and use it for every crop, processing, and committed-supply forecast. Add exact-boundary tests for harvest and job completion on `total_days - 1` and `total_days`.

### CQ-03: Processed-product feasibility ignores input arrival time

**Severity:** High  
**Category:** Correctness / contracts

`simulation/contracts.py:293-357` pools current inventory and all crop output expected by the deadline, then compares that aggregate with total processing slot-days. It does not preserve the day on which future inputs become available. Capacity from early in the window can therefore be applied to inputs harvested too late to finish the recipe.

**Impact:** `is_offer_feasible()` can approve a processed-product contract that cannot be scheduled in the actual daily order, causing avoidable failures and misleading contract-balance results.

**Recommendation:** Forecast processing by day, or track each input quantity's earliest availability. A batch should consume only inputs available by its start day and must finish by the effective deadline.

### CQ-04: The engine discards the quality selected by sales routing

**Severity:** High  
**Category:** Correctness / agent-engine contract

`agents/base.py:118-125` emits sales decisions containing `quality`. `simulation/engine.py:152-157` passes item, quantity, and channel to `markets.sell()` but drops that quality. `simulation/markets.py:87-118` already supports exact-quality execution.

**Impact:** Execution can consume a different lot from the one used to calculate route prices and channel capacity. Lower-grade routes may consume higher-grade stock, changing revenue and later sales.

**Recommendation:** Pass `quality=decision.get("quality")` to `markets.sell()`. Add a mixed-quality, capacity-limited routing test. Because this changes simulation output, run the full test suite and replay guard before accepting the fix.

### CQ-05: Auto-balance reports do not reliably describe the winning configuration

**Severity:** High  
**Category:** Tool correctness

`tools/auto_balance.py:495-526` reports accepted search moves, sorts them by individual score delta, and truncates them to `top_n`. These rows are sequential transitions rather than a baseline-to-final structural diff. The same setting can occur more than once, and sorted application order can reverse or omit dependent changes. Nevertheless, `tools/auto_balance.py:545-576` tells users to apply those rows.

**Impact:** Following the report can produce a configuration that was never evaluated and does not have the reported final score.

**Recommendation:** Generate proposed changes by diffing the original configuration against the final best configuration, with one final value per path. Keep accepted move history in a clearly separate diagnostics section.

### CQ-06: Chunked process-pool execution can reuse a stateful agent

**Severity:** High  
**Category:** Determinism / concurrency

`runner/batch_run.py:156-176` deep-copies agents only in sequential mode and assumes process-boundary pickling isolates every parallel task. `ProcessPoolExecutor.map()` batches work according to the `chunksize` calculated at `runner/batch_run.py:188-193`. Repeated references within one serialized chunk can preserve object identity, allowing consecutive jobs to mutate and reuse the same unpickled agent. Existing stateful-agent tests use workloads whose chunk size remains one.

**Impact:** A future stateful strategy can produce results dependent on worker count, window size, and chunk size, violating the documented reproducibility contract.

**Recommendation:** Instantiate from an agent class/factory inside `_run_in_worker()`, or deep-copy at worker entry. Add a stateful-agent test large enough to force `chunksize > 1` and compare sequential and parallel output.

### CQ-07: Report artifacts are not published atomically as a set

**Severity:** High  
**Category:** Reliability / concurrency

`main.py:278-305` performs three independent `os.replace()` operations. Rollback helps after a local exception, but readers can still observe artifacts from different runs between replacements. Concurrent batch processes can interleave publication and rollback. This is weaker than the atomic-set guarantee documented around `main.py:482-484` and in `CLAUDE.md`.

**Impact:** Consumers may pair a CSV, configuration snapshot, and summary from different batches. Concurrent writers can overwrite or restore stale artifacts.

**Recommendation:** Publish immutable run-specific directories and atomically switch a single manifest, symlink, or directory pointer. Add an inter-process lock if concurrent writers are unsupported. Test readers during publication and concurrent publishers.

### CQ-08: `ProgressionPlayer` repeats a known end-of-run off-by-one error

**Severity:** Medium  
**Category:** Correctness / duplication

`agents/progression_player.py:48-54` accepts a crop when `growth_days <= player.total_days - player.day`. The final executable day requires subtracting one more day. `ProfitOptimizer` already uses the correct condition and `tests/test_strategy_controls.py:184-196` documents the rejected old expression.

**Impact:** Near the end of a run, this strategy can buy and plant a contracted crop that will never mature, weakening it as a balance probe.

**Recommendation:** Centralize the run-horizon maturity predicate in `simulation/economy_rules.py` and use it from all agents and forecasts.

### CQ-09: Compiled-build verification is incomplete and fragile

**Severity:** Medium  
**Category:** Optional accelerator safety

`tools/build_cython.py:166-175` records Cython version, directives, and compiler flags, but `simulation/_compiled.py:94-123` verifies only manifest version, ABI tag, artifact presence, and source hashes. A build made with determinism-unsafe directives or flags can be accepted. In addition, valid JSON with missing entry keys can raise `KeyError` at `simulation/_compiled.py:107-120` instead of following the documented non-strict fallback path.

**Impact:** An unsafe artifact may silently violate bit-exact behavior, while a malformed manifest may prevent startup despite `FARM_COMPILED=1` promising fallback.

**Recommendation:** Validate the manifest schema and verify a build-recipe hash covering directives, required flags, builder version, compiler identity, and Cython version. Convert verification failures to fallback warnings in non-strict mode and retain fail-fast behavior in strict mode.

### CQ-10: Accelerator builds can destroy a working artifact before replacement succeeds

**Severity:** Medium  
**Category:** Build reliability

`tools/build_cython.py:119-157` deletes the complete active artifact directory before compilation. `tools/build_fastplot.py:47-59` compiles directly to the final extension path. A compiler failure can therefore remove a known-good build or expose partial output.

**Impact:** Optional performance tooling is less reliable than the pure-Python fallback, and failed rebuilds can leave confusing local state.

**Recommendation:** Build into a sibling temporary path, run import/equivalence checks, write the manifest last, and atomically replace the previous artifact only after success. Add failure-path tests with mocked compiler subprocesses.

### CQ-11: Visualization can crash on valid undefined metrics and labels the wrong measure

**Severity:** Medium  
**Category:** Reporting correctness / test coverage

`metrics/visualize.py:427-430` calls `max()` on crop-loss values without filtering `None`, which is valid for runs with no mature crops. `metrics/visualize.py:427-453` labels the x-axis as watering coverage of occupied plot-days but plots `watering_rate` instead of `occupied_watering_rate`. No tests import the visualization module, and Matplotlib is not declared in `requirements-dev.txt` or project extras.

**Impact:** Valid report data can crash chart generation, and successful charts can communicate a different metric from their label.

**Recommendation:** Filter undefined points, explicitly handle empty cohorts, and use the occupied-plot metric consistently. Declare a visualization dependency extra and add Agg-backend smoke tests for empty, partial, and representative CSV data.

### CQ-12: Packaging metadata does not define a reliable installable application

**Severity:** Medium  
**Category:** Packaging

`pyproject.toml:1-27` declares PEP 621 project metadata but has no build system, package discovery, package data, optional dependencies, or console entry point. Runtime configuration is loaded from checkout-relative JSON files, and CI never builds or installs a wheel.

**Impact:** The metadata suggests installation support without ensuring that Python packages and `config/*.json` are included or locatable after installation.

**Recommendation:** Either document the repository as source-tree-only and remove misleading packaging signals, or add an explicit backend, package-data/resource loading, console script, visualization extra, and wheel/sdist smoke tests from outside the checkout.

### CQ-13: Configuration validation does not consistently reject unknown keys

**Severity:** Medium  
**Category:** Data quality

Validation is extensive in `simulation/configuration.py:43-484`, but allowed-key enforcement is inconsistent across crop, upgrade, weather, market, buyer, and processing records. Tests in `tests/test_configuration.py` cover only a small subset of malformed schemas.

**Impact:** A misspelled optional balance setting can validate successfully and then be silently ignored or replaced by a default, producing hard-to-diagnose economic changes.

**Recommendation:** Define explicit allowed-key sets or a JSON Schema for every configuration object. Add table-driven tests for unknown fields, missing fields, duplicates, boolean-as-number values, non-finite numbers, boundaries, and cross-references.

### CQ-14: Supported-version claims exceed compiled-path CI coverage

**Severity:** Medium  
**Category:** CI coverage

`pyproject.toml:6-10` supports Python 3.12+, and pure-Python tests cover 3.12 through 3.14. Both compiled jobs and the pure golden replay job use only Python 3.14 at `.github/workflows/ci.yml:39-123`.

**Impact:** C/Cython build failures, ABI differences, or replay drift on the advertised Python 3.12 floor can ship undetected.

**Recommendation:** Run both accelerators and the replay check on Python 3.12 and 3.14. Prefer the pure replay check on every supported interpreter.

### CQ-15: Interrupted report staging is neither ignored nor cleaned

**Severity:** Low  
**Category:** Repository hygiene

`main.py:217` creates `reports/.batch-*` staging directories. `.gitignore:12-16` ignores final report file patterns but not those directories. The audit began with an existing untracked staging CSV under `reports/.batch-v51wqb2c/`.

**Impact:** Interrupted runs leave large untracked files, repository noise, and accumulating disk usage.

**Recommendation:** Ignore `reports/.batch-*/` and safely remove stale staging directories based on age and ownership, or stage them under the system temporary directory.

## Strengths

- `simulation/random_events.py` centralizes randomness, and the replay guard checks bit-exact outcomes across every registered strategy and fixed seed set.
- The engine remains the mutation boundary; agents generally return decisions rather than mutating `PlayerState`.
- The daily simulation order and floating-point constraints are documented with rationale instead of relying on convention.
- Configuration is data-driven and receives substantial load-time validation.
- Batch seeds are minted in a deterministic single-threaded order before dispatch.
- Ruff lint and formatting checks are clean.
- The test suite is broad, fast, and includes regression tests for previously reported issues.

## Recommended Remediation Order

1. Resolve the balance-policy/configuration/golden-baseline conflict so CI has an authoritative green gate.
2. Fix the contract horizon and processed-input scheduling forecasts.
3. Honor quality-specific sales decisions and run the replay guard.
4. Correct auto-balance final-diff reporting.
5. Guarantee agent isolation for chunked process-pool work.
6. Replace multi-file report publication with a single atomic pointer switch.
7. Harden compiled manifests and make accelerator builds transactional.
8. Cover visualization, configuration schemas, packaging, and supported compiled versions in CI.

## Residual Risk

This was a static and test-driven audit, not a production load test or a statistical balance review. The failing balance assertions identify policy drift but do not establish which economic values are desirable. Corrections under `simulation/` may intentionally alter replay output and must follow the repository's replay-review process rather than automatically updating the golden baseline.
