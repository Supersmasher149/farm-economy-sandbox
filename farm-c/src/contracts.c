#include "contracts.h"

#include <stdlib.h>
#include <string.h>

#include "derived.h"
#include "economy.h"
#include "markets.h"
#include "vec_util.h"

/* --- small local dynamic-array helpers, private to this file --- */

typedef struct {
    int *data;
    size_t count;
    size_t capacity;
} IntVec;

static bool int_vec_push(IntVec *vec, int value) {
    if (!vec_grow((void **)&vec->data, &vec->capacity, vec->count, sizeof(int))) {
        return false;
    }
    vec->data[vec->count++] = value;
    return true;
}

static void int_vec_free(IntVec *vec) {
    free(vec->data);
    *vec = (IntVec){0};
}

static int cmp_int(const void *a, const void *b) {
    int ia = *(const int *)a;
    int ib = *(const int *)b;
    return (ia > ib) - (ia < ib);
}

static void int_vec_sort(IntVec *vec) {
    qsort(vec->data, vec->count, sizeof(int), cmp_int);
}

static double min2(double a, double b) {
    return a < b ? a : b;
}
static double max2(double a, double b) {
    return a > b ? a : b;
}

/* --- simulation/contracts.py:24-33 offer expiry --- */

static bool is_offer_expired(const FarmState *state, const ResolvedConfig *config,
                              const ContractRecord *offer) {
    return state->day > offer->offered_day + config->contracts.offer_expiry_days;
}

/* --- simulation/contracts.py:155-169 _inventory_quantity. Distinct from
 * inventory.c's `available_quantity`: this one also requires positive
 * remaining shelf life, matching Python's two separate functions. --- */

static int contract_inventory_quantity(const FarmState *state, ItemId item_id,
                                        Quality min_quality) {
    int total = 0;
    for (size_t i = 0; i < state->inventory_lots.count; i++) {
        const InventoryLot *lot = &state->inventory_lots.data[i];
        if (lot->item_id == item_id && lot->quantity > 0 &&
            inventory_lot_remaining_shelf_life(lot) > 0 && lot->quality >= min_quality) {
            total += lot->quantity;
        }
    }
    return total;
}

/* --- simulation/contracts.py:179-183 _processing_capacity --- */

static int processing_capacity(const FarmState *state) {
    return state->has_processing_capacity ? state->processing_capacity : 0;
}

/* --- simulation/contracts.py:200-213 _best_possible_grade ---
 *
 * SIMPLIFIED (see contracts.h's scope note): the real grade ceiling needs
 * crop_growth.harvest_multipliers' stress-based quality score, which needs
 * live weather/soil physics this agents-only port doesn't have. Every
 * already-planted crop of the matching item is assumed capable of reaching
 * QUALITY_STANDARD. This only ever *undercounts* committed future supply
 * for contracts requiring above-standard grade (min_quality == premium),
 * since Python's real ceiling could be lower than standard when a crop is
 * already badly stressed -- reconcile once crop_growth/weather are ported.
 */
static Quality best_possible_grade_SIMPLIFIED(const PlantedCrop *planted) {
    (void)planted;
    return QUALITY_STANDARD;
}

/* --- simulation/contracts.py:216-311 _future_crop_arrivals --- */

typedef struct {
    IntVec guaranteed_days;
    IntVec seeded_days;
    double expected_yield;
    double seed_cash_needed;
} FutureCropArrivals;

static void future_crop_arrivals_free(FutureCropArrivals *arrivals) {
    int_vec_free(&arrivals->guaranteed_days);
    int_vec_free(&arrivals->seeded_days);
}

static void push_replant_cycles(IntVec *seeded_days, int today, int free_after, int growth_days,
                                 int days_available) {
    int max_cycle = int_floor_div(days_available - free_after, growth_days);
    for (int cycle = 1; cycle <= max_cycle; cycle++) {
        int_vec_push(seeded_days, today + free_after + cycle * growth_days);
    }
}

static void future_crop_arrivals(const FarmState *state, const ResolvedConfig *config,
                                  const CropDef *crop, int deadline, Quality min_quality,
                                  FutureCropArrivals *out) {
    memset(out, 0, sizeof(*out));

    deadline = economy_effective_deadline(state, deadline);
    int growth_days = economy_effective_growth_days(crop, state, config);
    if (growth_days < 1) {
        growth_days = 1;
    }
    int days_available = deadline - state->day;
    if (days_available < 0) {
        days_available = 0;
    }

    double expected_yield = (crop->min_yield + crop->max_yield) / 2.0 *
                             (1.0 - crop->loss_chance) * config->contracts.production_safety_factor;

    bool guaranteed_grade = min_quality <= QUALITY_STANDARD;

    if (guaranteed_grade) {
        int open_slots = state->slots_total - (int)state->planted.count;
        if (open_slots < 0) {
            open_slots = 0;
        }
        for (int i = 0; i < open_slots; i++) {
            push_replant_cycles(&out->seeded_days, state->day, 0, growth_days, days_available);
        }
    }

    for (size_t i = 0; i < state->planted.count; i++) {
        const PlantedCrop *planted = &state->planted.data[i];
        int days_until_free = planted->growth_days_required - (state->day - planted->day_planted);
        if (days_until_free < 0) {
            days_until_free = 0;
        }
        if (planted->crop_item_id == crop->item_id) {
            if (days_until_free > days_available) {
                continue;
            }
            Quality best_grade = best_possible_grade_SIMPLIFIED(planted);
            if (best_grade >= min_quality) {
                int_vec_push(&out->guaranteed_days, state->day + days_until_free);
            }
            if (guaranteed_grade) {
                push_replant_cycles(&out->seeded_days, state->day, days_until_free, growth_days,
                                     days_available);
            }
        } else if (guaranteed_grade && days_until_free < days_available) {
            push_replant_cycles(&out->seeded_days, state->day, days_until_free, growth_days,
                                 days_available);
        }
    }

    int seed_inventory = state->seed_inventory[crop->item_id];
    double seed_cost = crop->seed_cost;
    long cash_seed_units;
    if (seed_cost > 0) {
        double affordable_cash = max2(0.0, state->money - economy_operating_reserve(state));
        cash_seed_units = (long)(affordable_cash / seed_cost);
    } else {
        cash_seed_units = (long)out->seeded_days.count;
    }
    long funded_capacity = seed_inventory + cash_seed_units;
    size_t funded_seeded_cycles =
        (long)out->seeded_days.count > funded_capacity
            ? (funded_capacity > 0 ? (size_t)funded_capacity : 0)
            : out->seeded_days.count;
    long purchased = (long)funded_seeded_cycles - seed_inventory;
    if (purchased < 0) {
        purchased = 0;
    }

    int_vec_sort(&out->guaranteed_days);
    int_vec_sort(&out->seeded_days);
    /* del seeded_days[funded_seeded_cycles:] -- cash funds the earliest
     * cycles, and the list is ascending, so truncating to the first
     * funded_seeded_cycles entries is exactly that. */
    if (funded_seeded_cycles < out->seeded_days.count) {
        out->seeded_days.count = funded_seeded_cycles;
    }

    out->expected_yield = expected_yield;
    out->seed_cash_needed = (double)purchased * seed_cost;
}

/* --- simulation/contracts.py:314-332 _future_crop_capacity --- */

static void future_crop_capacity(const FarmState *state, const ResolvedConfig *config,
                                  const CropDef *crop, int deadline, Quality min_quality,
                                  double *out_future, double *out_funding,
                                  double *out_free_future) {
    FutureCropArrivals arrivals;
    future_crop_arrivals(state, config, crop, deadline, min_quality, &arrivals);
    *out_future =
        (double)(arrivals.guaranteed_days.count + arrivals.seeded_days.count) * arrivals.expected_yield;
    *out_funding = arrivals.seed_cash_needed;
    *out_free_future = (double)arrivals.guaranteed_days.count * arrivals.expected_yield;
    future_crop_arrivals_free(&arrivals);
}

/* --- simulation/contracts.py:334-351 _InputSupply, :353-407 its helpers --- */

typedef struct {
    int day;
    double quantity;
    bool is_future;
} Arrival;

typedef struct {
    Arrival *data;
    size_t count;
    size_t capacity;
} ArrivalVec;

static bool arrival_vec_push(ArrivalVec *vec, Arrival item) {
    if (!vec_grow((void **)&vec->data, &vec->capacity, vec->count, sizeof(Arrival))) {
        return false;
    }
    vec->data[vec->count++] = item;
    return true;
}

static void arrival_vec_free(ArrivalVec *vec) {
    free(vec->data);
    *vec = (ArrivalVec){0};
}

/* Decorate-sort-undecorate by (day, insertion order) so this reproduces
 * Python list.sort()'s stability guarantee (contracts.py:379-381: inventory
 * already on hand must sort before a harvest landing on the very same day)
 * without needing a hand-rolled stable sort -- qsort is not guaranteed
 * stable, but sorting by (day, original index) as a combined key is. */
typedef struct {
    Arrival arrival;
    size_t original_index;
} ArrivalSortEntry;

static int cmp_arrival_sort_entry(const void *a, const void *b) {
    const ArrivalSortEntry *ea = a;
    const ArrivalSortEntry *eb = b;
    if (ea->arrival.day != eb->arrival.day) {
        return ea->arrival.day < eb->arrival.day ? -1 : 1;
    }
    if (ea->original_index != eb->original_index) {
        return ea->original_index < eb->original_index ? -1 : 1;
    }
    return 0;
}

static void arrival_vec_stable_sort_by_day(ArrivalVec *vec) {
    if (vec->count == 0) {
        return;
    }
    ArrivalSortEntry *entries = malloc(vec->count * sizeof(ArrivalSortEntry));
    for (size_t i = 0; i < vec->count; i++) {
        entries[i] = (ArrivalSortEntry){.arrival = vec->data[i], .original_index = i};
    }
    qsort(entries, vec->count, sizeof(ArrivalSortEntry), cmp_arrival_sort_entry);
    for (size_t i = 0; i < vec->count; i++) {
        vec->data[i] = entries[i].arrival;
    }
    free(entries);
}

typedef struct {
    ArrivalVec arrivals;
    size_t head; /* consumed prefix -- see input_supply_consume */
    double funding;
    double future_total;
    double used_future;
} InputSupply;

static void input_supply_free(InputSupply *supply) {
    arrival_vec_free(&supply->arrivals);
}

static void input_supply_build(const FarmState *state, const ResolvedConfig *config,
                                ItemId input_id, Quality min_quality, int deadline,
                                InputSupply *out) {
    memset(out, 0, sizeof(*out));
    int current = contract_inventory_quantity(state, input_id, min_quality);
    if (current > 0) {
        arrival_vec_push(&out->arrivals,
                          (Arrival){.day = state->day, .quantity = current, .is_future = false});
    }
    const CropDef *crop = config_find_crop(config, input_id);
    if (crop != NULL) {
        FutureCropArrivals arrivals;
        future_crop_arrivals(state, config, crop, deadline, min_quality, &arrivals);
        out->funding = arrivals.seed_cash_needed;

        IntVec harvest_days = {0};
        for (size_t i = 0; i < arrivals.guaranteed_days.count; i++) {
            int_vec_push(&harvest_days, arrivals.guaranteed_days.data[i]);
        }
        for (size_t i = 0; i < arrivals.seeded_days.count; i++) {
            int_vec_push(&harvest_days, arrivals.seeded_days.data[i]);
        }
        int_vec_sort(&harvest_days);
        for (size_t i = 0; i < harvest_days.count; i++) {
            arrival_vec_push(&out->arrivals, (Arrival){.day = harvest_days.data[i],
                                                         .quantity = arrivals.expected_yield,
                                                         .is_future = true});
        }
        out->future_total = (double)harvest_days.count * arrivals.expected_yield;
        int_vec_free(&harvest_days);
        future_crop_arrivals_free(&arrivals);
    }
    arrival_vec_stable_sort_by_day(&out->arrivals);
}

static bool input_supply_arrival_day(const InputSupply *supply, double needed, int *out_day) {
    double remaining = needed;
    for (size_t i = supply->head; i < supply->arrivals.count; i++) {
        remaining -= supply->arrivals.data[i].quantity;
        if (remaining <= 0) {
            *out_day = supply->arrivals.data[i].day;
            return true;
        }
    }
    return false;
}

static void input_supply_consume(InputSupply *supply, double needed) {
    double remaining = needed;
    while (remaining > 0 && supply->head < supply->arrivals.count) {
        Arrival *entry = &supply->arrivals.data[supply->head];
        double take = min2(entry->quantity, remaining);
        entry->quantity -= take;
        remaining -= take;
        if (entry->is_future) {
            supply->used_future += take;
        }
        if (entry->quantity <= 0) {
            supply->head++;
        }
    }
}

/* --- simulation/contracts.py:353-363 _slot_free_days --- */

static void slot_free_days(const FarmState *state, int capacity, IntVec *out) {
    memset(out, 0, sizeof(*out));
    int cap = capacity > 0 ? capacity : 0;

    size_t job_count = state->processing_jobs.count;
    int *completion_days = job_count > 0 ? malloc(job_count * sizeof(int)) : NULL;
    for (size_t i = 0; i < job_count; i++) {
        completion_days[i] = state->processing_jobs.data[i].completion_day;
    }
    qsort(completion_days, job_count, sizeof(int), cmp_int);

    size_t busy_count = (size_t)cap < job_count ? (size_t)cap : job_count;
    for (size_t i = 0; i < busy_count; i++) {
        int free_day = completion_days[i] > state->day ? completion_days[i] : state->day;
        int_vec_push(out, free_day);
    }
    free(completion_days);
    for (size_t i = busy_count; i < (size_t)cap; i++) {
        int_vec_push(out, state->day);
    }
}

/* --- simulation/contracts.py:409-436 _schedule_batches --- */

static int schedule_batches(InputSupply *supply, IntVec *slot_free_day, int input_quantity,
                             int recipe_days, int deadline) {
    if (slot_free_day->count == 0) {
        return 0;
    }
    int batches = 0;
    for (;;) {
        int arrival;
        if (!input_supply_arrival_day(supply, (double)input_quantity, &arrival)) {
            break;
        }
        size_t slot = 0;
        for (size_t i = 1; i < slot_free_day->count; i++) {
            if (slot_free_day->data[i] < slot_free_day->data[slot]) {
                slot = i;
            }
        }
        int start = arrival > slot_free_day->data[slot] ? arrival : slot_free_day->data[slot];
        if (start + recipe_days > deadline) {
            break;
        }
        input_supply_consume(supply, (double)input_quantity);
        slot_free_day->data[slot] = start + recipe_days;
        batches++;
    }
    return batches;
}

/* --- simulation/contracts.py:439-519 _item_capacity ---
 *
 * Python's `seen` parameter is dead code in this codebase: both call sites
 * (`producible_quantity`, `is_offer_feasible`) always use the default empty
 * `()`, and nothing inside `_item_capacity` itself ever recurses with a
 * non-empty one. Intentionally omitted here rather than ported unused.
 */

static void item_capacity(const FarmState *state, const ResolvedConfig *config, ItemId item_id,
                           Quality min_quality, int deadline, double *out_current,
                           double *out_future, double *out_funding) {
    deadline = economy_effective_deadline(state, deadline);
    double current = contract_inventory_quantity(state, item_id, min_quality);
    for (size_t i = 0; i < state->processing_jobs.count; i++) {
        const ProcessingJob *job = &state->processing_jobs.data[i];
        if (job->output_item_id == item_id && job->completion_day <= deadline &&
            min_quality <= QUALITY_STANDARD) {
            current += job->output_quantity;
        }
    }

    const CropDef *crop = config_find_crop(config, item_id);
    if (crop != NULL) {
        double future, funding, free_future;
        future_crop_capacity(state, config, crop, deadline, min_quality, &future, &funding,
                              &free_future);
        *out_current = current;
        *out_future = future;
        *out_funding = funding;
        return;
    }

    double future = 0.0;
    double funding = 0.0;
    if (min_quality > QUALITY_STANDARD) {
        *out_current = current;
        *out_future = future;
        *out_funding = funding;
        return;
    }

    IntVec slot_free_day;
    slot_free_days(state, processing_capacity(state), &slot_free_day);

    size_t supply_capacity = config->recipe_count > 0 ? config->recipe_count : 1;
    ItemId *supply_input_ids = malloc(supply_capacity * sizeof(ItemId));
    InputSupply *supplies = malloc(supply_capacity * sizeof(InputSupply));
    size_t supply_count = 0;

    for (size_t i = 0; i < config->recipe_count; i++) {
        const RecipeDef *recipe = &config->recipes[i];
        if (recipe->output_item_id != item_id || slot_free_day.count == 0) {
            continue;
        }
        int recipe_days = recipe->processing_days > 1 ? recipe->processing_days : 1;
        if (state->day + recipe_days > deadline) {
            continue;
        }

        ItemId input_id = recipe->input_item_id;
        InputSupply *supply = NULL;
        for (size_t s = 0; s < supply_count; s++) {
            if (supply_input_ids[s] == input_id) {
                supply = &supplies[s];
                break;
            }
        }
        if (supply == NULL) {
            supply_input_ids[supply_count] = input_id;
            input_supply_build(state, config, input_id, recipe->min_quality, deadline,
                                &supplies[supply_count]);
            supply = &supplies[supply_count];
            supply_count++;
        }

        int batches =
            schedule_batches(supply, &slot_free_day, recipe->input_quantity, recipe_days, deadline);
        if (batches <= 0) {
            continue;
        }
        future += (double)batches * recipe->output_quantity;
        funding += (double)batches * recipe->cost;
    }

    for (size_t s = 0; s < supply_count; s++) {
        InputSupply *supply = &supplies[s];
        if (supply->used_future > 0 && supply->future_total > 0) {
            funding += supply->funding * min2(1.0, supply->used_future / supply->future_total);
        }
        input_supply_free(supply);
    }
    free(supplies);
    free(supply_input_ids);
    int_vec_free(&slot_free_day);

    *out_current = current;
    *out_future = future;
    *out_funding = funding;
}

/* --- Public API --- */

double contracts_best_market_alternative(const FarmState *state, const ResolvedConfig *config,
                                          const ContractRecord *contract) {
    double best = 0.0;
    bool found = false;
    for (size_t i = 0; i < config->channel_count; i++) {
        MarketQuote quote;
        if (!markets_quote(state, contract->item_id, contract->min_quality, &config->channels[i],
                            contract->quantity, NULL, &quote)) {
            continue;
        }
        double alternative = quote.net / quote.quantity;
        if (!found || alternative > best) {
            best = alternative;
            found = true;
        }
    }
    if (found) {
        return best;
    }
    double market_price =
        state->has_market_price[contract->item_id] ? state->market_prices[contract->item_id] : 0.0;
    return market_price * config->contracts.fallback_price_multiplier;
}

bool contracts_is_offer_profitable(const FarmState *state, const ResolvedConfig *config,
                                    const ContractRecord *contract) {
    return contract->unit_price > contracts_best_market_alternative(state, config, contract);
}

double contracts_forecast_committed_supply(const FarmState *state, const ResolvedConfig *config,
                                            const ContractRecord *contract) {
    int deadline = economy_effective_deadline(state, contract->deadline_day);
    double current = contract_inventory_quantity(state, contract->item_id, contract->min_quality);
    for (size_t i = 0; i < state->processing_jobs.count; i++) {
        const ProcessingJob *job = &state->processing_jobs.data[i];
        if (job->output_item_id == contract->item_id && job->completion_day <= deadline &&
            contract->min_quality <= QUALITY_STANDARD) {
            current += job->output_quantity;
        }
    }
    const CropDef *crop = config_find_crop(config, contract->item_id);
    if (crop != NULL) {
        double future, funding, free_future;
        future_crop_capacity(state, config, crop, deadline, contract->min_quality, &future,
                              &funding, &free_future);
        current += free_future;
    }
    return current;
}

bool contracts_is_offer_feasible(const FarmState *state, const ResolvedConfig *config,
                                  const ContractRecord *contract) {
    if (is_offer_expired(state, config, contract)) {
        return false;
    }
    double current, future, funding;
    item_capacity(state, config, contract->item_id, contract->min_quality, contract->deadline_day,
                   &current, &future, &funding);
    if (current + future < contract->quantity) {
        return false;
    }
    double missing = max2(0.0, contract->quantity - current);
    double free_future = 0.0;
    const CropDef *crop = config_find_crop(config, contract->item_id);
    if (crop != NULL) {
        double crop_future, crop_funding;
        future_crop_capacity(state, config, crop, contract->deadline_day, contract->min_quality,
                              &crop_future, &crop_funding, &free_future);
    }
    double paid_future = max2(0.0, future - free_future);
    double required =
        paid_future > 0 ? funding * (max2(0.0, missing - free_future) / paid_future) : 0.0;
    return required <= max2(0.0, state->money - economy_operating_reserve(state));
}
