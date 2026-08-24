/* Tests for src/progress.c -- the live batch progress line and elapsed-time
 * reporting added alongside `farm-c batch --progress`/`--no-progress`.
 *
 * Not a fixture-driven parity suite: this is a cosmetic/diagnostic feature
 * (../CLAUDE.md's Section 7 bit-exact contract doesn't cover it, same as
 * ../../runner/progress.py isn't checked against a C port), so these are
 * plain assertions against hand-computed expected text rather than
 * recorded Python output.
 */
/* mkstemp is not ISO C -- see tests/test_config_invalid.c's comment on
 * _POSIX_C_SOURCE/_DARWIN_C_SOURCE for why both are needed and must come
 * before any header is included. */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE

#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "progress.h"

static int failures;

static void report(bool ok, const char *name) {
    if (!ok) {
        failures++;
        printf("FAIL %s\n", name);
    }
}

static bool streq(const char *a, const char *b) { return strcmp(a, b) == 0; }

static void test_format_duration(void) {
    char buf[32];

    progress_format_duration(0.0, buf, sizeof(buf));
    report(streq(buf, "00:00"), "duration: zero");

    progress_format_duration(59.4, buf, sizeof(buf));
    report(streq(buf, "00:59"), "duration: rounds down within a second");

    progress_format_duration(59.6, buf, sizeof(buf));
    report(streq(buf, "01:00"), "duration: rounds up into the next minute");

    progress_format_duration(125.0, buf, sizeof(buf));
    report(streq(buf, "02:05"), "duration: minutes and seconds");

    progress_format_duration(3725.0, buf, sizeof(buf));
    report(streq(buf, "1:02:05"), "duration: past an hour switches to H:MM:SS");

    progress_format_duration(-5.0, buf, sizeof(buf));
    report(streq(buf, "00:00"), "duration: negative clamps to zero");

    progress_format_duration(NAN, buf, sizeof(buf));
    report(streq(buf, "--:--"), "duration: NaN is unknown");

    progress_format_duration(INFINITY, buf, sizeof(buf));
    report(streq(buf, "--:--"), "duration: infinite is unknown");
}

static void test_format_rate(void) {
    char buf[32];

    progress_format_rate(NAN, buf, sizeof(buf));
    report(streq(buf, "--"), "rate: NaN is unknown");

    progress_format_rate(0.0, buf, sizeof(buf));
    report(streq(buf, "0.00"), "rate: zero uses two decimals");

    progress_format_rate(9.999, buf, sizeof(buf));
    report(streq(buf, "10.00"), "rate: below 10 uses two decimals");

    progress_format_rate(10.0, buf, sizeof(buf));
    report(streq(buf, "10.0"), "rate: at 10 switches to one decimal");

    progress_format_rate(99.94, buf, sizeof(buf));
    report(streq(buf, "99.9"), "rate: just under 100 keeps one decimal");

    progress_format_rate(100.0, buf, sizeof(buf));
    report(streq(buf, "100"), "rate: at 100 switches to a whole number");

    progress_format_rate(12345.6, buf, sizeof(buf));
    report(streq(buf, "12,346"), "rate: large values are comma-grouped");
}

static void test_render_line_basics(void) {
    char buf[PROGRESS_LINE_MAX];

    /* Nothing completed yet: no rate/ETA to report. */
    progress_render_line(0, 100, 0.0, buf, sizeof(buf), 0, 10, true);
    report(strstr(buf, "0.0%") != NULL, "render: 0/100 reads as 0.0%%");
    report(strstr(buf, "--:-- left") != NULL, "render: no throughput yet means no ETA");
    report(strstr(buf, "0/100") != NULL, "render: counts padded to total's width");

    /* Halfway, steady rate. */
    progress_render_line(50, 100, 10.0, buf, sizeof(buf), 0, 10, true);
    report(strstr(buf, "50.0%") != NULL, "render: 50/100 reads as 50.0%%");
    report(strstr(buf, "5.00 sim/s") != NULL, "render: rate is completed/elapsed");
    report(strstr(buf, "00:10 left") != NULL, "render: ETA is remaining/rate");

    /* Complete: remaining is pinned to zero even though total==completed
     * would otherwise divide cleanly. */
    progress_render_line(100, 100, 10.0, buf, sizeof(buf), 0, 10, true);
    report(strstr(buf, "100.0%") != NULL, "render: 100/100 reads as 100.0%%");
    report(strstr(buf, "00:00 left") != NULL, "render: finished run has zero ETA");

    /* Floored, not rounded: 999/1000 must not read as 100%. */
    progress_render_line(999, 1000, 100.0, buf, sizeof(buf), 0, 10, true);
    report(strstr(buf, "99.9%") != NULL, "render: 999/1000 floors to 99.9%%, not 100%%");

    /* ASCII fallback uses '#'/'-' instead of the Unicode block glyphs. */
    progress_render_line(5, 10, 1.0, buf, sizeof(buf), 0, 10, true);
    report(buf[0] == '[' && (buf[1] == '#' || buf[1] == '-'), "render: ascii_only avoids UTF-8 glyphs");

    /* Unicode bar: a fully-filled 4-cell bar is four 3-byte U+2588 glyphs. */
    progress_render_line(4, 4, 1.0, buf, sizeof(buf), 0, 4, false);
    report(strncmp(buf, "[\xE2\x96\x88\xE2\x96\x88\xE2\x96\x88\xE2\x96\x88]", 14) == 0,
          "render: full bar is four repeated FULL BLOCK glyphs");

    /* total == 0 renders as complete rather than dividing by zero. */
    progress_render_line(0, 0, 5.0, buf, sizeof(buf), 0, 10, true);
    report(strstr(buf, "100.0%") != NULL, "render: zero total reads as already done");
}

static void test_render_line_truncates_to_width(void) {
    char buf[PROGRESS_LINE_MAX];
    progress_render_line(50, 100, 10.0, buf, sizeof(buf), 20, 10, true);
    report(strlen(buf) == 20, "render: positive width truncates the line");

    progress_render_line(50, 100, 10.0, buf, sizeof(buf), 0, 10, true);
    report(strlen(buf) > 20, "render: width==0 means no truncation (sanity check on the above)");
}

/* A fake stream that discards output but still round-trips isatty()==false
 * so ProgressReporter's draw() runs its normal codepath without polluting
 * test output with carriage-return spam. */
static void test_reporter_counts_without_drawing(void) {
    FILE *sink = fopen("/dev/null", "w");
    assert(sink != NULL);

    ProgressReporter p;
    progress_init(&p, 3, sink, false /* enabled */);
    report(progress_elapsed_seconds(&p) == 0.0, "reporter: elapsed is zero before start()");

    progress_start(&p);
    progress_advance(&p, 1);
    progress_advance(&p, 2);
    report(p.completed == 3, "reporter: advance() accumulates by count");
    report(progress_elapsed_seconds(&p) >= 0.0, "reporter: elapsed is non-negative while running");

    progress_finish(&p);
    report(!p.running, "reporter: finish() stops the clock");
    report(!p.drew_anything, "reporter: disabled reporter never draws");
    double frozen = progress_elapsed_seconds(&p);
    report(progress_elapsed_seconds(&p) == frozen,
          "reporter: elapsed is frozen after finish(), not still advancing");

    fclose(sink);
}

static void test_reporter_draws_when_enabled(void) {
    char path[] = "/tmp/farm_c_progress_test_XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    FILE *stream = fdopen(fd, "w+");
    assert(stream != NULL);

    ProgressReporter p;
    progress_init(&p, 2, stream, true /* enabled */);
    progress_start(&p);
    progress_advance(&p, 1);
    progress_advance(&p, 1);
    progress_finish(&p);
    report(p.drew_anything, "reporter: enabled reporter draws at least once");

    fflush(stream);
    long size = ftell(stream);
    report(size > 0, "reporter: enabled reporter wrote bytes to its stream");

    fclose(stream);
    remove(path);
}

int main(void) {
    test_format_duration();
    test_format_rate();
    test_render_line_basics();
    test_render_line_truncates_to_width();
    test_reporter_counts_without_drawing();
    test_reporter_draws_when_enabled();

    if (failures > 0) {
        printf("%d failed\n", failures);
        return 1;
    }
    puts("progress tests passed");
    return 0;
}
