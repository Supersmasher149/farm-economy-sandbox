# farm-c End-to-End Single Run

## Goal

Wire configuration loading, strategy selection, state initialization, the
modern 24-step engine, and final reporting into one reusable single-run path
and a `farm-c single` command.

This milestone does not add batch execution, replay verification, CSV/JSON
reporting, charts, or the legacy `world=None` simulation path.

## Public Runner API

Add `farm-c/include/runner.h` with a reusable run lifecycle:

```c
typedef struct {
    bool has_seed;
    uint64_t seed;
} RunSeed;

typedef struct {
    FarmState state;
    uint64_t seed;
    int days_simulated;
} RunResult;

typedef enum {
    RUNNER_ERROR_NONE,
    RUNNER_ERROR_ARGUMENT,
    RUNNER_ERROR_SEED,
    RUNNER_ERROR_ENGINE,
    RUNNER_ERROR_ALLOCATION
} RunnerErrorCode;

typedef struct {
    RunnerErrorCode code;
    char message[256];
} RunnerError;

typedef void (*RunDayCallback)(const FarmState *state, const WeatherDay *weather,
                               void *context);

bool runner_run_single(const ResolvedConfig *config,
                       const SimulationSettings *settings,
                       const Agent *agent,
                       RunSeed requested_seed,
                       RunDayCallback on_day,
                       void *context,
                       RunResult *out,
                       RunnerError *error);
void runner_run_result_destroy(RunResult *result);
```

The API owns the returned `FarmState` until `runner_run_result_destroy` is
called. It borrows configuration and agent memory. The callback receives a
read-only state after each completed day and must not mutate it. `RunSeed`
with `has_seed == false` causes the runner to generate a fresh 64-bit seed; the
actual seed is always stored in `RunResult`.

The runner initializes a fresh state from settings: starting money, slots,
operating reserve, total-day horizon, initial soil values, and the selected
run seed. It calls the engine once per day, stopping at the configured horizon
or immediately after bankruptcy. Engine/config/argument/allocation failures
are returned as `RunnerError`; ordinary rejected actions remain simulation
outcomes.

## CLI

Add `farm-c/main.c` and a `farm-c` executable target:

```text
farm-c single [--strategy NAME] [--seed INT] [--config DIR] [--verbose]
```

Defaults match the Python single command:

- strategy: `profit_optimizer`;
- config directory: `../config` when launched from `farm-c`;
- seed: generated when omitted;
- verbose: off.

The command prints the selected strategy, actual seed, simulated days, final
money, revenue, expenses, planted/harvested/sold totals, upgrades, idle days,
bankruptcy status/reason, lowest/highest money, watering coverage,
fertilizer totals, quality totals, processing totals, contract totals,
reputation, and revenue by channel. `--verbose` adds one human-readable line
per completed day from the callback. Output is intentionally not a stable
machine-readable reporting format yet.

Invalid arguments, unknown strategies, missing/malformed configuration, and
run failures print a concise diagnostic to stderr and exit nonzero. Successful
runs exit zero, including runs that end in bankruptcy.

## Seed Generation

Explicit seeds use the existing Python-compatible `rng_seed` path and are
fully reproducible. Omitted seeds are generated from the platform's secure
random source where available, with a clock-based fallback only if that source
cannot be opened. The chosen seed is printed so the run can be reproduced.

## Ownership and Failure Safety

`runner_run_single` initializes `RunResult` before work. Every failure path
destroys any partially initialized state and leaves the result destroyable.
The runner never frees `ResolvedConfig` or the selected `Agent`. Callback
execution is outside ownership of the runner; the callback context belongs to
the caller.

## Tests and Build

Add a runner test binary and CLI target to `make test` covering:

- explicit-seed repeatability and actual seed preservation;
- omitted-seed execution and returned seed validity;
- initial soil/settings propagation;
- callback count and immutable post-day observations;
- bankruptcy stopping before the configured horizon;
- invalid strategy, config, and argument failures;
- CLI summary, verbose history, exit status, and default strategy behavior.

The existing configuration, engine, agent, RNG, physics, and mutation tests
must remain green under ASan/UBSan. The README will document build and single
run commands plus the result ownership contract.
