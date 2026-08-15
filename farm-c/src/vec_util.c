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
