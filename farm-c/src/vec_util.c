#include "vec_util.h"

#include <stdlib.h>

bool vec_grow(void **data, size_t *capacity, size_t count, size_t elem_size) {
    if (count < *capacity) {
        return true;
    }
    size_t new_capacity = *capacity == 0 ? 4 : *capacity * 2;
    void *grown = realloc(*data, new_capacity * elem_size);
    if (grown == NULL) {
        return false;
    }
    *data = grown;
    *capacity = new_capacity;
    return true;
}

int int_floor_div(int a, int b) {
    int q = a / b;
    int r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        q -= 1;
    }
    return q;
}

void *scratch_buffer_reserve(ScratchBuffer *scratch, size_t bytes) {
    if (bytes == 0) {
        return NULL;
    }
    if (bytes <= scratch->capacity_bytes) {
        return scratch->data;
    }
    /* Geometric growth, so repeated reserves stay amortized O(1); a request
     * larger than the doubled size is satisfied exactly rather than rounded
     * up further. */
    size_t new_capacity = scratch->capacity_bytes == 0 ? 256 : scratch->capacity_bytes * 2;
    if (new_capacity < bytes) {
        new_capacity = bytes;
    }
    void *grown = realloc(scratch->data, new_capacity);
    if (grown == NULL) {
        return NULL;
    }
    scratch->data = grown;
    scratch->capacity_bytes = new_capacity;
    return scratch->data;
}

void scratch_buffer_free(ScratchBuffer *scratch) {
    free(scratch->data);
    scratch->data = NULL;
    scratch->capacity_bytes = 0;
}
