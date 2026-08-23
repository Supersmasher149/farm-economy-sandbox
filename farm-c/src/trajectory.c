#include "trajectory.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "blake2b.h"
#include "pyfloat.h"

/* --- payload string builder ---------------------------------------------
 *
 * Appends into trajectory->scratch, which is reused across days. Any
 * allocation failure latches trajectory->failed and every later append
 * becomes a no-op, so a short write can never masquerade as a valid digest.
 */

static bool sb_reserve(Trajectory *t, size_t extra) {
    size_t needed = t->scratch_len + extra + 1;
    if (needed <= t->scratch_capacity) return true;
    size_t capacity = t->scratch_capacity == 0 ? 4096 : t->scratch_capacity;
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2) {
            t->failed = true;
            return false;
        }
        capacity *= 2;
    }
    char *grown = realloc(t->scratch, capacity);
    if (grown == NULL) {
        t->failed = true;
        return false;
    }
    t->scratch = grown;
    t->scratch_capacity = capacity;
    return true;
}

static void sb_printf(Trajectory *t, const char *format, ...) {
    if (t->failed) return;
    va_list args;
    va_start(args, format);
    va_list probe;
    va_copy(probe, args);
    int needed = vsnprintf(NULL, 0, format, probe);
    va_end(probe);
    if (needed < 0) {
        t->failed = true;
        va_end(args);
        return;
    }
    if (!sb_reserve(t, (size_t)needed)) {
        va_end(args);
        return;
    }
    vsnprintf(t->scratch + t->scratch_len, t->scratch_capacity - t->scratch_len, format, args);
    t->scratch_len += (size_t)needed;
    va_end(args);
}

/* Floats always go through here; see trajectory.h on why not "%g"/"%a". */
static void sb_hex(Trajectory *t, const char *label, double value) {
    char buf[PY_FLOAT_HEX_BUFSIZE];
    sb_printf(t, " %s=%s", label, py_float_hex(value, buf));
}

/* --- sorted, default-skipping emission for dict-shaped state -------------
 *
 * See trajectory.h: sorting by key and skipping defaults is what lets the C's
 * dense arrays and Python's sparse dicts produce the same bytes.
 */

typedef struct {
    const char *key;
    size_t index;
} KeyRef;

static int keyref_cmp(const void *a, const void *b) {
    return strcmp(((const KeyRef *)a)->key, ((const KeyRef *)b)->key);
}

/* Config tables are small (tens of entries); a fixed cap keeps this
 * allocation-free and is checked rather than assumed. */
#define MAX_KEYS 256

static const char *item_key(const ResolvedConfig *config, size_t index) {
    const ItemDef *item = config_find_item(config, (ItemId)index);
    return item != NULL && item->external_id != NULL ? item->external_id : "?";
}

static void emit_double_map(Trajectory *t, const char *label, const KeyRef *keys, size_t count,
                            const double *values) {
    sb_printf(t, "%s", label);
    char buf[PY_FLOAT_HEX_BUFSIZE];
    for (size_t i = 0; i < count; i++) {
        sb_printf(t, " %s=%s", keys[i].key, py_float_hex(values[keys[i].index], buf));
    }
    sb_printf(t, "\n");
}

static void emit_int_map(Trajectory *t, const char *label, const KeyRef *keys, size_t count,
                         const int *values) {
    sb_printf(t, "%s", label);
    for (size_t i = 0; i < count; i++) {
        sb_printf(t, " %s=%d", keys[i].key, values[keys[i].index]);
    }
    sb_printf(t, "\n");
}

/* Collects the non-default entries of a dense double array, keyed and
 * sorted. `count` is clamped to MAX_KEYS with a latched failure rather than
 * a silent truncation. */
static size_t collect_doubles(Trajectory *t, const ResolvedConfig *config, const double *values,
                              const bool *present, size_t count, KeyRef *out,
                              const char *(*key_of)(const ResolvedConfig *, size_t)) {
    size_t used = 0;
    for (size_t i = 0; i < count; i++) {
        if (present != NULL && !present[i]) continue;
        if (values[i] == 0.0) continue; /* the skipped default; see trajectory.h */
        if (used >= MAX_KEYS) {
            t->failed = true;
            return 0;
        }
        out[used].key = key_of(config, i);
        out[used].index = i;
        used++;
    }
    qsort(out, used, sizeof(*out), keyref_cmp);
    return used;
}

static size_t collect_ints(Trajectory *t, const ResolvedConfig *config, const int *values,
                           size_t count, KeyRef *out,
                           const char *(*key_of)(const ResolvedConfig *, size_t)) {
    size_t used = 0;
    for (size_t i = 0; i < count; i++) {
        if (values[i] == 0) continue;
        if (used >= MAX_KEYS) {
            t->failed = true;
            return 0;
        }
        out[used].key = key_of(config, i);
        out[used].index = i;
        used++;
    }
    qsort(out, used, sizeof(*out), keyref_cmp);
    return used;
}

static const char *upgrade_key(const ResolvedConfig *config, size_t index) {
    const char *id = config->upgrades[index].external_id;
    return id != NULL ? id : "?";
}

static const char *buyer_key(const ResolvedConfig *config, size_t index) {
    const char *id = config->buyers[index].external_id;
    return id != NULL ? id : "?";
}

static const char *channel_key(const ResolvedConfig *config, size_t index) {
    const char *id = config->channels[index].external_id;
    return id != NULL ? id : "?";
}

static const char *const QUALITY_NAMES[QUALITY_COUNT] = {"rejected", "processing", "standard",
                                                         "premium"};

/* simulation/state.py:204-208 record_expense's category strings, in
 * ExpenseCategory order. */
static const char *const EXPENSE_NAMES[EXPENSE_COUNT] = {
    "seeds", "watering", "fertilizer", "upgrades", "storage", "processing", "contract_penalties"};

static const char *const SEASON_NAMES[SEASON_COUNT] = {"spring", "summer", "autumn", "winter"};

/* A fixed-name map (expenses, quality grades, loss causes): same
 * sorted-and-default-skipped discipline, but the keys are literals rather
 * than config ids. */
static void emit_named_doubles(Trajectory *t, const char *label, const char *const *names,
                               const double *values, size_t count) {
    KeyRef keys[MAX_KEYS];
    size_t used = 0;
    for (size_t i = 0; i < count && used < MAX_KEYS; i++) {
        if (values[i] == 0.0) continue;
        keys[used].key = names[i];
        keys[used].index = i;
        used++;
    }
    qsort(keys, used, sizeof(*keys), keyref_cmp);
    emit_double_map(t, label, keys, used, values);
}

static void emit_named_ints(Trajectory *t, const char *label, const char *const *names,
                            const int *values, size_t count) {
    KeyRef keys[MAX_KEYS];
    size_t used = 0;
    for (size_t i = 0; i < count && used < MAX_KEYS; i++) {
        if (values[i] == 0) continue;
        keys[used].key = names[i];
        keys[used].index = i;
        used++;
    }
    qsort(keys, used, sizeof(*keys), keyref_cmp);
    emit_int_map(t, label, keys, used, values);
}

/* Nullable ints (bankruptcy_day, an unset shelf life, a plot with no crop)
 * print as "-", matching the Python mirror's None. */
static void sb_optint(Trajectory *t, const char *label, int value, bool present) {
    if (present) {
        sb_printf(t, " %s=%d", label, value);
    } else {
        sb_printf(t, " %s=-", label);
    }
}

static void emit_contracts(Trajectory *t, const char *label, const ResolvedConfig *config,
                           const ContractVec *contracts) {
    char buf[PY_FLOAT_HEX_BUFSIZE];
    char buf2[PY_FLOAT_HEX_BUFSIZE];
    for (size_t i = 0; i < contracts->count; i++) {
        const ContractRecord *c = &contracts->data[i];
        sb_printf(t,
                  "%s %zu buyer=%s item=%s qty=%d delivered=%d minq=%d price=%s penalty=%s "
                  "offered=%d deadline=%d accepted=%d resolved=%d\n",
                  label, i, buyer_key(config, c->buyer_id), item_key(config, c->item_id),
                  c->quantity, c->delivered, (int)c->min_quality,
                  py_float_hex(c->unit_price, buf), py_float_hex(c->penalty_rate, buf2),
                  c->offered_day, c->deadline_day, c->accepted ? 1 : 0, c->resolved ? 1 : 0);
    }
}

const char *trajectory_day_payload(Trajectory *t, const FarmState *state,
                                   const WeatherDay *weather) {
    t->scratch_len = 0;
    if (t->scratch != NULL) t->scratch[0] = '\0';
    const ResolvedConfig *config = state->config;
    char buf[PY_FLOAT_HEX_BUFSIZE];

    /* --- scalars. Everything that could legitimately be 0.0 lives here
     * rather than in a skip-defaults map, so a real zero stays visible. --- */
    sb_printf(t, "day %d\n", state->day);

    sb_printf(t, "weather season=%s",
              weather != NULL && weather->season < SEASON_COUNT ? SEASON_NAMES[weather->season]
                                                                : "-");
    if (weather != NULL) {
        sb_hex(t, "temperature", weather->temperature);
        sb_hex(t, "rainfall", weather->rainfall);
        sb_hex(t, "evaporation", weather->evaporation);
    }
    sb_printf(t, "\n");

    sb_printf(t, "cash");
    sb_hex(t, "money", state->money);
    sb_hex(t, "revenue", state->total_revenue);
    sb_hex(t, "expenses", state->total_expenses);
    sb_hex(t, "reputation", state->reputation);
    sb_hex(t, "lowest", state->lowest_money);
    /* highest_money is None on a fresh PlayerState and economy_rules reads
     * that as a real state -- so absence is emitted, not coerced to 0.0. */
    if (state->has_highest_money) {
        sb_hex(t, "highest", state->highest_money);
    } else {
        sb_printf(t, " highest=-");
    }
    sb_hex(t, "processing_revenue", state->processing_revenue);
    sb_hex(t, "contract_penalties", state->contract_penalties);
    sb_hex(t, "contract_revenue", state->contract_channel_revenue);
    sb_printf(t, "\n");

    sb_printf(t, "farm bankrupt=%d", state->bankrupt ? 1 : 0);
    sb_optint(t, "bankruptcy_day", state->bankruptcy_day, state->bankruptcy_day != INVALID_DAY);
    sb_printf(t, " reason=%s",
              state->bankruptcy_reason != NULL ? state->bankruptcy_reason : "-");
    sb_printf(t, " slots=%d planted=%zu fertilizer=%d", state->slots_total, state->planted.count,
              state->fertilizer_inventory);
    sb_hex(t, "water_units", state->water_units);
    sb_printf(t, "\n");

    sb_printf(t,
              "tally planted=%d harvested=%d sold=%d spoiled=%d processed=%d waterings=%d "
              "harvest_events=%d lost=%d fert_bought=%d fert_applied=%d contracts_done=%d "
              "contracts_failed=%d idle=%d slot_days=%d occupied=%d\n",
              state->total_planted, state->total_harvested, state->total_sold,
              state->total_spoiled, state->total_processed, state->total_waterings,
              state->total_harvest_events, state->total_crops_lost,
              state->total_fertilizer_bought, state->total_fertilizer_applied,
              state->contracts_completed, state->contracts_failed, state->idle_days,
              state->slot_days, state->occupied_slot_days);

    /* --- fixed-name maps --- */
    emit_named_doubles(t, "expenses", EXPENSE_NAMES, state->expenses_by_category, EXPENSE_COUNT);
    emit_named_ints(t, "quality", QUALITY_NAMES, state->quality_harvested, QUALITY_COUNT);
    {
        /* losses_by_cause's three keys (state.h's ExpenseCategory-style
         * flattening), rebuilt as a map so the sorted/skip-default rule
         * applies to them too. */
        static const char *const LOSS_NAMES[3] = {"crop_loss_events", "rejected_quality_units",
                                                  "spoilage_units"};
        const int losses[3] = {state->crop_loss_events, state->rejected_quality_units,
                               state->spoilage_units};
        emit_named_ints(t, "losses", LOSS_NAMES, losses, 3);
    }

    /* --- config-keyed maps --- */
    KeyRef keys[MAX_KEYS];
    size_t used;

    used = collect_doubles(t, config, state->market_prices, state->has_market_price,
                           config->item_count, keys, item_key);
    emit_double_map(t, "prices", keys, used, state->market_prices);

    used = collect_doubles(t, config, state->market_supply, NULL, config->item_count, keys,
                           item_key);
    emit_double_map(t, "supply", keys, used, state->market_supply);

    used = collect_ints(t, config, state->seed_inventory, config->item_count, keys, item_key);
    emit_int_map(t, "seeds", keys, used, state->seed_inventory);

    used = collect_ints(t, config, state->crop_plant_counts, config->item_count, keys, item_key);
    emit_int_map(t, "plant_counts", keys, used, state->crop_plant_counts);

    used = collect_doubles(t, config, state->buyer_relationships, NULL, config->buyer_count, keys,
                           buyer_key);
    emit_double_map(t, "buyers", keys, used, state->buyer_relationships);

    used = collect_doubles(t, config, state->revenue_by_channel, NULL, config->channel_count, keys,
                           channel_key);
    emit_double_map(t, "channel_revenue", keys, used, state->revenue_by_channel);

    used = collect_ints(t, config, state->channel_capacity_used, config->channel_count, keys,
                        channel_key);
    emit_int_map(t, "channel_used", keys, used, state->channel_capacity_used);

    /* Upgrades: owned set plus purchase day, sorted by id. Python keeps
     * these as a set and a dict; an unowned upgrade appears in neither. */
    {
        size_t owned = 0;
        for (size_t i = 0; i < config->upgrade_count && owned < MAX_KEYS; i++) {
            if (!state->upgrades_owned[i]) continue;
            keys[owned].key = upgrade_key(config, i);
            keys[owned].index = i;
            owned++;
        }
        qsort(keys, owned, sizeof(*keys), keyref_cmp);
        sb_printf(t, "upgrades");
        for (size_t i = 0; i < owned; i++) {
            int day = state->upgrade_purchase_days[keys[i].index];
            sb_printf(t, " %s@", keys[i].key);
            if (day == INVALID_DAY) {
                sb_printf(t, "-");
            } else {
                sb_printf(t, "%d", day);
            }
        }
        sb_printf(t, "\n");
    }

    /* --- positional lists. Order is part of the comparison here, not
     * sorted away: a reordered inventory changes which lot a FIFO consume
     * takes. --- */
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        sb_printf(t, "lot %zu item=%s qty=%d quality=%d produced=%d age=%d shelf=%d", i,
                  item_key(config, lot->item_id), lot->quantity, (int)lot->quality,
                  lot->produced_day, lot->age_days, lot->shelf_life_days);
        sb_optint(t, "eff_shelf", lot->effective_shelf_life_days,
                  lot->effective_shelf_life_days > 0);
        sb_printf(t, " type=%d unit_cost=%s\n", (int)lot->item_type,
                  py_float_hex(lot->unit_cost, buf));
    }

    for (size_t i = 0; i < state->processing_jobs.count; i++) {
        const ProcessingJob *job = &state->processing_jobs.data[i];
        sb_printf(t, "job %zu recipe=%s out=%s qty=%d done=%d shelf=%d unit_cost=%s\n", i,
                  config->recipes[job->recipe_id].external_id, item_key(config, job->output_item_id),
                  job->output_quantity, job->completion_day, job->shelf_life_days,
                  py_float_hex(job->unit_cost, buf));
    }

    emit_contracts(t, "active", config, &state->active_contracts);
    emit_contracts(t, "offer", config, &state->contract_offers);

    for (size_t i = 0; i < state->planted.count; i++) {
        sb_printf(t, "crop %zu item=%s planted=%d grow=%d watered=%d neglect=%d fert=%d plot=%d", i,
                  item_key(config, state->planted.crop_item_id[i]), state->planted.day_planted[i],
                  state->planted.growth_days_required[i], state->planted.last_watered_day[i],
                  state->planted.neglect_days[i], state->planted.fertilized[i] ? 1 : 0,
                  state->planted.plot_index[i]);
        sb_hex(t, "accrued", state->planted.accrued_cost[i]);
        sb_hex(t, "water_stress", state->planted.water_stress[i]);
        sb_hex(t, "nutrient_stress", state->planted.nutrient_stress[i]);
        sb_hex(t, "temperature_stress", state->planted.temperature_stress[i]);
        sb_hex(t, "pest_stress", state->planted.pest_stress[i]);
        sb_hex(t, "disease_stress", state->planted.disease_stress[i]);
        sb_printf(t, "\n");
    }

    for (size_t i = 0; i < state->plots.count; i++) {
        sb_printf(t, "plot %zu", i);
        sb_hex(t, "moisture", state->plots.moisture[i]);
        sb_hex(t, "nitrogen", state->plots.nitrogen[i]);
        sb_hex(t, "phosphorus", state->plots.phosphorus[i]);
        sb_hex(t, "potassium", state->plots.potassium[i]);
        sb_hex(t, "ph", state->plots.ph[i]);
        sb_hex(t, "soil_health", state->plots.soil_health[i]);
        sb_hex(t, "pest_pressure", state->plots.pest_pressure[i]);
        sb_hex(t, "disease_pressure", state->plots.disease_pressure[i]);
        sb_printf(t, " family=%s", state->plots.previous_crop_family[i] != NULL
                                        ? state->plots.previous_crop_family[i]
                                        : "-");
        /* The index rather than the crop's fields: it is emitted above, and
         * comparing the index checks the C's plot<->planted bookkeeping
         * against Python's object graph (where plot.crop is the object
         * itself and the mirror recovers its position by identity). */
        sb_optint(t, "crop", state->plots.planted_index[i], state->plots.planted_index[i] >= 0);
        sb_printf(t, "\n");
    }

    if (t->failed) return NULL;
    return t->scratch != NULL ? t->scratch : "";
}

/* --- digest --------------------------------------------------------------- */

void trajectory_init(Trajectory *trajectory, bool keep_per_day) {
    memset(trajectory, 0, sizeof(*trajectory));
    trajectory->keep_per_day = keep_per_day;
}

void trajectory_destroy(Trajectory *trajectory) {
    if (trajectory == NULL) return;
    free(trajectory->per_day);
    free(trajectory->scratch);
    memset(trajectory, 0, sizeof(*trajectory));
}

static void record_per_day(Trajectory *t) {
    if (!t->keep_per_day || t->failed) return;
    if (t->per_day_count == t->per_day_capacity) {
        size_t capacity = t->per_day_capacity == 0 ? 64 : t->per_day_capacity * 2;
        void *grown = realloc(t->per_day, capacity * sizeof(*t->per_day));
        if (grown == NULL) {
            t->failed = true;
            return;
        }
        t->per_day = grown;
        t->per_day_capacity = capacity;
    }
    trajectory_digest(t, t->per_day[t->per_day_count]);
    t->per_day_count++;
}

void trajectory_observe_day(const FarmState *state, const WeatherDay *weather, void *context) {
    Trajectory *t = context;
    if (t == NULL || t->failed) return;
    const char *payload = trajectory_day_payload(t, state, weather);
    if (payload == NULL) {
        t->failed = true;
        return;
    }

    /* Chained, not a hash of the concatenation: chaining is what makes the
     * first differing per-day digest the first day that actually diverged
     * (every later one differs by construction), which is what `golden
     * trace` bisects on. Mirrors golden_replay.py's _Trajectory.__call__. */
    size_t payload_len = t->scratch_len;
    size_t combined_len = t->running_len + payload_len;
    uint8_t *combined = malloc(combined_len);
    if (combined == NULL) {
        t->failed = true;
        return;
    }
    memcpy(combined, t->running, t->running_len);
    memcpy(combined + t->running_len, payload, payload_len);
    blake2b_hash(combined, combined_len, t->running, sizeof(t->running));
    free(combined);
    t->running_len = sizeof(t->running);

    t->day_count++;
    record_per_day(t);
}

const char *trajectory_digest(const Trajectory *trajectory, char *out) {
    if (trajectory->failed) {
        snprintf(out, TRAJECTORY_DIGEST_HEX_SIZE, "unavailable");
        return out;
    }
    /* Before the first day the chain is still the empty prefix, whose hex is
     * the empty string -- matching Python's b"".hex(). */
    for (size_t i = 0; i < trajectory->running_len; i++) {
        snprintf(out + i * 2, 3, "%02x", trajectory->running[i]);
    }
    out[trajectory->running_len * 2] = '\0';
    return out;
}
