/* strdup is not ISO C, so glibc's <string.h> hides its declaration under
 * -std=c11 unless a feature-test macro asks for it. _DARWIN_C_SOURCE rides
 * alongside for the same reason tests/test_config_invalid.c defines it next
 * to _POSIX_C_SOURCE (see the comment there): on Darwin, _POSIX_C_SOURCE
 * alone can narrow visibility instead of widening it. Both must come before
 * any header is included, including golden.h's own. */
#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE

#include "golden.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "agent.h"
#include "cJSON.h"
#include "config.h"
#include "pyfloat.h"
#include "runner.h"
#include "trajectory.h"

/* The same seeds ../.claude/skills/replay-guard/scripts/golden_replay.py
 * uses, so a C failure and a Python failure name the same combo and can be
 * read side by side. */
static const uint64_t GOLDEN_SEEDS[] = {1, 42, 777, 123456789};
#define GOLDEN_SEED_COUNT (sizeof(GOLDEN_SEEDS) / sizeof(GOLDEN_SEEDS[0]))

#define DEFAULT_BASELINE "tests/golden_baseline.json"
#define DEFAULT_CONFIG_DIR "../config"

/* --- one run's recorded fields ------------------------------------------
 *
 * Everything is stringified, floats via py_float_hex. A uniform string map
 * is what makes the diff below generic (add a field in one place, not
 * three) and keeps the committed JSON unambiguous: an exact float has no
 * faithful JSON-number spelling, and a baseline that stored some fields as
 * numbers would invite exactly the "round to 6dp" tolerance replay-guard's
 * header warns about.
 */

#define MAX_FIELD_TEXT 40

typedef struct {
    const char *name;
    char value[MAX_FIELD_TEXT];
} GoldenField;

#define MAX_FIELDS 32

typedef struct {
    GoldenField fields[MAX_FIELDS];
    size_t count;
} GoldenRecord;

static void record_put(GoldenRecord *record, const char *name, const char *value) {
    if (record->count >= MAX_FIELDS) return;
    record->fields[record->count].name = name;
    snprintf(record->fields[record->count].value, MAX_FIELD_TEXT, "%s", value);
    record->count++;
}

static void record_put_int(GoldenRecord *record, const char *name, long value) {
    char buf[MAX_FIELD_TEXT];
    snprintf(buf, sizeof(buf), "%ld", value);
    record_put(record, name, buf);
}

static void record_put_double(GoldenRecord *record, const char *name, double value) {
    char buf[PY_FLOAT_HEX_BUFSIZE];
    record_put(record, name, py_float_hex(value, buf));
}

static void build_record(GoldenRecord *record, const RunResult *result,
                         const Trajectory *trajectory) {
    const FarmState *s = &result->state;
    record->count = 0;

    char digest[TRAJECTORY_DIGEST_HEX_SIZE];
    record_put(record, "trajectory", trajectory_digest(trajectory, digest));
    record_put_int(record, "trajectory_days", trajectory->day_count);
    record_put_int(record, "days_simulated", result->days_simulated);

    record_put_double(record, "final_money", s->money);
    record_put_double(record, "total_revenue", s->total_revenue);
    record_put_double(record, "total_expenses", s->total_expenses);
    /* Literally batch.c:snapshot_result's subtraction, not an equivalent --
     * the point is to record the same arithmetic the CSV reports. */
    record_put_double(record, "net_profit", s->total_revenue - s->total_expenses);
    record_put_double(record, "lowest_money", s->lowest_money);
    if (s->has_highest_money) {
        record_put_double(record, "highest_money", s->highest_money);
    } else {
        record_put(record, "highest_money", "-");
    }
    record_put_double(record, "reputation", s->reputation);
    record_put_double(record, "contract_penalties", s->contract_penalties);
    record_put_double(record, "processing_revenue", s->processing_revenue);

    record_put_int(record, "total_planted", s->total_planted);
    record_put_int(record, "total_harvested", s->total_harvested);
    record_put_int(record, "total_sold", s->total_sold);
    record_put_int(record, "total_spoiled", s->total_spoiled);
    record_put_int(record, "total_processed", s->total_processed);
    record_put_int(record, "total_waterings", s->total_waterings);
    record_put_int(record, "total_fertilizer_applied", s->total_fertilizer_applied);
    record_put_int(record, "total_crops_lost", s->total_crops_lost);
    record_put_int(record, "idle_days", s->idle_days);
    record_put_int(record, "contracts_completed", s->contracts_completed);
    record_put_int(record, "contracts_failed", s->contracts_failed);
    record_put(record, "bankrupt", s->bankrupt ? "true" : "false");
    if (s->bankruptcy_day == INVALID_DAY) {
        record_put(record, "bankruptcy_day", "-");
    } else {
        record_put_int(record, "bankruptcy_day", s->bankruptcy_day);
    }
}

/* --- running one combo --------------------------------------------------- */

static bool run_combo(const ResolvedConfig *config, const SimulationSettings *settings,
                      const Agent *agent, uint64_t seed, bool keep_per_day, GoldenRecord *record,
                      Trajectory *trajectory) {
    trajectory_init(trajectory, keep_per_day);
    RunResult result = {0};
    RunnerError error;
    RunSeed run_seed = {true, seed};
    bool ok = runner_run_single(config, settings, agent, run_seed, trajectory_observe_day,
                                trajectory, &result, &error);
    if (!ok) {
        fprintf(stderr, "run error (seed %" PRIu64 "): %s\n", seed, error.message);
        runner_run_result_destroy(&result);
        return false;
    }
    if (trajectory->failed) {
        fprintf(stderr, "trajectory digest unavailable (allocation failure), seed %" PRIu64 "\n",
                seed);
        runner_run_result_destroy(&result);
        return false;
    }
    if (record != NULL) build_record(record, &result, trajectory);
    runner_run_result_destroy(&result);
    return true;
}

static bool load_config_or_report(const char *directory, ResolvedConfig *config,
                                  SimulationSettings *settings) {
    ConfigError error;
    if (!config_load_directory(directory, config, &error)) {
        fprintf(stderr, "config error: %s\n", error.message);
        return false;
    }
    if (!config_load_simulation_settings(directory, settings, &error)) {
        fprintf(stderr, "config error: %s\n", error.message);
        config_destroy(config);
        return false;
    }
    return true;
}

/* --- baseline file I/O ---------------------------------------------------- */

static char *read_file(const char *path) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) return NULL;
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    long size = ftell(file);
    if (size < 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    char *text = malloc((size_t)size + 1);
    if (text == NULL) {
        fclose(file);
        return NULL;
    }
    size_t got = fread(text, 1, (size_t)size, file);
    fclose(file);
    text[got] = '\0';
    return text;
}

/* Recorded alongside the baseline purely so a divergence can be attributed
 * to a toolchain change rather than a code change -- never compared for
 * pass/fail, exactly as replay-guard's _environment() is not. */
static cJSON *build_meta(const SimulationSettings *settings) {
    cJSON *meta = cJSON_CreateObject();
    if (meta == NULL) return NULL;
#if defined(__clang__)
    cJSON_AddStringToObject(meta, "compiler", "clang " __clang_version__);
#elif defined(__GNUC__)
    cJSON_AddStringToObject(meta, "compiler", "gcc " __VERSION__);
#else
    cJSON_AddStringToObject(meta, "compiler", "unknown");
#endif
#if defined(__APPLE__)
    cJSON_AddStringToObject(meta, "platform", "darwin");
#elif defined(__linux__)
    cJSON_AddStringToObject(meta, "platform", "linux");
#else
    cJSON_AddStringToObject(meta, "platform", "unknown");
#endif
    cJSON_AddNumberToObject(meta, "days", settings->days);
    cJSON_AddNumberToObject(meta, "seed_count", (double)(size_t)GOLDEN_SEED_COUNT);
    return meta;
}

static int cmd_capture(const char *config_dir, const char *baseline_path) {
    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(config_dir, &config, &settings)) return 2;

    cJSON *document = cJSON_CreateObject();
    cJSON *runs = cJSON_CreateObject();
    cJSON *meta = build_meta(&settings);
    if (document == NULL || runs == NULL || meta == NULL) {
        cJSON_Delete(document);
        cJSON_Delete(runs);
        cJSON_Delete(meta);
        config_destroy(&config);
        return 2;
    }
    cJSON_AddItemToObject(document, "_meta", meta);
    cJSON_AddItemToObject(document, "runs", runs);

    long combos = 0;
    for (const AgentRegistryEntry *entry = AGENT_REGISTRY; entry->strategy_name != NULL; entry++) {
        for (size_t i = 0; i < GOLDEN_SEED_COUNT; i++) {
            GoldenRecord record;
            Trajectory trajectory;
            if (!run_combo(&config, &settings, entry->agent, GOLDEN_SEEDS[i], false, &record,
                           &trajectory)) {
                trajectory_destroy(&trajectory);
                cJSON_Delete(document);
                config_destroy(&config);
                return 2;
            }
            trajectory_destroy(&trajectory);

            char key[128];
            snprintf(key, sizeof(key), "%s:%" PRIu64, entry->strategy_name, GOLDEN_SEEDS[i]);
            cJSON *object = cJSON_CreateObject();
            for (size_t f = 0; f < record.count; f++) {
                cJSON_AddStringToObject(object, record.fields[f].name, record.fields[f].value);
            }
            cJSON_AddItemToObject(runs, key, object);
            combos++;
        }
    }

    char *text = cJSON_Print(document);
    cJSON_Delete(document);
    config_destroy(&config);
    if (text == NULL) return 2;

    FILE *out = fopen(baseline_path, "wb");
    if (out == NULL) {
        fprintf(stderr, "cannot write %s: %s\n", baseline_path, strerror(errno));
        free(text);
        return 2;
    }
    fprintf(out, "%s\n", text);
    fclose(out);
    free(text);
    printf("Captured baseline for %ld (strategy, seed) combos -> %s\n", combos, baseline_path);
    return 0;
}

static int cmd_check(const char *config_dir, const char *baseline_path, int max_report) {
    char *text = read_file(baseline_path);
    if (text == NULL) {
        fprintf(stderr, "no baseline at %s -- run `farm-c golden capture` first\n", baseline_path);
        return 2;
    }
    cJSON *document = cJSON_Parse(text);
    free(text);
    if (document == NULL) {
        fprintf(stderr, "cannot parse %s\n", baseline_path);
        return 2;
    }
    cJSON *runs = cJSON_GetObjectItemCaseSensitive(document, "runs");
    if (!cJSON_IsObject(runs)) {
        fprintf(stderr, "%s has no \"runs\" object\n", baseline_path);
        cJSON_Delete(document);
        return 2;
    }

    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(config_dir, &config, &settings)) {
        cJSON_Delete(document);
        return 2;
    }

    long total = 0;
    long failed = 0;
    long reported = 0;
    char first_failure[128] = {0};

    for (const AgentRegistryEntry *entry = AGENT_REGISTRY; entry->strategy_name != NULL; entry++) {
        for (size_t i = 0; i < GOLDEN_SEED_COUNT; i++) {
            char key[128];
            snprintf(key, sizeof(key), "%s:%" PRIu64, entry->strategy_name, GOLDEN_SEEDS[i]);
            total++;

            GoldenRecord record;
            Trajectory trajectory;
            if (!run_combo(&config, &settings, entry->agent, GOLDEN_SEEDS[i], false, &record,
                           &trajectory)) {
                trajectory_destroy(&trajectory);
                cJSON_Delete(document);
                config_destroy(&config);
                return 2;
            }
            trajectory_destroy(&trajectory);

            cJSON *expected = cJSON_GetObjectItemCaseSensitive(runs, key);
            if (!cJSON_IsObject(expected)) {
                failed++;
                if (reported++ < max_report) {
                    printf("  %s: missing from baseline (new strategy/seed since capture)\n", key);
                }
                if (first_failure[0] == '\0') snprintf(first_failure, sizeof(first_failure), "%s", key);
                continue;
            }

            bool combo_failed = false;
            for (size_t f = 0; f < record.count; f++) {
                cJSON *field = cJSON_GetObjectItemCaseSensitive(expected, record.fields[f].name);
                const char *was = cJSON_IsString(field) ? field->valuestring : NULL;
                if (was != NULL && strcmp(was, record.fields[f].value) == 0) continue;
                if (!combo_failed) {
                    combo_failed = true;
                    failed++;
                    if (first_failure[0] == '\0') {
                        snprintf(first_failure, sizeof(first_failure), "%s", key);
                    }
                    if (reported < max_report) printf("  %s:\n", key);
                }
                if (reported < max_report) {
                    printf("      %-24s baseline=%s  now=%s\n", record.fields[f].name,
                           was != NULL ? was : "(absent)", record.fields[f].value);
                }
            }
            if (combo_failed) reported++;
        }
    }

    config_destroy(&config);

    if (failed == 0) {
        printf("PASS: all %ld (strategy, seed) combos match %s.\n", total, baseline_path);
        cJSON_Delete(document);
        return 0;
    }

    printf("\nFAIL: %ld/%ld combos diverged from the committed baseline.\n", failed, total);
    if (reported > max_report) printf("  ... and %ld more\n", reported - max_report);

    cJSON *meta = cJSON_GetObjectItemCaseSensitive(document, "_meta");
    if (cJSON_IsObject(meta)) {
        cJSON *compiler = cJSON_GetObjectItemCaseSensitive(meta, "compiler");
        cJSON *platform = cJSON_GetObjectItemCaseSensitive(meta, "platform");
        printf("\nBaseline was captured with: %s on %s\n",
               cJSON_IsString(compiler) ? compiler->valuestring : "?",
               cJSON_IsString(platform) ? platform->valuestring : "?");
    }
    /* Only the trajectory field can localize to a day, so point at the tool
     * that does rather than making the reader guess. */
    char strategy[64];
    unsigned long long seed = 0;
    if (sscanf(first_failure, "%63[^:]:%llu", strategy, &seed) == 2) {
        printf("\nBisect the first failure to a day:\n"
               "  ./farm-c golden trace %s %llu\n",
               strategy, seed);
    }
    printf("\nIf this is an intended behavior change, re-run `make golden-capture`, verify\n"
           "against Python with `python3 ../.claude/skills/c-parity/scripts/c_parity.py check`,\n"
           "and commit the updated baseline alongside the change.\n");
    cJSON_Delete(document);
    return 1;
}

static int cmd_trace(const char *config_dir, const char *baseline_path, const char *strategy_name,
                     uint64_t seed) {
    const Agent *agent = agent_registry_find(strategy_name);
    if (agent == NULL) {
        fprintf(stderr, "unknown strategy: %s\n", strategy_name);
        return 2;
    }
    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(config_dir, &config, &settings)) return 2;

    GoldenRecord record;
    Trajectory trajectory;
    if (!run_combo(&config, &settings, agent, seed, true, &record, &trajectory)) {
        trajectory_destroy(&trajectory);
        config_destroy(&config);
        return 2;
    }
    config_destroy(&config);

    char digest[TRAJECTORY_DIGEST_HEX_SIZE];
    trajectory_digest(&trajectory, digest);

    /* If the baseline already agrees there is nothing to bisect -- say so
     * rather than printing 300 lines the reader then has to compare by eye. */
    const char *recorded = NULL;
    char *text = read_file(baseline_path);
    cJSON *document = text != NULL ? cJSON_Parse(text) : NULL;
    free(text);
    if (document != NULL) {
        char key[128];
        snprintf(key, sizeof(key), "%s:%" PRIu64, strategy_name, seed);
        cJSON *runs = cJSON_GetObjectItemCaseSensitive(document, "runs");
        cJSON *combo = cJSON_IsObject(runs) ? cJSON_GetObjectItemCaseSensitive(runs, key) : NULL;
        cJSON *field =
            cJSON_IsObject(combo) ? cJSON_GetObjectItemCaseSensitive(combo, "trajectory") : NULL;
        if (cJSON_IsString(field)) recorded = field->valuestring;
    }
    if (recorded != NULL && strcmp(recorded, digest) == 0) {
        printf("%s:%" PRIu64 " matches the baseline trajectory (%ld days, %s). "
               "Nothing to bisect.\n",
               strategy_name, seed, trajectory.day_count, digest);
        cJSON_Delete(document);
        trajectory_destroy(&trajectory);
        return 0;
    }

    printf("%s:%" PRIu64 " -- %ld simulated days\n", strategy_name, seed, trajectory.day_count);
    for (size_t i = 0; i < trajectory.per_day_count; i++) {
        printf("  day %4zu  %s\n", i + 1, trajectory.per_day[i]);
    }
    if (recorded != NULL) {
        printf("\nFinal digest %s != baseline %s.\n"
               "Re-run this on a known-good build and compare the two listings; the first\n"
               "differing day is where to look, then `golden payload %s %" PRIu64 " --day N`\n"
               "prints the exact bytes that were hashed for that day.\n",
               digest, recorded, strategy_name, seed);
    }
    cJSON_Delete(document);
    trajectory_destroy(&trajectory);
    return recorded != NULL ? 1 : 0;
}

/* Prints the exact hashed bytes for one day. This is the cross-language
 * debugging tool: the Python mirror in c_parity.py can print the same thing,
 * so a digest mismatch becomes a plain text diff instead of a guess. */
typedef struct {
    Trajectory *trajectory;
    int want_day;
    int seen;
    char *captured;
} PayloadCapture;

static void capture_day(const FarmState *state, const WeatherDay *weather, void *context) {
    PayloadCapture *capture = context;
    capture->seen++;
    /* Fold into the digest as usual first, so `keep_per_day` bookkeeping and
     * the running hash stay identical to a normal run. */
    trajectory_observe_day(state, weather, capture->trajectory);
    if (capture->seen != capture->want_day || capture->captured != NULL) return;
    const char *payload = trajectory_day_payload(capture->trajectory, state, weather);
    if (payload != NULL) capture->captured = strdup(payload);
}

static int cmd_payload(const char *config_dir, const char *strategy_name, uint64_t seed,
                       int day) {
    const Agent *agent = agent_registry_find(strategy_name);
    if (agent == NULL) {
        fprintf(stderr, "unknown strategy: %s\n", strategy_name);
        return 2;
    }
    ResolvedConfig config = {0};
    SimulationSettings settings = {0};
    if (!load_config_or_report(config_dir, &config, &settings)) return 2;

    Trajectory trajectory;
    trajectory_init(&trajectory, false);
    PayloadCapture capture = {&trajectory, day, 0, NULL};
    RunResult result = {0};
    RunnerError error;
    RunSeed run_seed = {true, seed};
    bool ok = runner_run_single(&config, &settings, agent, run_seed, capture_day, &capture,
                                &result, &error);
    runner_run_result_destroy(&result);
    config_destroy(&config);
    if (!ok) {
        fprintf(stderr, "run error: %s\n", error.message);
        free(capture.captured);
        trajectory_destroy(&trajectory);
        return 2;
    }
    if (capture.captured == NULL) {
        fprintf(stderr, "day %d out of range (run simulated %d days)\n", day, capture.seen);
        trajectory_destroy(&trajectory);
        return 2;
    }
    fputs(capture.captured, stdout);
    free(capture.captured);
    trajectory_destroy(&trajectory);
    return 0;
}

/* --- dispatch ------------------------------------------------------------- */

static void golden_usage(FILE *stream) {
    fprintf(stream,
            "usage: farm-c golden capture [--config DIR] [--baseline PATH]\n"
            "       farm-c golden check   [--config DIR] [--baseline PATH] [--max-report N]\n"
            "       farm-c golden trace STRATEGY SEED [--config DIR] [--baseline PATH]\n"
            "       farm-c golden payload STRATEGY SEED --day N [--config DIR]\n");
}

static bool parse_u64(const char *text, uint64_t *out) {
    char *end = NULL;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') return false;
    *out = (uint64_t)value;
    return true;
}

int golden_main(int argc, char **argv) {
    if (argc < 3) {
        golden_usage(stderr);
        return 2;
    }
    const char *subcommand = argv[2];
    const char *config_dir = DEFAULT_CONFIG_DIR;
    const char *baseline_path = DEFAULT_BASELINE;
    int max_report = 10;
    int day = 0;

    /* Positional STRATEGY SEED for trace/payload, then flags. */
    int index = 3;
    const char *strategy_name = NULL;
    uint64_t seed = 0;
    bool wants_positional =
        strcmp(subcommand, "trace") == 0 || strcmp(subcommand, "payload") == 0;
    if (wants_positional) {
        if (argc < 5 || !parse_u64(argv[4], &seed)) {
            golden_usage(stderr);
            return 2;
        }
        strategy_name = argv[3];
        index = 5;
    }

    for (; index < argc; index++) {
        if (strcmp(argv[index], "--config") == 0 && index + 1 < argc) {
            config_dir = argv[++index];
        } else if (strcmp(argv[index], "--baseline") == 0 && index + 1 < argc) {
            baseline_path = argv[++index];
        } else if (strcmp(argv[index], "--max-report") == 0 && index + 1 < argc) {
            max_report = atoi(argv[++index]);
        } else if (strcmp(argv[index], "--day") == 0 && index + 1 < argc) {
            day = atoi(argv[++index]);
        } else {
            golden_usage(stderr);
            return 2;
        }
    }

    if (strcmp(subcommand, "capture") == 0) return cmd_capture(config_dir, baseline_path);
    if (strcmp(subcommand, "check") == 0) return cmd_check(config_dir, baseline_path, max_report);
    if (strcmp(subcommand, "trace") == 0) {
        return cmd_trace(config_dir, baseline_path, strategy_name, seed);
    }
    if (strcmp(subcommand, "payload") == 0) {
        if (day < 1) {
            golden_usage(stderr);
            return 2;
        }
        return cmd_payload(config_dir, strategy_name, seed, day);
    }
    golden_usage(stderr);
    return 2;
}
