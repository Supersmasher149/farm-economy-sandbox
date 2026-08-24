#include "rng_hash.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "blake2b.h"

typedef struct {
    char *data;
    size_t len;
    size_t capacity;
} StrBuf;

static _Thread_local bool rng_hash_allocation_failed;

void rng_hash_clear_allocation_failure(void) {
    rng_hash_allocation_failed = false;
}

bool rng_hash_had_allocation_failure(void) {
    return rng_hash_allocation_failed;
}

void rng_hash_mark_allocation_failure(void) {
    rng_hash_allocation_failed = true;
}

static bool sb_reserve(StrBuf *sb, size_t extra) {
    if (sb->len == SIZE_MAX || extra > SIZE_MAX - sb->len - 1) {
        return false;
    }
    if (sb->len + extra + 1 <= sb->capacity) {
        return true;
    }
    size_t new_capacity = sb->capacity == 0 ? 64 : sb->capacity;
    while (sb->len + extra + 1 > new_capacity) {
        if (new_capacity > SIZE_MAX / 2) {
            new_capacity = sb->len + extra + 1;
            break;
        }
        new_capacity *= 2;
    }
    char *grown = realloc(sb->data, new_capacity);
    if (grown == NULL) {
        return false;
    }
    sb->data = grown;
    sb->capacity = new_capacity;
    return true;
}

static bool sb_append(StrBuf *sb, const char *text) {
    size_t n = strlen(text);
    if (!sb_reserve(sb, n)) return false;
    memcpy(sb->data + sb->len, text, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
    return true;
}

static bool sb_append_char(StrBuf *sb, char c) {
    if (!sb_reserve(sb, 1)) return false;
    sb->data[sb->len++] = c;
    sb->data[sb->len] = '\0';
    return true;
}

static bool sb_append_long(StrBuf *sb, long value) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", value);
    return sb_append(sb, buf);
}

static bool sb_append_uint64(StrBuf *sb, uint64_t value) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%" PRIu64, value);
    return sb_append(sb, buf);
}

/* Python str repr's quote-selection rule (the one piece of the algorithm
 * this file implements in full, since it's cheap and this project's config
 * ids are plain ASCII with no other repr-affecting characters): prefer
 * single quotes; switch to double quotes only if the string contains a
 * single quote and no double quote. Backslash and the chosen quote
 * character are escaped; every other byte is emitted as-is, which is exact
 * for this codebase's ASCII identifiers (see rng_hash.h's scope note). */
static bool sb_append_str_repr(StrBuf *sb, const char *s) {
    bool has_squote = strchr(s, '\'') != NULL;
    bool has_dquote = strchr(s, '"') != NULL;
    char quote = (has_squote && !has_dquote) ? '"' : '\'';
    if (!sb_append_char(sb, quote)) return false;
    for (const char *p = s; *p != '\0'; p++) {
        if (*p == quote || *p == '\\') {
            if (!sb_append_char(sb, '\\')) return false;
        }
        if (!sb_append_char(sb, *p)) return false;
    }
    return sb_append_char(sb, quote);
}

/* repr() of a homogeneous tuple of strings, e.g. ('a', 'b') or ('a',) or (). */
static bool sb_append_str_tuple_repr(StrBuf *sb, const char *const *items, size_t count) {
    if (!sb_append_char(sb, '(')) return false;
    for (size_t i = 0; i < count; i++) {
        if (i > 0) {
            if (!sb_append(sb, ", ")) return false;
        }
        if (!sb_append_str_repr(sb, items[i])) return false;
    }
    if (count == 1) {
        if (!sb_append_char(sb, ',')) return false;
    }
    return sb_append_char(sb, ')');
}

static bool sb_append_repr_value(StrBuf *sb, const ReprValue *value) {
    switch (value->kind) {
        case REPR_INT:
            return sb_append_long(sb, value->int_value);
        case REPR_STR:
            return sb_append_str_repr(sb, value->str_value);
        case REPR_STR_TUPLE:
            return sb_append_str_tuple_repr(sb, value->tuple_items, value->tuple_count);
    }
    return false;
}

/* repr() of the heterogeneous `context` tuple (agents/random_agent.py's
 * *args), e.g. ('choose_crop', 0, 5, ('a', 'b')) or ('upgrade', 'well'). */
static bool sb_append_context_tuple_repr(StrBuf *sb, const ReprValue *values, size_t count) {
    if (!sb_append_char(sb, '(')) return false;
    for (size_t i = 0; i < count; i++) {
        if (i > 0) {
            if (!sb_append(sb, ", ")) return false;
        }
        if (!sb_append_repr_value(sb, &values[i])) return false;
    }
    if (count == 1) {
        if (!sb_append_char(sb, ',')) return false;
    }
    return sb_append_char(sb, ')');
}

double rng_decision_random(bool has_run_seed, uint64_t run_seed, int day, const ReprValue *context,
                            size_t context_count) {
    StrBuf sb = {0};
    /* repr((seed_or_0, day, context)) -- a plain 3-tuple, never length 1,
     * so no trailing-comma case to handle here. */
    bool ok = sb_append_char(&sb, '(') &&
              sb_append_uint64(&sb, has_run_seed ? run_seed : 0) &&
              sb_append(&sb, ", ") && sb_append_long(&sb, (long)day) &&
              sb_append(&sb, ", ") &&
              sb_append_context_tuple_repr(&sb, context, context_count) &&
              sb_append_char(&sb, ')');
    if (!ok) {
        rng_hash_allocation_failed = true;
        free(sb.data);
        return 0.0;
    }

    uint8_t digest[8];
    blake2b_hash(sb.data, sb.len, digest, sizeof(digest));
    free(sb.data);

    uint64_t value = 0;
    for (int i = 0; i < 8; i++) {
        value = (value << 8) | digest[i];
    }
    return (double)value / 18446744073709551616.0; /* 2**64 */
}
