#ifndef FARM_C_PROGRESS_H
#define FARM_C_PROGRESS_H

/* Live progress reporting for long batch runs, and the plain elapsed-time
 * totals every command reports regardless of whether a live bar was drawn.
 *
 * Mirrors ../runner/progress.py's shape and constraints, though this is a
 * cosmetic/diagnostic feature, not part of the bit-exact Python contract
 * (../CLAUDE.md's Section 7): the rendered text is not fixture-tested
 * against Python, and nothing here reads FarmRng or touches FarmState.
 *
 * Two deliberate constraints, same as the Python original:
 *
 *   - The live bar goes to stderr only, so `farm-c batch > report.txt`
 *     stays clean and `--csv`/`--html` output is unaffected.
 *   - progress_advance() only counts and (optionally) draws; it never
 *     touches simulation state or the RNG, so turning the bar on or off
 *     cannot change a batch's outcome for a given seed -- the same
 *     "outside determinism" boundary ../CLAUDE.md documents for
 *     runner/progress.py.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

#define PROGRESS_BAR_WIDTH 28
/* Bar glyphs are 3-byte UTF-8 each; generous headroom for counts/rate/
 * duration text and a NUL besides. */
#define PROGRESS_LINE_MAX 256

/* --- Pure, I/O-free renderers ---------------------------------------------
 * Kept free of wall-clock reads (unlike ProgressReporter below) so exact
 * rendering can be asserted from plain numbers -- the same separation
 * render_line()/format_duration()/format_rate() keep in the Python
 * original for testability. */

/* Renders seconds as MM:SS, or H:MM:SS past an hour. NaN/infinite/negative
 * renders as "--:--" (nothing to report yet). `buf_size` must be at least
 * 16. */
void progress_format_duration(double seconds, char *buf, size_t buf_size);

/* Renders a rate (completions/second), with precision only where it
 * informs: >=100 -> comma-grouped whole number, >=10 -> one decimal, else
 * two. NaN/infinite/negative renders as "--". `buf_size` must be at least
 * 16. */
void progress_format_rate(double rate, char *buf, size_t buf_size);

/* Builds one status line -- bar, percent, done/total, rate, elapsed,
 * estimated time left -- for a given point in a batch. `percent` is
 * floored to one decimal place, not rounded, so 9,999 of 10,000 runs
 * cannot read as 100%. `buf_size` must be at least PROGRESS_LINE_MAX.
 * `width`, if > 0, truncates the rendered line to that many bytes (0 = no
 * truncation). `ascii_only` selects '#'/'-' fill instead of the Unicode
 * block glyphs, for streams that cannot encode them. */
void progress_render_line(size_t completed, size_t total, double elapsed, char *buf,
                          size_t buf_size, int width, int bar_width, bool ascii_only);

/* --- Stateful reporter ------------------------------------------------- */

typedef struct {
    size_t total;
    FILE *stream;
    bool enabled;          /* draw the live bar at all */
    bool ascii_only;
    double min_interval_seconds;

    size_t completed;
    bool running;
    double started_at;     /* monotonic seconds; valid only while running */
    double final_elapsed;  /* frozen at progress_finish() */
    bool has_last_draw;
    double last_draw_at;
    size_t last_width;
    bool drew_anything;
} ProgressReporter;

/* `enabled` controls only whether the live bar is drawn -- timing is always
 * tracked once progress_start() runs, so progress_elapsed_seconds() after
 * progress_finish() is a reliable "how long it took" total independent of
 * whether anything was ever drawn to `stream`. */
void progress_init(ProgressReporter *p, size_t total, FILE *stream, bool enabled);
void progress_start(ProgressReporter *p);
void progress_advance(ProgressReporter *p, size_t count);
void progress_finish(ProgressReporter *p);

/* Seconds since progress_start(); frozen at the final total once
 * progress_finish() has run. 0 before progress_start(). */
double progress_elapsed_seconds(const ProgressReporter *p);

/* Wall-clock seconds from a monotonic clock (CLOCK_MONOTONIC), for
 * reporters and for one-off elapsed-time measurements (e.g. `single`'s
 * per-run timing, which has no progress bar of its own). */
double progress_now_seconds(void);

#endif
