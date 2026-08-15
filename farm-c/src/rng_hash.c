#include "rng_hash.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "blake2b.h"

typedef struct {
    char *data;
    size_t len;
    size_t capacity;
} StrBuf;

static void sb_reserve(StrBuf *sb, size_t extra) {
    if (sb->len + extra + 1 <= sb->capacity) {
        return;
    }
    size_t new_capacity = sb->capacity == 0 ? 64 : sb->capacity;
    while (sb->len + extra + 1 > new_capacity) {
        new_capacity *= 2;
    }
    sb->data = realloc(sb->data, new_capacity);
    sb->capacity = new_capacity;
}

static void sb_append(StrBuf *sb, const char *text) {
    size_t n = strlen(text);
    sb_reserve(sb, n);
    memcpy(sb->data + sb->len, text, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

static void sb_append_char(StrBuf *sb, char c) {
    sb_reserve(sb, 1);
    sb->data[sb->len++] = c;
    sb->data[sb->len] = '\0';
}

static void sb_append_long(StrBuf *sb, long value) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%ld", value);
    sb_append(sb, buf);
}

/* Python str repr's quote-selection rule (the one piece of the algorithm
 * this file implements in full, since it's cheap and this project's config
 * ids are plain ASCII with no other repr-affecting characters): prefer
 * single quotes; switch to double quotes only if the string contains a
 * single quote and no double quote. Backslash and the chosen quote
 * character are escaped; every other byte is emitted as-is, which is exact
 * for this codebase's ASCII identifiers (see rng_hash.h's scope note). */
static void sb_append_str_repr(StrBuf *sb, const char *s) {
    bool has_squote = strchr(s, '\'') != NULL;
    bool has_dquote = strchr(s, '"') != NULL;
    char quote = (has_squote && !has_dquote) ? '"' : '\'';
    sb_append_char(sb, quote);
    for (const char *p = s; *p != '\0'; p++) {
        if (*p == quote || *p == '\\') {
            sb_append_char(sb, '\\');
        }
        sb_append_char(sb, *p);
    }
    sb_append_char(sb, quote);
}

/* repr() of a homogeneous tuple of strings, e.g. ('a', 'b') or ('a',) or (). */
static void sb_append_str_tuple_repr(StrBuf *sb, const char *const *items, size_t count) {
    sb_append_char(sb, '(');
    for (size_t i = 0; i < count; i++) {
        if (i > 0) {
            sb_append(sb, ", ");
        }
        sb_append_str_repr(sb, items[i]);
    }
    if (count == 1) {
        sb_append_char(sb, ',');
    }
    sb_append_char(sb, ')');
}

static void sb_append_repr_value(StrBuf *sb, const ReprValue *value) {
    switch (value->kind) {
        case REPR_INT:
            sb_append_long(sb, value->int_value);
            break;
        case REPR_STR:
            sb_append_str_repr(sb, value->str_value);
            break;
        case REPR_STR_TUPLE:
            sb_append_str_tuple_repr(sb, value->tuple_items, value->tuple_count);
            break;
    }
}

/* repr() of the heterogeneous `context` tuple (agents/random_agent.py's
 * *args), e.g. ('choose_crop', 0, 5, ('a', 'b')) or ('upgrade', 'well'). */
static void sb_append_context_tuple_repr(StrBuf *sb, const ReprValue *values, size_t count) {
    sb_append_char(sb, '(');
    for (size_t i = 0; i < count; i++) {
        if (i > 0) {
            sb_append(sb, ", ");
        }
        sb_append_repr_value(sb, &values[i]);
    }
    if (count == 1) {
        sb_append_char(sb, ',');
    }
    sb_append_char(sb, ')');
}

double rng_decision_random(bool has_run_seed, int64_t run_seed, int day, const ReprValue *context,
                            size_t context_count) {
    StrBuf sb = {0};
    /* repr((seed_or_0, day, context)) -- a plain 3-tuple, never length 1,
     * so no trailing-comma case to handle here. */
    sb_append_char(&sb, '(');
    sb_append_long(&sb, has_run_seed ? (long)run_seed : 0L);
    sb_append(&sb, ", ");
    sb_append_long(&sb, (long)day);
    sb_append(&sb, ", ");
    sb_append_context_tuple_repr(&sb, context, context_count);
    sb_append_char(&sb, ')');

    uint8_t digest[8];
    blake2b_hash(sb.data, sb.len, digest, sizeof(digest));
    free(sb.data);

    uint64_t value = 0;
    for (int i = 0; i < 8; i++) {
        value = (value << 8) | digest[i];
    }
    return (double)value / 18446744073709551616.0; /* 2**64 */
}
