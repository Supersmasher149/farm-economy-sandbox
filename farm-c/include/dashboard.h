/* Self-contained interactive HTML report for `farm-c batch --html PATH` --
 * the C analogue of ../metrics/dashboard.py, and the "HTML dashboard" entry
 * in docs/c-port-plan.md Section 12's Later Version list.
 *
 * Design, and why it differs from the Python one:
 *
 *   - Fed from the batch's BatchRunCallback, not from run_results.csv. The
 *     CSV main.c writes is a 22-column scalar subset; BatchRunResult also
 *     carries crop_plant_counts, total_crops_lost, total_harvest_events,
 *     slot_days and first_upgrade_day, which the crop-mix and
 *     watering-vs-loss panels need. Reading the CSV back would cap this
 *     permanently at what the CSV happens to record. The tradeoff: --html
 *     renders the batch you are running, not an archived one.
 *
 *   - Charts are drawn in the browser, from data this module emits; no
 *     geometry is computed in C. matplotlib has no C equivalent worth
 *     vendoring, and the alternatives all cost more than they save --
 *     gnuplot via popen needs an external binary and yields static images,
 *     a vendored JS chart library adds a third-party blob and still leaves
 *     the sortable table hand-rolled, and C-computed static SVG cannot
 *     sort, filter, or show a tooltip. The page therefore ships ~1 screen
 *     of vanilla JS (src/dashboard_js.h) and no dependencies at all: no
 *     CDN, no external stylesheet, no network access. It opens offline,
 *     which is the same property metrics/dashboard.py preserves by
 *     base64-inlining its PNGs.
 *
 *   - Streaming, like the --csv path beside it. Rows are written straight
 *     through to the file as they arrive, so peak memory stays bounded by
 *     strategy count rather than run count -- the guarantee
 *     include/batch.h makes and that a 1000-run batch would otherwise
 *     break. That is also why the payload is printed with fprintf rather
 *     than built as a cJSON tree: cJSON's printer is round-trip safe, but
 *     assembling one would hold the entire batch in memory.
 *
 * Precision: the emitted payload is a *display* artifact. Doubles are
 * written at DASHBOARD_DOUBLE_FORMAT precision, not the %.17g the CSV uses
 * -- run_results.csv stays the exact record of a run. Seeds are integers
 * and so are always exact, which matters because the page offers each
 * point's seed as a copyable `farm-c single --seed N` command.
 */
#ifndef FARM_DASHBOARD_H
#define FARM_DASHBOARD_H

#include <stdbool.h>
#include <stddef.h>

#include "aggregate.h"
#include "batch.h"
#include "config.h"

typedef struct DashboardWriter DashboardWriter;

/* Opens `path` for writing and emits everything up to and including the
 * opening bracket of the run array. Returns NULL if the file cannot be
 * opened or memory cannot be allocated; the caller reports the error, the
 * same way cmd_batch reports a failed --csv fopen.
 *
 * `config`, `strategy_names` and `settings` are borrowed for the duration
 * of the call only -- everything needed later is copied or emitted now. */
DashboardWriter *dashboard_open(const char *path, const ResolvedConfig *config,
                                const char *const *strategy_names, size_t strategy_count,
                                const SimulationSettings *settings);

/* Appends one run. Safe to call with a NULL writer (the --html-not-
 * requested case), so the callback stays a single unconditional line. */
void dashboard_add_run(DashboardWriter *writer, const BatchRunResult *result);

/* Closes the run array, emits the per-strategy summary and the balance
 * warnings, then the script and the page epilogue, and closes the file.
 * `agg` is the parallel array of the same `strategy_count` accumulators
 * cmd_batch already keeps for its terminal table, so both renderings
 * report identical numbers.
 *
 * Frees `writer` unconditionally. Returns false if any write failed or the
 * file did not close cleanly -- errors are checked once here rather than
 * per row, so a full disk is reported rather than producing a silently
 * truncated page. Safe to call with a NULL writer, which returns true. */
bool dashboard_close(DashboardWriter *writer, const StrategyAgg *agg, size_t strategy_count,
                     int total_days, double start_money);

#endif /* FARM_DASHBOARD_H */
