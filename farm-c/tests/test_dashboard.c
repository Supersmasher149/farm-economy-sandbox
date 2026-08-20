/* Covers src/dashboard.c -- the --html report writer.
 *
 * The page's charts are drawn by JS in a browser, which C cannot exercise,
 * so what is testable here is the contract between the two: that the
 * emitted payload is well-formed and complete, that the numbers in it agree
 * with the aggregator, and that nothing in it can break out of the script
 * block. Those are also the failure modes that would silently produce a
 * blank page rather than a compile error.
 *
 * The payload is deliberately valid JSON (quoted object keys, no trailing
 * commas), which lets these tests parse it back with the cJSON the config
 * loader already vendors instead of pattern-matching the output.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "aggregate.h"
#include "batch.h"
#include "cJSON.h"
#include "dashboard.h"

#define TMP_HTML "tests/.test_dashboard.html"

static void load(ResolvedConfig *config, SimulationSettings *settings) {
    ConfigError error;
    assert(config_load_directory("../config", config, &error));
    assert(config_load_simulation_settings("../config", settings, &error));
}

static char *read_file(const char *path, size_t *out_len) {
    FILE *f = fopen(path, "rb");
    assert(f != NULL);
    assert(fseek(f, 0, SEEK_END) == 0);
    long size = ftell(f);
    assert(size > 0);
    rewind(f);
    char *buf = malloc((size_t)size + 1);
    assert(buf != NULL);
    assert(fread(buf, 1, (size_t)size, f) == (size_t)size);
    buf[size] = '\0';
    fclose(f);
    if (out_len != NULL) *out_len = (size_t)size;
    return buf;
}

/* Returns the JSON text assigned to `var <name>=`, up to the terminating
 * ";\n". Caller frees. */
static char *extract_payload(const char *html, const char *name) {
    char needle[64];
    snprintf(needle, sizeof(needle), "var %s=", name);
    const char *start = strstr(html, needle);
    assert(start != NULL);
    start += strlen(needle);
    const char *end = strstr(start, ";\n");
    assert(end != NULL);
    size_t len = (size_t)(end - start);
    char *out = malloc(len + 1);
    assert(out != NULL);
    memcpy(out, start, len);
    out[len] = '\0';
    return out;
}

static BatchRunResult make_run(const char *strategy, uint64_t seed, const int *crop_counts) {
    BatchRunResult r = {0};
    r.strategy = strategy;
    r.seed = seed;
    r.days_simulated = 100;
    r.final_money = 123.45;
    r.total_revenue = 1000.0;
    r.total_expenses = 876.55;
    r.net_profit = 123.45;
    r.total_planted = 10;
    r.total_harvested = 8;
    r.total_sold = 8;
    r.idle_days = 3;
    r.bankrupt = false;
    r.bankruptcy_day = INVALID_DAY;
    r.lowest_money = 5.0;
    r.highest_money = 200.0;
    r.total_waterings = 40;
    r.total_fertilizer_applied = 2;
    r.total_processed = 1;
    r.contracts_completed = 1;
    r.contracts_failed = 0;
    r.contract_penalties = 0.0;
    r.reputation = 1.5;
    r.crop_plant_counts = crop_counts;
    r.total_crops_lost = 2;
    r.total_harvest_events = 8;
    r.slot_days = 200;
    r.first_upgrade_day = 12;
    return r;
}

/* The RUNS array must be parseable JSON with one fixed-width row per run --
 * a row that drifts out of sync with the COLS header silently mislabels
 * every chart, because src/dashboard_js.h indexes it positionally. */
static void test_runs_payload_is_well_formed(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);

    const char *names[] = {"alpha", "beta"};
    int *counts = calloc(config.crop_count, sizeof(int));
    assert(counts != NULL);
    for (size_t c = 0; c < config.crop_count; c++) counts[c] = (int)c + 1;

    long *crop_totals = calloc(2 * config.crop_count, sizeof(long));
    assert(crop_totals != NULL);
    StrategyAgg agg[2] = {0};
    for (size_t i = 0; i < 2; i++) agg[i].crop_totals = crop_totals + i * config.crop_count;

    DashboardWriter *w = dashboard_open(TMP_HTML, &config, names, 2, &settings);
    assert(w != NULL);
    for (int i = 0; i < 5; i++) {
        BatchRunResult r = make_run(names[i % 2], 1000u + (unsigned)i, counts);
        dashboard_add_run(w, &r);
        aggregate_add_run(&agg[i % 2], &r, config.crop_count);
    }
    assert(dashboard_close(w, agg, 2, settings.days, settings.start_money));

    char *html = read_file(TMP_HTML, NULL);
    char *runs_text = extract_payload(html, "RUNS");
    cJSON *runs = cJSON_Parse(runs_text);
    assert(runs != NULL);
    assert(cJSON_IsArray(runs));
    assert(cJSON_GetArraySize(runs) == 5);

    /* Every row is the same width as the COLS header declares. */
    char *cols_text = extract_payload(html, "COLS");
    size_t declared = 1;
    for (const char *p = cols_text; *p != '\0'; p++)
        if (*p == ',') declared++;
    cJSON *row = NULL;
    cJSON_ArrayForEach(row, runs) {
        assert(cJSON_IsArray(row));
        assert((size_t)cJSON_GetArraySize(row) == declared);
    }

    /* Strategy index resolves by name, and the seed round-trips exactly --
     * the page offers it as a reproduce command, so an inexact seed would
     * be worse than none. */
    cJSON *first = cJSON_GetArrayItem(runs, 0);
    assert(cJSON_GetArrayItem(first, 0)->valuedouble == 0.0);
    assert(cJSON_GetArrayItem(first, 1)->valuedouble == 1000.0);
    cJSON *second = cJSON_GetArrayItem(runs, 1);
    assert(cJSON_GetArrayItem(second, 0)->valuedouble == 1.0);
    assert(cJSON_GetArrayItem(second, 1)->valuedouble == 1001.0);

    cJSON_Delete(runs);
    free(cols_text);
    free(runs_text);
    free(html);
    free(crop_totals);
    free(counts);
    config_destroy(&config);
    remove(TMP_HTML);
}

/* SUMMARY must agree with aggregate_finalize -- the whole reason
 * include/aggregate.h exists is that the page and stdout cannot disagree. */
static void test_summary_matches_the_aggregator(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);

    const char *names[] = {"solo"};
    int *counts = calloc(config.crop_count, sizeof(int));
    assert(counts != NULL);
    counts[0] = 4; /* everything planted was crop 0 -> 100% of the mix */

    long *crop_totals = calloc(config.crop_count, sizeof(long));
    assert(crop_totals != NULL);
    StrategyAgg agg = {0};
    agg.crop_totals = crop_totals;

    DashboardWriter *w = dashboard_open(TMP_HTML, &config, names, 1, &settings);
    assert(w != NULL);
    for (int i = 0; i < 4; i++) {
        BatchRunResult r = make_run("solo", 7u, counts);
        r.total_planted = 4;
        dashboard_add_run(w, &r);
        aggregate_add_run(&agg, &r, config.crop_count);
    }
    assert(dashboard_close(w, &agg, 1, settings.days, settings.start_money));

    StrategySummary expected;
    double *pct = calloc(config.crop_count, sizeof(double));
    assert(pct != NULL);
    aggregate_finalize(&agg, config.crop_count, pct, &expected);

    char *html = read_file(TMP_HTML, NULL);
    char *text = extract_payload(html, "SUMMARY");
    cJSON *summary = cJSON_Parse(text);
    assert(summary != NULL);
    assert(cJSON_GetArraySize(summary) == 1);
    cJSON *s = cJSON_GetArrayItem(summary, 0);

    assert(strcmp(cJSON_GetObjectItem(s, "name")->valuestring, "solo") == 0);
    assert(cJSON_GetObjectItem(s, "runs")->valuedouble == 4.0);
    assert(cJSON_GetObjectItem(s, "bankruptRate")->valuedouble == expected.bankruptcy_rate);
    assert(cJSON_IsTrue(cJSON_GetObjectItem(s, "cropUsageObserved")));
    /* 10 significant digits is display precision, not the CSV's %.17g --
     * compare with a tolerance rather than for equality. */
    double got = cJSON_GetObjectItem(s, "avgFinalMoney")->valuedouble;
    double diff = got - expected.avg_final_money;
    assert(diff < 1e-6 && diff > -1e-6);

    cJSON *mix = cJSON_GetObjectItem(s, "cropPct");
    assert((size_t)cJSON_GetArraySize(mix) == config.crop_count);
    assert(cJSON_GetArrayItem(mix, 0)->valuedouble == 100.0);

    cJSON_Delete(summary);
    free(text);
    free(html);
    free(pct);
    free(crop_totals);
    free(counts);
    config_destroy(&config);
    remove(TMP_HTML);
}

/* A crop-loss rate no run observed is undefined, not 0% -- the distinction
 * metrics/run_results.py draws and that a naive mean would erase. It has to
 * survive all the way into the payload as null. */
static void test_unobserved_rate_is_null_not_zero(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);

    const char *names[] = {"barren"};
    int *counts = calloc(config.crop_count, sizeof(int));
    long *crop_totals = calloc(config.crop_count, sizeof(long));
    assert(counts != NULL && crop_totals != NULL);
    StrategyAgg agg = {0};
    agg.crop_totals = crop_totals;

    DashboardWriter *w = dashboard_open(TMP_HTML, &config, names, 1, &settings);
    assert(w != NULL);
    BatchRunResult r = make_run("barren", 1u, counts);
    r.total_harvest_events = 0; /* nothing ever matured */
    r.total_crops_lost = 0;
    r.total_planted = 0;
    r.first_upgrade_day = INVALID_DAY;
    dashboard_add_run(w, &r);
    aggregate_add_run(&agg, &r, config.crop_count);
    assert(dashboard_close(w, &agg, 1, settings.days, settings.start_money));

    char *html = read_file(TMP_HTML, NULL);
    char *text = extract_payload(html, "SUMMARY");
    cJSON *summary = cJSON_Parse(text);
    assert(summary != NULL);
    cJSON *s = cJSON_GetArrayItem(summary, 0);
    assert(cJSON_IsNull(cJSON_GetObjectItem(s, "avgCropLossRate")));
    assert(cJSON_IsFalse(cJSON_GetObjectItem(s, "cropUsageObserved")));

    cJSON_Delete(summary);
    free(text);
    free(html);
    free(crop_totals);
    free(counts);
    config_destroy(&config);
    remove(TMP_HTML);
}

/* The payload sits inside a <script> block, so a strategy name is untrusted
 * text in an executable context. Nothing it contains may reach the output
 * raw, and in particular a closing script tag must be unrepresentable. */
static void test_hostile_strategy_name_is_escaped(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);

    static const char nasty[] = "</script><img src=x onerror=alert(1)>\"&\\";
    const char *names[] = {nasty};
    int *counts = calloc(config.crop_count, sizeof(int));
    long *crop_totals = calloc(config.crop_count, sizeof(long));
    assert(counts != NULL && crop_totals != NULL);
    StrategyAgg agg = {0};
    agg.crop_totals = crop_totals;

    DashboardWriter *w = dashboard_open(TMP_HTML, &config, names, 1, &settings);
    assert(w != NULL);
    BatchRunResult r = make_run(nasty, 1u, counts);
    dashboard_add_run(w, &r);
    aggregate_add_run(&agg, &r, config.crop_count);
    assert(dashboard_close(w, &agg, 1, settings.days, settings.start_money));

    char *html = read_file(TMP_HTML, NULL);

    /* Exactly one opening and one closing script tag: the writer's own. The
     * hostile name's payload text (onerror=..., src=x) may well appear, but
     * only inside a JSON string with its angle brackets escaped, where it
     * is inert -- what must not exist is a second tag that could make it
     * markup again. */
    int openers = 0, closers = 0;
    for (const char *p = html; (p = strstr(p, "<script")) != NULL; p++) openers++;
    for (const char *p = html; (p = strstr(p, "</script")) != NULL; p++) closers++;
    assert(openers == 1);
    assert(closers == 1);
    assert(strstr(html, "<img") == NULL);

    /* And it still round-trips through JSON intact. */
    char *text = extract_payload(html, "SUMMARY");
    cJSON *summary = cJSON_Parse(text);
    assert(summary != NULL);
    cJSON *s = cJSON_GetArrayItem(summary, 0);
    assert(strcmp(cJSON_GetObjectItem(s, "name")->valuestring, nasty) == 0);

    cJSON_Delete(summary);
    free(text);
    free(html);
    free(crop_totals);
    free(counts);
    config_destroy(&config);
    remove(TMP_HTML);
}

/* The page must open offline: no CDN, no external stylesheet, no remote
 * anything -- the same property tests/test_dashboard.py asserts for the
 * Python dashboard, which base64-inlines its PNGs to get it. */
static void test_page_is_self_contained(void) {
    ResolvedConfig config;
    SimulationSettings settings;
    load(&config, &settings);

    const char *names[] = {"solo"};
    int *counts = calloc(config.crop_count, sizeof(int));
    long *crop_totals = calloc(config.crop_count, sizeof(long));
    assert(counts != NULL && crop_totals != NULL);
    StrategyAgg agg = {0};
    agg.crop_totals = crop_totals;

    DashboardWriter *w = dashboard_open(TMP_HTML, &config, names, 1, &settings);
    assert(w != NULL);
    BatchRunResult r = make_run("solo", 1u, counts);
    dashboard_add_run(w, &r);
    aggregate_add_run(&agg, &r, config.crop_count);
    assert(dashboard_close(w, &agg, 1, settings.days, settings.start_money));

    char *html = read_file(TMP_HTML, NULL);
    assert(strstr(html, "http://") == NULL);
    assert(strstr(html, "https://") == NULL);
    assert(strstr(html, "<!doctype html>") == html);
    assert(strstr(html, "</html>") != NULL);

    free(html);
    free(crop_totals);
    free(counts);
    config_destroy(&config);
    remove(TMP_HTML);
}

/* A writer that was never opened is a no-op everywhere, so cmd_batch's
 * callback can stay a single unconditional line. */
static void test_null_writer_is_inert(void) {
    BatchRunResult r = {0};
    dashboard_add_run(NULL, &r);
    assert(dashboard_close(NULL, NULL, 0, 365, 33.0));
}

int main(void) {
    test_runs_payload_is_well_formed();
    test_summary_matches_the_aggregator();
    test_unobserved_rate_is_null_not_zero();
    test_hostile_strategy_name_is_escaped();
    test_page_is_self_contained();
    test_null_writer_is_inert();
    printf("dashboard tests passed\n");
    return 0;
}
