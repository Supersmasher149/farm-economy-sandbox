/* See include/dashboard.h for the design and the scope decisions behind it. */
#include "dashboard.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "farm_types.h"
#include "warnings.h"

#include "dashboard_js.h"

/* Display precision for the embedded payload. Deliberately not the %.17g
 * write_csv_row uses: the CSV is the exact record of a run, this page is a
 * rendering of it, and 17 significant digits per double would inflate the
 * file by roughly a third to show digits no chart can resolve. 10 is well
 * past the precision of anything on screen. */
#define DASHBOARD_DOUBLE_FORMAT "%.10g"

struct DashboardWriter {
    FILE *out;
    size_t rows;
    size_t warnings_written;
    size_t crop_count;
    size_t strategy_count;
    /* Parallel to the strategy_names cmd_batch passed, so a run's strategy
     * resolves to the same index the SUMMARY array uses. Borrowed from the
     * caller, whose names[] outlives the writer. */
    const char *const *strategy_names;
    /* Borrowed for crop labels in the mix panel's legend. */
    const ResolvedConfig *config;
};

/* Emits `text` as the body of a JSON string (without the surrounding
 * quotes), escaped.
 *
 * Strategy and crop ids are safe identifiers today, so this is defensive
 * rather than load-bearing -- but write_csv_row's decision to skip quoting
 * entirely is a latent bug waiting on the first id with a comma in it, and
 * the failure mode here is worse than a malformed cell: an unescaped < or a
 * quote breaks the page, and an embedded closing script tag would turn
 * config data into executable markup. Escaping < and > (not required by
 * JSON) is what makes the "</script" sequence unrepresentable in the
 * output, so the payload can safely live inside the script block. */
static void write_json_escaped(FILE *out, const char *text) {
    for (const unsigned char *p = (const unsigned char *)text; *p != '\0'; p++) {
        switch (*p) {
        case '"': fputs("\\\"", out); break;
        case '\\': fputs("\\\\", out); break;
        case '\n': fputs("\\n", out); break;
        case '\r': fputs("\\r", out); break;
        case '\t': fputs("\\t", out); break;
        case '<': fputs("\\u003c", out); break;
        case '>': fputs("\\u003e", out); break;
        case '&': fputs("\\u0026", out); break;
        default:
            if (*p < 0x20) {
                fprintf(out, "\\u%04x", *p);
            } else {
                fputc(*p, out);
            }
            break;
        }
    }
}

static void write_json_string(FILE *out, const char *text) {
    fputc('"', out);
    write_json_escaped(out, text != NULL ? text : "");
    fputc('"', out);
}

static void write_double(FILE *out, double value) {
    /* JSON has no NaN/Infinity literal; null renders as "--" in the page,
     * which is the honest display for a value that is not a number. */
    if (value != value || value > 1e308 || value < -1e308) {
        fputs("null", out);
    } else {
        fprintf(out, DASHBOARD_DOUBLE_FORMAT, value);
    }
}

/* The crop's external id, via the unified item table (a crop is an item,
 * per docs/c-port-plan.md Section 1). */
static const char *crop_label(const ResolvedConfig *config, size_t index) {
    const ItemDef *item = config_find_item(config, config->crops[index].item_id);
    return item != NULL && item->external_id != NULL ? item->external_id : "?";
}

static size_t strategy_index(const DashboardWriter *w, const char *name) {
    for (size_t i = 0; i < w->strategy_count; i++)
        if (strcmp(w->strategy_names[i], name) == 0) return i;
    return w->strategy_count > 0 ? w->strategy_count - 1 : 0;
}

DashboardWriter *dashboard_open(const char *path, const ResolvedConfig *config,
                                const char *const *strategy_names, size_t strategy_count,
                                const SimulationSettings *settings) {
    DashboardWriter *w = calloc(1, sizeof(*w));
    if (w == NULL) return NULL;
    w->out = fopen(path, "w");
    if (w->out == NULL) {
        free(w);
        return NULL;
    }
    w->config = config;
    w->crop_count = config->crop_count;
    w->strategy_names = strategy_names;
    w->strategy_count = strategy_count;

    FILE *o = w->out;
    fputs("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n", o);
    fputs("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n", o);
    fputs("<title>farm-c batch report</title>\n<style>\n", o);
    fputs(DASHBOARD_CSS, o);
    fputs("</style>\n</head>\n<body>\n", o);

    fputs("<h1>farm-c batch report</h1>\n<div class=\"sub\">", o);
    fprintf(o, "%d days &middot; start money %.2f &middot; %zu strategies", settings->days,
            settings->start_money, strategy_count);
    fputs("</div>\n", o);

    fputs("<div class=\"warn\" id=\"warns\"></div>\n", o);
    fputs("<div class=\"pick\">Showing <b id=\"flt\">all strategies</b>", o);
    fputs("<button id=\"clr\">Show all</button>", o);
    fputs("<input id=\"cmd\" readonly placeholder=\"click a scatter point to get its "
          "reproduce command\">", o);
    fputs("</div>\n", o);

    fputs("<div class=\"grid\">\n", o);
    fputs("<div class=\"card wide\"><h2>Overview</h2><table id=\"tbl\"></table></div>\n", o);
    fputs("<div class=\"card\"><h2>Final money distribution</h2><div id=\"box\"></div></div>\n", o);
    fputs("<div class=\"card\"><h2>Bankruptcy rate</h2><div id=\"bank\"></div></div>\n", o);
    fputs("<div class=\"card\"><h2>Average profit per day</h2><div id=\"ppd\"></div></div>\n", o);
    fputs("<div class=\"card\"><h2>Crop mix</h2><div id=\"mix\"></div>"
          "<div class=\"lg\" id=\"mixlg\"></div></div>\n", o);
    fputs("<div class=\"card wide\"><h2>Watering rate vs crop loss rate "
          "<span style=\"font-weight:400;color:var(--muted)\">&mdash; one point per run; "
          "click one to reproduce it</span></h2><div class=\"sm\" id=\"sc\"></div></div>\n", o);
    fputs("</div>\n<div id=\"tip\"></div>\n<script>\n", o);

    /* META first: the renderer reads crop labels out of it. */
    fputs("var META={\"days\":", o);
    fprintf(o, "%d,\"startMoney\":", settings->days);
    write_double(o, settings->start_money);
    fputs(",\"crops\":[", o);
    for (size_t c = 0; c < config->crop_count; c++) {
        if (c > 0) fputc(',', o);
        write_json_string(o, crop_label(config, c));
    }
    fputs("]};\n", o);

    /* Column order is contractual: src/dashboard_js.h indexes RUNS rows
     * positionally (wrate reads [15]/[24], lrate reads [22]/[23], the
     * scatter reads [1] for the seed). Adding a column is fine; reordering
     * one silently mislabels every chart. */
    fputs("var COLS=['strategy','seed','days','money','revenue','expenses','profit','planted',"
          "'harvested','sold','idle','bankrupt','bankruptcyDay','low','high','waterings',"
          "'fertilizer','processed','contractsDone','contractsFailed','penalties','reputation',"
          "'cropsLost','harvestEvents','slotDays','firstUpgradeDay'];\n", o);
    fputs("var RUNS=[", o);
    return w;
}

void dashboard_add_run(DashboardWriter *w, const BatchRunResult *r) {
    if (w == NULL) return;
    FILE *o = w->out;
    if (w->rows++ > 0) fputs(",\n", o);

    fprintf(o, "[%zu,%" PRIu64 ",%d,", strategy_index(w, r->strategy), r->seed,
            r->days_simulated);
    write_double(o, r->final_money);
    fputc(',', o);
    write_double(o, r->total_revenue);
    fputc(',', o);
    write_double(o, r->total_expenses);
    fputc(',', o);
    write_double(o, r->net_profit);
    fprintf(o, ",%d,%d,%d,%d,%d,", r->total_planted, r->total_harvested, r->total_sold,
            r->idle_days, r->bankrupt ? 1 : 0);
    if (r->bankruptcy_day == INVALID_DAY) {
        fputs("null,", o);
    } else {
        fprintf(o, "%d,", r->bankruptcy_day);
    }
    write_double(o, r->lowest_money);
    fputc(',', o);
    write_double(o, r->highest_money);
    fprintf(o, ",%d,%d,%d,%d,%d,", r->total_waterings, r->total_fertilizer_applied,
            r->total_processed, r->contracts_completed, r->contracts_failed);
    write_double(o, r->contract_penalties);
    fputc(',', o);
    write_double(o, r->reputation);
    fprintf(o, ",%d,%d,%d,", r->total_crops_lost, r->total_harvest_events, r->slot_days);
    if (r->first_upgrade_day == INVALID_DAY) {
        fputs("null]", o);
    } else {
        fprintf(o, "%d]", r->first_upgrade_day);
    }
}

/* warnings_evaluate_strategy hands each line to a callback; write them
 * straight into the JS array literal rather than buffering the set. */
static void emit_warning(const char *line, void *context) {
    DashboardWriter *w = context;
    if (w->warnings_written++ > 0) fputc(',', w->out);
    write_json_string(w->out, line);
}

bool dashboard_close(DashboardWriter *w, const StrategyAgg *agg, size_t strategy_count,
                     int total_days, double start_money) {
    if (w == NULL) return true;
    FILE *o = w->out;
    fputs("];\n", o);

    double *crop_pct = w->crop_count > 0 ? calloc(w->crop_count, sizeof(double)) : NULL;
    const char **crop_ids = w->crop_count > 0 ? calloc(w->crop_count, sizeof(char *)) : NULL;
    if (w->crop_count > 0 && (crop_pct == NULL || crop_ids == NULL)) {
        free(crop_pct);
        free(crop_ids);
        fclose(o);
        free(w);
        return false;
    }
    for (size_t c = 0; c < w->crop_count; c++) crop_ids[c] = crop_label(w->config, c);

    fputs("var SUMMARY=[", o);
    for (size_t i = 0; i < strategy_count; i++) {
        StrategySummary s;
        aggregate_finalize(&agg[i], w->crop_count, crop_pct, &s);
        if (i > 0) fputs(",\n", o);
        fprintf(o, "{\"index\":%zu,\"name\":", i);
        write_json_string(o, w->strategy_names[i]);
        fprintf(o, ",\"runs\":%ld,\"bankruptRate\":", s.runs);
        write_double(o, s.bankruptcy_rate);
        fputs(",\"avgFinalMoney\":", o);
        write_double(o, s.avg_final_money);
        fputs(",\"avgNetProfit\":", o);
        write_double(o, s.avg_net_profit);
        fputs(",\"avgProfitPerDay\":", o);
        write_double(o, s.avg_profit_per_day);
        fputs(",\"avgDays\":", o);
        write_double(o, s.avg_days_simulated);
        fputs(",\"avgCropLossRate\":", o);
        if (s.has_crop_loss_rate) {
            write_double(o, s.avg_crop_loss_rate);
        } else {
            fputs("null", o); /* undefined, not 0% -- see include/aggregate.h */
        }
        fputs(",\"firstUpgradeRate\":", o);
        write_double(o, s.first_upgrade_rate);
        fprintf(o, ",\"cropUsageObserved\":%s,\"cropPct\":[",
                s.crop_usage_observed ? "true" : "false");
        for (size_t c = 0; c < w->crop_count; c++) {
            if (c > 0) fputc(',', o);
            write_double(o, crop_pct[c]);
        }
        fputs("]}", o);
    }
    fputs("];\n", o);

    /* Same rules, same inputs, same order as the terminal summary, so the
     * page and stdout can never disagree about what is wrong. */
    fputs("var WARNINGS=[", o);
    for (size_t i = 0; i < strategy_count; i++) {
        StrategySummary s;
        aggregate_finalize(&agg[i], w->crop_count, crop_pct, &s);
        StrategyWarningStats stats;
        /* The cast is C's const-qualifier wart: const char ** does not
         * implicitly convert to const char *const *, though it is safe. */
        aggregate_to_warning_stats(&s, (const char *const *)crop_ids, crop_pct, w->crop_count,
                                   &stats);
        warnings_evaluate_strategy(w->strategy_names[i], &stats, total_days, start_money,
                                   &WARNING_DEFAULT_THRESHOLDS, emit_warning, w);
    }
    fputs("];\n", o);

    fputs(DASHBOARD_JS, o);
    fputs("</script>\n</body>\n</html>\n", o);

    free(crop_pct);
    free(crop_ids);

    /* One error check for the whole page rather than one per row: a write
     * failure sticks in the stream's error flag, so this catches a full
     * disk without branching on every fprintf. */
    bool ok = ferror(o) == 0;
    if (fclose(o) != 0) ok = false;
    free(w);
    return ok;
}
