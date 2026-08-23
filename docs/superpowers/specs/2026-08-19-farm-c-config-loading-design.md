# farm-c Production Configuration Loading

## Goal

Make `farm-c` load the repository's existing `config/*.json` files in
production code instead of relying on test-only fixture loaders. Loading must
produce the existing immutable, indexed `ResolvedConfig` representation and
must reject configuration that the Python runtime would reject.

## Scope

The loader reads the eleven world files currently consumed by the simulator:

- `crops.json`
- `upgrades.json`
- `fertilizer.json`
- `watering_settings.json`
- `soil.json`
- `weather.json`
- `markets.json`
- `contracts.json`
- `buyers.json`
- `processing.json`
- `storage.json`

Simulation settings remain separate from world configuration because they
control a run rather than the immutable economy. They will be loaded through a
small validated `SimulationSettings` structure so the future runner can use
the same directory API without putting run parameters in `ResolvedConfig`.

This work does not implement the daily engine, batch runner, reporting, or
generated configuration tables.

## Public API

Add a directory-oriented API in `config.h`:

```c
bool config_load_directory(const char *directory,
                           ResolvedConfig *out,
                           ConfigError *error);
bool config_load_simulation_settings(const char *directory,
                                     SimulationSettings *out,
                                     ConfigError *error);
void config_destroy(ResolvedConfig *config);
```

`ConfigError` contains a stable error code and a human-readable message with
the file and JSON path when available. The loader initializes output objects
before doing work, so callers may always call the matching destroy function.
On failure, no partially-owned allocation is exposed as a successful config.

The loader preserves JSON array order when assigning integer IDs. String IDs
are used only while parsing and resolving references. `ResolvedConfig` owns
all strings, arrays, and nested buyer item lists; callers must not free or
mutate them.

## Parsing and Resolution

Vendor cJSON for production use under the farm-c source tree, separate from
the test fixture copy. Each file is opened, parsed as the expected JSON root
type, and closed before its temporary parse tree is released.

Parsing proceeds in dependency order:

1. Parse crops and upgrades and assign IDs.
2. Parse processing products and create product entries in the unified item
   table, then parse recipes.
3. Parse buyers and resolve their allowed item IDs.
4. Parse markets and resolve channel IDs.
5. Parse the scalar world sections and weather seasons.
6. Resolve crop unlock upgrades, recipe items, buyer items, and all other
   references before publishing the result.

Defaults are applied at load time exactly where the Python derived/configuration
layers apply them: nutrient demand, crop environmental fields,
seasonal demand, fertilizer optional fields, soil dynamics/regen, weather
season values, contract optional fields, market channel optional fields,
storage values, recipe quality/shelf life, and buyer optional terms.

## Validation

Validation is closed-schema and fail-fast. It must cover the Python validator's
current behavior:

- expected root types, required fields, and non-empty string IDs;
- unknown-field rejection, including effect-specific union fields;
- unique crop, product, upgrade, recipe, buyer, and channel IDs;
- valid enum values for quality, season, unlock, role, and upgrade effects;
- finite numeric values and the existing nonnegative, bounded, ordered-range,
  and strictly-positive constraints;
- required `spot` market channel;
- valid crop unlock upgrade references;
- valid recipe input/output item references;
- valid buyer allowed-item references;
- valid array sizes and allocation arithmetic.

Errors identify the source file and logical path, such as
`markets.json: channels[0].min_quality`, rather than exposing cJSON internals.
Missing files, unreadable files, malformed JSON, wrong root types, and
allocation failures are all loader errors.

## Ownership and Failure Safety

The loader uses temporary parsed JSON trees and a temporary `ResolvedConfig`.
Every failure path releases both. `config_destroy` releases nested arrays and
all owned strings and resets the structure to zero. No pointer in the final
configuration refers to a cJSON tree or a temporary file buffer.

The implementation must reject non-finite numeric values even if the JSON
parser accepts them, and must check size conversions before allocating. It
must not silently coerce a missing, null, boolean, string, or fractional value
to zero or an integer.

## Tests and Build Changes

Add a production-loader test binary and include it in `make test`. Tests will:

- load the real shipped directory and assert representative resolved values,
  defaults, IDs, and cross-references;
- load simulation settings and validate their constraints;
- reject malformed JSON, unknown fields, missing required fields, duplicate
  IDs, invalid enum/range values, non-finite values, and unknown references;
- verify failure cleanup under AddressSanitizer/UndefinedBehaviorSanitizer;
- verify `config_destroy` is safe after both success and failure.

Update `farm-c/README.md` and the build rules to document the production
loader, its directory argument, ownership contract, and the fact that the
fixture loaders remain test-only.

## Compatibility

The loader is a prerequisite for the future C engine and does not change the
already-tested simulation algorithms. It must retain source order because
array order can affect deterministic ID assignment and later RNG-sensitive
iteration. The existing test fixture loaders may remain temporarily for their
fixture-specific schemas, but production code must not depend on test files.
