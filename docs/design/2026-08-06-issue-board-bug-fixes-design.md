# Issue Board Bug Fixes Design

## Scope

Fix every currently open issue except #1. Issues #3 and #8 are already fixed
and pushed; their existing implementations and tests remain in place. The
remaining work is split into simulation/decision correctness and reporting
correctness so each subsystem can be tested independently.

## Simulation And Decisions

### Yield effects (#4)

Configured neglect yield loss and fertilizer yield bonus will each be applied
exactly once in harvest calculation. Fertilizer's quality benefit remains a
separate quality-only effect. Zero and sub-baseline configured values must be
honored without hard-coded minimums.

### Storage timing (#6)

Inventory aging will capture the day's storage liability before agent actions.
The liability will be collected after sales and contract revenue, using the
existing nonnegative-cash policy. Inventory sold during the day still incurs
the liability captured at the start of the day.

### Growth upgrades (#7)

Multiple growth reductions will be applied in stable upgrade configuration
order, retaining current per-effect rounding semantics. This makes the result
independent of set iteration order while minimizing balance changes.

### Configuration and CLI validation (#9, #11)

Runtime configuration validation will cover required numeric ranges, ordered
ranges, enum values, cross-namespace IDs, and effect-specific fields before
simulation starts. CLI and programmatic batch entry points will reject invalid
runs, days, workers, and starting money before opening report artifacts.

### Random decisions (#13)

Random-agent decisions will use a dedicated deterministic stream incorporating
the recorded run seed, current day, and decision context. Identical seeds will
remain replayable while different seeds can produce different policy choices.

### Contract offers and feasibility (#12, #14)

Agent contract hooks will receive all unresolved, unexpired retained offers.
Feasibility will account for eligible inventory, quality, crop maturity,
funding, recipes, processing capacity, and in-flight jobs. Expiry will also be
defended at acceptance time.

### Optimizer sales (#16)

Optimizer sales will plan by item and quality tier, track channel capacity
while building decisions, and route profitable residual quantities to fallback
channels. Sales decisions will carry an optional quality constraint so the
market consumes the intended lots.

## Reporting And Publication

### Upgrade reach (#5)

Aggregates will expose first- and second-upgrade purchase counts and rates.
Timing remains available but is labeled as conditional on purchasing runs, and
warnings use reach rate rather than the conditional timing average.

### Seed provenance (#10)

Batch execution will resolve a concrete base seed, including when none is
provided, and record it in both the config snapshot and Markdown report.

### Serialized accounting (#17)

Exported money values will use one canonical cent-rounded representation.
Revenue and expense components will be rounded first; totals, net profit, and
profit variants will be derived from those canonical components so every row
reconciles at exported precision.

### Median memory (#15)

Streaming aggregation will replace unbounded exact median lists with
deterministic fixed-capacity reservoir samples and retain cohort counts without
duplicating lists during finalization. The report will document that medians
are approximate after the cap is exceeded.

### Atomic publication (#18)

CSV, snapshot, and Markdown artifacts will be written to a temporary directory
on the reports filesystem. Existing final files will be replaced only after
all staged writes succeed; staging files will be cleaned on success or failure.
Worker failures will include strategy and run-seed context.

## Verification

Each issue will receive focused regression tests. The full suite will then
verify sequential determinism, process-pool equivalence, malformed
configuration rejection, row-level accounting identities, bounded aggregation
memory, retained-offer behavior, quality-tier sales, seed provenance, and
failure-safe report publication.
