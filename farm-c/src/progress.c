#include "progress.h"

#include <math.h>
#include <string.h>
#include <time.h>

double progress_now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

void progress_format_duration(double seconds, char *buf, size_t buf_size) {
    if (isnan(seconds) || isinf(seconds)) {
        snprintf(buf, buf_size, "--:--");
        return;
    }
    long total = (long)llround(seconds);
    if (total < 0) total = 0;
    long hours = total / 3600;
    long remainder = total % 3600;
    long minutes = remainder / 60;
    long secs = remainder % 60;
    if (hours > 0) {
        snprintf(buf, buf_size, "%ld:%02ld:%02ld", hours, minutes, secs);
    } else {
        snprintf(buf, buf_size, "%02ld:%02ld", minutes, secs);
    }
}

/* Right-justifies a non-negative integer's decimal digits with ',' every
 * three digits (e.g. 12345 -> "12,345"). `buf_size` must be large enough
 * for the full grouped text plus a NUL; truncates (still NUL-terminated)
 * rather than overflow if not. */
static void format_grouped(long value, char *buf, size_t buf_size) {
    if (value < 0) value = 0; /* rates/counts here are never negative */
    char digits[32];
    snprintf(digits, sizeof(digits), "%ld", value);
    size_t ndigits = strlen(digits);

    size_t out = 0;
    for (size_t i = 0; i < ndigits; i++) {
        if (i > 0 && (ndigits - i) % 3 == 0 && out + 1 < buf_size) buf[out++] = ',';
        if (out + 1 < buf_size) buf[out++] = digits[i];
    }
    buf[out < buf_size ? out : buf_size - 1] = '\0';
}

void progress_format_rate(double rate, char *buf, size_t buf_size) {
    if (isnan(rate) || isinf(rate) || rate < 0.0) {
        snprintf(buf, buf_size, "--");
        return;
    }
    if (rate >= 100.0) {
        format_grouped((long)llround(rate), buf, buf_size);
        return;
    }
    if (rate >= 10.0) {
        snprintf(buf, buf_size, "%.1f", rate);
        return;
    }
    snprintf(buf, buf_size, "%.2f", rate);
}

void progress_render_line(size_t completed, size_t total, double elapsed, char *buf,
                          size_t buf_size, int width, int bar_width, bool ascii_only) {
    double fraction;
    if (total == 0) {
        fraction = 1.0;
    } else {
        fraction = (double)completed / (double)total;
        if (fraction < 0.0) fraction = 0.0;
        if (fraction > 1.0) fraction = 1.0;
    }

    bool has_rate = elapsed > 0.0 && completed > 0;
    double rate = has_rate ? (double)completed / elapsed : NAN;
    double remaining = has_rate ? ((double)total - (double)completed) / rate : NAN;
    if (completed >= total) remaining = 0.0;

    if (bar_width < 1) bar_width = 1;
    /* `bar` below is sized for PROGRESS_BAR_WIDTH cells; every caller in
     * this codebase passes that constant, but clamp defensively rather
     * than trust it. */
    if (bar_width > PROGRESS_BAR_WIDTH) bar_width = PROGRESS_BAR_WIDTH;
    int filled_cells = (int)(fraction * (double)bar_width);
    if (filled_cells > bar_width) filled_cells = bar_width;
    if (filled_cells < 0) filled_cells = 0;
    int empty_cells = bar_width - filled_cells;

    /* U+2588 FULL BLOCK / U+2591 LIGHT SHADE, matching runner/progress.py's
     * _BLOCKS -- same glyphs, so a terminal that renders one renders both. */
    const char *filled_glyph = ascii_only ? "#" : "\xE2\x96\x88";
    const char *empty_glyph = ascii_only ? "-" : "\xE2\x96\x91";

    char bar[PROGRESS_BAR_WIDTH * 3 + 1];
    size_t bar_len = 0;
    for (int i = 0; i < filled_cells && bar_len + 4 <= sizeof(bar); i++) {
        size_t glyph_len = strlen(filled_glyph);
        memcpy(bar + bar_len, filled_glyph, glyph_len);
        bar_len += glyph_len;
    }
    for (int i = 0; i < empty_cells && bar_len + 4 <= sizeof(bar); i++) {
        size_t glyph_len = strlen(empty_glyph);
        memcpy(bar + bar_len, empty_glyph, glyph_len);
        bar_len += glyph_len;
    }
    bar[bar_len] = '\0';

    /* Floored, not rounded: 9,999 of 10,000 runs must not read as 100%. */
    double percent = floor(fraction * 1000.0) / 10.0;

    char total_text[32];
    format_grouped((long)total, total_text, sizeof(total_text));
    char completed_text[32];
    format_grouped((long)completed, completed_text, sizeof(completed_text));

    char rate_text[16];
    progress_format_rate(rate, rate_text, sizeof(rate_text));
    char elapsed_text[16];
    progress_format_duration(elapsed, elapsed_text, sizeof(elapsed_text));
    char remaining_text[16];
    progress_format_duration(remaining, remaining_text, sizeof(remaining_text));

    size_t total_width = strlen(total_text);
    size_t completed_width = strlen(completed_text);
    int pad = (int)(total_width > completed_width ? total_width - completed_width : 0);

    int written = snprintf(buf, buf_size, "[%s] %5.1f%% | %*s/%s | %s sim/s | %s elapsed | %s left",
                           bar, percent, pad + (int)completed_width, completed_text, total_text,
                           rate_text, elapsed_text, remaining_text);

    if (width > 0 && written > width && (size_t)written < buf_size) buf[width] = '\0';
}

static void draw(ProgressReporter *p, bool force) {
    if (!p->enabled || p->total == 0) return;
    double now = progress_now_seconds();
    if (!force && p->has_last_draw && (now - p->last_draw_at) < p->min_interval_seconds) return;
    p->last_draw_at = now;
    p->has_last_draw = true;

    char line[PROGRESS_LINE_MAX];
    progress_render_line(p->completed, p->total, progress_elapsed_seconds(p), line, sizeof(line),
                         0, PROGRESS_BAR_WIDTH, p->ascii_only);
    size_t len = strlen(line);

    fputc('\r', p->stream);
    fputs(line, p->stream);
    /* Pad to the previous line's width so a shorter line cannot leave stale
     * characters behind from the one it overwrites. */
    for (size_t i = len; i < p->last_width; i++) fputc(' ', p->stream);
    p->last_width = len;
    fflush(p->stream);
    p->drew_anything = true;
}

void progress_init(ProgressReporter *p, size_t total, FILE *stream, bool enabled) {
    memset(p, 0, sizeof(*p));
    p->total = total;
    p->stream = stream != NULL ? stream : stderr;
    p->enabled = enabled;
    p->min_interval_seconds = 0.1;
    p->ascii_only = false;
}

void progress_start(ProgressReporter *p) {
    p->started_at = progress_now_seconds();
    p->running = true;
    p->final_elapsed = 0.0;
    p->has_last_draw = false;
    p->last_width = 0;
    p->drew_anything = false;
    p->completed = 0;
    draw(p, true);
}

double progress_elapsed_seconds(const ProgressReporter *p) {
    if (!p->running) return p->final_elapsed;
    double elapsed = progress_now_seconds() - p->started_at;
    return elapsed < 0.0 ? 0.0 : elapsed;
}

void progress_advance(ProgressReporter *p, size_t count) {
    p->completed += count;
    draw(p, false);
}

void progress_finish(ProgressReporter *p) {
    if (!p->running) return;
    draw(p, true);
    if (p->drew_anything) {
        fputc('\n', p->stream);
        fflush(p->stream);
    }
    p->final_elapsed = progress_elapsed_seconds(p);
    p->running = false;
}
