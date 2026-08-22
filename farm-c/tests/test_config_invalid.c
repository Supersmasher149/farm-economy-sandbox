/* Invalid-configuration tests for src/config_loader.c.
 *
 * `tests/test_config_loader.c` covers the happy path plus one missing
 * directory. That leaves the entire *rejection* surface untested -- and the
 * loader is the port's only defence against a malformed `../config`, which
 * both implementations read. A field the C silently accepts (or coerces)
 * where `simulation/configuration.py:validate` rejects is a parity bug that
 * shows up as a divergent run, not as a config error.
 *
 * Method: copy the real `../config` into a temp directory, mutate exactly
 * one document through cJSON, and assert the load fails with the expected
 * error *code* and names the offending field. Mutating the parsed tree
 * rather than the file text keeps these tests robust to the config being
 * reformatted or its values retuned -- a textual find/replace would rot the
 * moment someone reflows a JSON file.
 *
 * Every case also asserts the failure is *clean*: `config_destroy` must be
 * safe on the partially-initialised object a failed load leaves behind
 * (docs/c-port-plan.md Section 9), and ASan is what makes that assertion
 * mean something.
 */
#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "cJSON.h"
#include "config.h"

static int failures;

static void report(bool ok, const char *name) {
    if (!ok) {
        failures++;
        printf("FAIL %s\n", name);
    }
}

#define SOURCE_CONFIG "../config"

static const char *const DOCUMENTS[] = {
    "crops",   "upgrades", "fertilizer", "watering_settings",  "soil",    "weather",
    "markets", "contracts", "buyers",    "processing",         "storage", "simulation_settings",
};
#define DOCUMENT_COUNT (sizeof(DOCUMENTS) / sizeof(DOCUMENTS[0]))

/* --- temp-directory scaffolding ------------------------------------------ */

static char *read_text(const char *path) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) return NULL;
    fseek(file, 0, SEEK_END);
    long length = ftell(file);
    rewind(file);
    if (length < 0) {
        fclose(file);
        return NULL;
    }
    char *text = malloc((size_t)length + 1);
    if (text == NULL) {
        fclose(file);
        return NULL;
    }
    size_t got = fread(text, 1, (size_t)length, file);
    fclose(file);
    text[got] = '\0';
    return text;
}

static bool write_text(const char *path, const char *text) {
    FILE *file = fopen(path, "wb");
    if (file == NULL) return false;
    fputs(text, file);
    fclose(file);
    return true;
}

/* Copies every config document into a fresh temp directory and returns its
 * path in `out` (caller calls remove_sandbox). */
static bool make_sandbox(char *out, size_t out_size) {
    char template[] = "/tmp/farm-c-config-XXXXXX";
    char *directory = mkdtemp(template);
    if (directory == NULL) return false;
    snprintf(out, out_size, "%s", directory);

    for (size_t i = 0; i < DOCUMENT_COUNT; i++) {
        char source[PATH_MAX];
        char destination[PATH_MAX];
        snprintf(source, sizeof(source), "%s/%s.json", SOURCE_CONFIG, DOCUMENTS[i]);
        snprintf(destination, sizeof(destination), "%s/%s.json", directory, DOCUMENTS[i]);
        char *text = read_text(source);
        if (text == NULL) return false;
        bool ok = write_text(destination, text);
        free(text);
        if (!ok) return false;
    }
    return true;
}

static void remove_sandbox(const char *directory) {
    for (size_t i = 0; i < DOCUMENT_COUNT; i++) {
        char path[PATH_MAX];
        snprintf(path, sizeof(path), "%s/%s.json", directory, DOCUMENTS[i]);
        unlink(path);
    }
    rmdir(directory);
}

/* --- the case runner ------------------------------------------------------ */

typedef void (*MutateFn)(cJSON *root);

/* Applies `mutate` to one document in a fresh sandbox, loads it, and checks
 * the load failed with `expected_code` and a message naming `needle`.
 * A NULL `mutate` means "delete the document entirely". */
static void expect_rejected(const char *document, MutateFn mutate, ConfigErrorCode expected_code,
                            const char *needle, const char *name) {
    char sandbox[PATH_MAX];
    if (!make_sandbox(sandbox, sizeof(sandbox))) {
        failures++;
        printf("FAIL %s: could not build sandbox\n", name);
        return;
    }

    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/%s.json", sandbox, document);

    if (mutate == NULL) {
        unlink(path);
    } else {
        char *text = read_text(path);
        cJSON *root = text != NULL ? cJSON_Parse(text) : NULL;
        free(text);
        if (root == NULL) {
            failures++;
            printf("FAIL %s: could not parse %s\n", name, document);
            remove_sandbox(sandbox);
            return;
        }
        mutate(root);
        char *rendered = cJSON_Print(root);
        cJSON_Delete(root);
        if (rendered == NULL || !write_text(path, rendered)) {
            failures++;
            printf("FAIL %s: could not rewrite %s\n", name, document);
            free(rendered);
            remove_sandbox(sandbox);
            return;
        }
        free(rendered);
    }

    ConfigError error;
    bool ok;
    if (strcmp(document, "simulation_settings") == 0) {
        SimulationSettings settings;
        ok = config_load_simulation_settings(sandbox, &settings, &error);
    } else {
        ResolvedConfig config;
        /* Poisoned, not zeroed: a loader that fails before its own memset
         * would otherwise be handed a conveniently clean struct here and
         * config_destroy would appear safe when it is not. */
        memset(&config, 0xA5, sizeof(config));
        ok = config_load_directory(sandbox, &config, &error);
        /* Must be safe on the partially-initialised object a failure
         * leaves, and on a successful one alike. */
        config_destroy(&config);
    }

    if (ok) {
        failures++;
        printf("FAIL %s: invalid config was accepted\n", name);
    } else {
        if (error.code != expected_code) {
            failures++;
            printf("FAIL %s: expected error code %d, got %d (%s)\n", name, (int)expected_code,
                   (int)error.code, error.message);
        }
        if (needle != NULL && strstr(error.message, needle) == NULL) {
            failures++;
            printf("FAIL %s: message %s does not mention %s\n", name, error.message, needle);
        }
        /* Every config error names the document it came from -- without it
         * a message like "must be an integer" is unactionable across 12
         * files. */
        if (strstr(error.message, document) == NULL) {
            failures++;
            printf("FAIL %s: message %s does not name the document\n", name, error.message);
        }
    }
    remove_sandbox(sandbox);
}

/* --- mutations ------------------------------------------------------------ */

/* crops.json and buyers.json are top-level ARRAYS; processing.json and
 * markets.json are objects. Mutations below follow the real shapes. */
static cJSON *first_crop(cJSON *root) { return cJSON_GetArrayItem(root, 0); }

static void mutate_drop_required_crop_field(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(first_crop(root), "growth_days");
}

static void mutate_crop_field_wrong_type(cJSON *root) {
    cJSON *crop = first_crop(root);
    cJSON_DeleteItemFromObjectCaseSensitive(crop, "growth_days");
    cJSON_AddStringToObject(crop, "growth_days", "seven");
}

static void mutate_unknown_crop_field(cJSON *root) {
    cJSON_AddNumberToObject(first_crop(root), "definitely_not_a_field", 1);
}

static void mutate_negative_growth_days(cJSON *root) {
    cJSON *crop = first_crop(root);
    cJSON_DeleteItemFromObjectCaseSensitive(crop, "growth_days");
    cJSON_AddNumberToObject(crop, "growth_days", -3);
}

static void mutate_duplicate_crop_id(cJSON *root) {
    cJSON *first = cJSON_GetArrayItem(root, 0);
    cJSON *second = cJSON_GetArrayItem(root, 1);
    cJSON *id = cJSON_GetObjectItemCaseSensitive(first, "id");
    cJSON_DeleteItemFromObjectCaseSensitive(second, "id");
    cJSON_AddStringToObject(second, "id", id->valuestring);
}

static void mutate_bad_crop_role(cJSON *root) {
    cJSON *crop = first_crop(root);
    cJSON_DeleteItemFromObjectCaseSensitive(crop, "role");
    cJSON_AddStringToObject(crop, "role", "turbo");
}

/* range2 fields must be ordered [low, high]. */
static void mutate_inverted_temperature_range(cJSON *root) {
    cJSON *crop = first_crop(root);
    cJSON_DeleteItemFromObjectCaseSensitive(crop, "temperature_range");
    cJSON *inverted = cJSON_CreateArray();
    cJSON_AddItemToArray(inverted, cJSON_CreateNumber(30));
    cJSON_AddItemToArray(inverted, cJSON_CreateNumber(8));
    cJSON_AddItemToObject(crop, "temperature_range", inverted);
}

/* A three-element "range" -- exactly two entries are required. */
static void mutate_three_element_range(cJSON *root) {
    cJSON *crop = first_crop(root);
    cJSON_DeleteItemFromObjectCaseSensitive(crop, "ph_range");
    cJSON *wrong = cJSON_CreateArray();
    for (int i = 0; i < 3; i++) cJSON_AddItemToArray(wrong, cJSON_CreateNumber(6.0 + i));
    cJSON_AddItemToObject(crop, "ph_range", wrong);
}

static void mutate_unresolvable_recipe_input(cJSON *root) {
    cJSON *recipes = cJSON_GetObjectItemCaseSensitive(root, "recipes");
    cJSON *recipe = cJSON_GetArrayItem(recipes, 0);
    cJSON_DeleteItemFromObjectCaseSensitive(recipe, "input_item_id");
    cJSON_AddStringToObject(recipe, "input_item_id", "no_such_item");
}

static void mutate_unresolvable_buyer_item(cJSON *root) {
    cJSON *buyer = cJSON_GetArrayItem(root, 0);
    cJSON_DeleteItemFromObjectCaseSensitive(buyer, "items");
    cJSON *items = cJSON_CreateArray();
    cJSON_AddItemToArray(items, cJSON_CreateString("no_such_item"));
    cJSON_AddItemToObject(buyer, "items", items);
}

static void mutate_drop_spot_channel(cJSON *root) {
    cJSON *channels = cJSON_GetObjectItemCaseSensitive(root, "channels");
    int count = cJSON_GetArraySize(channels);
    for (int i = 0; i < count; i++) {
        cJSON *channel = cJSON_GetArrayItem(channels, i);
        cJSON *id = cJSON_GetObjectItemCaseSensitive(channel, "id");
        if (cJSON_IsString(id) && strcmp(id->valuestring, "spot") == 0) {
            cJSON_DeleteItemFromObjectCaseSensitive(channel, "id");
            cJSON_AddStringToObject(channel, "id", "not_spot");
            return;
        }
    }
}

static void mutate_zero_days(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(root, "days");
    cJSON_AddNumberToObject(root, "days", 0);
}

static void mutate_zero_slots(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(root, "start_slots");
    cJSON_AddNumberToObject(root, "start_slots", 0);
}

static void mutate_negative_start_money(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(root, "start_money");
    cJSON_AddNumberToObject(root, "start_money", -1.0);
}

static void mutate_negative_seed(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(root, "seed");
    cJSON_AddNumberToObject(root, "seed", -5);
}

static void mutate_drop_days(cJSON *root) {
    cJSON_DeleteItemFromObjectCaseSensitive(root, "days");
}

/* --- cases ---------------------------------------------------------------- */

static void test_sandbox_itself_is_valid(void) {
    /* Guards the whole file: if copying `../config` produced something the
     * loader rejects, every rejection case below would "pass" for the wrong
     * reason. */
    char sandbox[PATH_MAX];
    if (!make_sandbox(sandbox, sizeof(sandbox))) {
        failures++;
        printf("FAIL could not build sandbox\n");
        return;
    }
    ResolvedConfig config;
    ConfigError error;
    bool ok = config_load_directory(sandbox, &config, &error);
    report(ok, "an unmodified copy of ../config still loads");
    if (!ok) printf("     (%s)\n", error.message);
    config_destroy(&config);

    SimulationSettings settings;
    report(config_load_simulation_settings(sandbox, &settings, &error),
           "an unmodified copy of simulation_settings.json still loads");
    remove_sandbox(sandbox);
}

static void test_io_and_json_errors(void) {
    expect_rejected("crops", NULL, CONFIG_ERROR_IO, "crops", "a missing document is an IO error");
    expect_rejected("simulation_settings", NULL, CONFIG_ERROR_IO, NULL,
                    "a missing simulation_settings.json is an IO error");

    /* Truncated JSON, written as raw text -- there is no way to build an
     * unparseable document through cJSON. */
    char sandbox[PATH_MAX];
    if (!make_sandbox(sandbox, sizeof(sandbox))) {
        failures++;
        printf("FAIL could not build sandbox\n");
        return;
    }
    char path[PATH_MAX];
    snprintf(path, sizeof(path), "%s/crops.json", sandbox);
    write_text(path, "[ {\"id\": \"quickweed\" ");
    ResolvedConfig config;
    ConfigError error;
    memset(&config, 0xA5, sizeof(config));
    bool ok = config_load_directory(sandbox, &config, &error);
    config_destroy(&config);
    report(!ok && error.code == CONFIG_ERROR_JSON, "truncated JSON is a JSON error");
    report(strstr(error.message, "crops") != NULL, "the JSON error names the document");
    remove_sandbox(sandbox);

    /* Parses cleanly, but the top level is the wrong kind. crops.json is an
     * array, so an object there is the inversion worth checking. */
    if (!make_sandbox(sandbox, sizeof(sandbox))) return;
    snprintf(path, sizeof(path), "%s/crops.json", sandbox);
    write_text(path, "{\"crops\": []}");
    memset(&config, 0xA5, sizeof(config));
    ok = config_load_directory(sandbox, &config, &error);
    config_destroy(&config);
    report(!ok && error.code == CONFIG_ERROR_SCHEMA,
           "an object where the top-level array belongs is a schema error");
    remove_sandbox(sandbox);

    /* An empty file is neither valid JSON nor a valid document. */
    if (!make_sandbox(sandbox, sizeof(sandbox))) return;
    snprintf(path, sizeof(path), "%s/crops.json", sandbox);
    write_text(path, "");
    memset(&config, 0xA5, sizeof(config));
    ok = config_load_directory(sandbox, &config, &error);
    config_destroy(&config);
    report(!ok, "an empty document is rejected");
    remove_sandbox(sandbox);
}

static void test_schema_errors(void) {
    expect_rejected("crops", mutate_drop_required_crop_field, CONFIG_ERROR_SCHEMA, "crops[0]",
                    "a missing required field is rejected, naming the element");
    expect_rejected("crops", mutate_crop_field_wrong_type, CONFIG_ERROR_SCHEMA, "must be an integer",
                    "a string where an integer belongs is rejected");
    expect_rejected("crops", mutate_unknown_crop_field, CONFIG_ERROR_SCHEMA, "unknown field",
                    "an unknown field is rejected rather than ignored");
    expect_rejected("crops", mutate_bad_crop_role, CONFIG_ERROR_SCHEMA, "invalid enum value",
                    "an out-of-set enum value is rejected");
    expect_rejected("crops", mutate_duplicate_crop_id, CONFIG_ERROR_SCHEMA, "id",
                    "a duplicate crop id is rejected");
    expect_rejected("markets", mutate_drop_spot_channel, CONFIG_ERROR_REFERENCE, "spot",
                    "a missing 'spot' channel is rejected");
    expect_rejected("crops", mutate_three_element_range, CONFIG_ERROR_SCHEMA, "exactly two values",
                    "a range with three entries is rejected");
}

static void test_range_errors(void) {
    expect_rejected("crops", mutate_negative_growth_days, CONFIG_ERROR_RANGE, "too small",
                    "a below-minimum integer is a range error");
    expect_rejected("crops", mutate_inverted_temperature_range, CONFIG_ERROR_RANGE,
                    "not ordered", "an inverted [low, high] range is a range error");
}

static void test_reference_errors(void) {
    expect_rejected("processing", mutate_unresolvable_recipe_input, CONFIG_ERROR_REFERENCE,
                    "unknown recipe item", "a recipe input naming no known item is rejected");
    expect_rejected("buyers", mutate_unresolvable_buyer_item, CONFIG_ERROR_REFERENCE, NULL,
                    "a buyer item naming no known item is rejected");
}

static void test_simulation_settings_errors(void) {
    expect_rejected("simulation_settings", mutate_drop_days, CONFIG_ERROR_SCHEMA, "days",
                    "a missing required setting is rejected");
    expect_rejected("simulation_settings", mutate_zero_days, CONFIG_ERROR_RANGE, "days",
                    "zero days is rejected");
    expect_rejected("simulation_settings", mutate_zero_slots, CONFIG_ERROR_RANGE, "start_slots",
                    "zero starting slots is rejected");
    expect_rejected("simulation_settings", mutate_negative_start_money, CONFIG_ERROR_RANGE,
                    "start_money", "negative starting money is rejected");
    expect_rejected("simulation_settings", mutate_negative_seed, CONFIG_ERROR_SCHEMA, "seed",
                    "a negative seed is rejected");
}

static void test_argument_errors(void) {
    ConfigError error;
    ResolvedConfig config;
    report(!config_load_directory(NULL, &config, &error) && error.code == CONFIG_ERROR_ARGUMENT,
           "a NULL directory is an argument error");
    report(!config_load_directory("../config", NULL, &error) && error.code == CONFIG_ERROR_ARGUMENT,
           "a NULL destination is an argument error");

    /* A NULL error pointer must not crash: the loader stashes it in a file
     * static and every errorf has to tolerate its absence. */
    memset(&config, 0xA5, sizeof(config));
    report(!config_load_directory("does-not-exist", &config, NULL),
           "a failing load with no error pointer does not crash");
    config_destroy(&config);

    /* Teardown on a zeroed object, per docs/c-port-plan.md Section 9. */
    ResolvedConfig zeroed;
    memset(&zeroed, 0, sizeof(zeroed));
    config_destroy(&zeroed);
    report(true, "config_destroy is safe on a zeroed object");
}

int main(void) {
    test_sandbox_itself_is_valid();
    test_io_and_json_errors();
    test_schema_errors();
    test_range_errors();
    test_reference_errors();
    test_simulation_settings_errors();
    test_argument_errors();

    if (failures > 0) {
        printf("%d failed\n", failures);
        return 1;
    }
    puts("invalid-config tests passed");
    return 0;
}
