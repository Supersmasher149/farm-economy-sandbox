/* Minimal unkeyed BLAKE2b (RFC 7693). See include/blake2b.h. */
#include "blake2b.h"

#include <string.h>

static const uint64_t BLAKE2B_IV[8] = {
    0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL, 0xa54ff53a5f1d36f1ULL,
    0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL, 0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL,
};

static const uint8_t SIGMA[12][16] = {
    {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
    {14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3},
    {11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4},
    {7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8},
    {9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13},
    {2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9},
    {12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11},
    {13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10},
    {6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5},
    {10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0},
    {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15},
    {14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3},
};

typedef struct {
    uint64_t h[8];
    uint64_t t[2]; /* input bytes counter (128-bit, low/high) */
    uint64_t f[2]; /* finalization flags */
    uint8_t buf[128];
    size_t buflen;
    size_t outlen;
} blake2b_state;

static uint64_t load64_le(const uint8_t *src) {
    uint64_t value = 0;
    for (int i = 0; i < 8; i++) {
        value |= (uint64_t)src[i] << (8 * i);
    }
    return value;
}

static void store64_le(uint8_t *dst, uint64_t value) {
    for (int i = 0; i < 8; i++) {
        dst[i] = (uint8_t)(value >> (8 * i));
    }
}

static uint64_t rotr64(uint64_t x, unsigned n) {
    return (x >> n) | (x << (64 - n));
}

static void blake2b_compress(blake2b_state *S, const uint8_t block[128]) {
    uint64_t m[16];
    uint64_t v[16];
    for (int i = 0; i < 16; i++) {
        m[i] = load64_le(block + i * 8);
    }
    for (int i = 0; i < 8; i++) {
        v[i] = S->h[i];
    }
    v[8] = BLAKE2B_IV[0];
    v[9] = BLAKE2B_IV[1];
    v[10] = BLAKE2B_IV[2];
    v[11] = BLAKE2B_IV[3];
    v[12] = BLAKE2B_IV[4] ^ S->t[0];
    v[13] = BLAKE2B_IV[5] ^ S->t[1];
    v[14] = BLAKE2B_IV[6] ^ S->f[0];
    v[15] = BLAKE2B_IV[7] ^ S->f[1];

#define G(r, i, a, b, c, d)                                    \
    do {                                                       \
        a = a + b + m[SIGMA[r][2 * i + 0]];                    \
        d = rotr64(d ^ a, 32);                                 \
        c = c + d;                                             \
        b = rotr64(b ^ c, 24);                                 \
        a = a + b + m[SIGMA[r][2 * i + 1]];                    \
        d = rotr64(d ^ a, 16);                                 \
        c = c + d;                                             \
        b = rotr64(b ^ c, 63);                                 \
    } while (0)

    for (int r = 0; r < 12; r++) {
        G(r, 0, v[0], v[4], v[8], v[12]);
        G(r, 1, v[1], v[5], v[9], v[13]);
        G(r, 2, v[2], v[6], v[10], v[14]);
        G(r, 3, v[3], v[7], v[11], v[15]);
        G(r, 4, v[0], v[5], v[10], v[15]);
        G(r, 5, v[1], v[6], v[11], v[12]);
        G(r, 6, v[2], v[7], v[8], v[13]);
        G(r, 7, v[3], v[4], v[9], v[14]);
    }
#undef G

    for (int i = 0; i < 8; i++) {
        S->h[i] ^= v[i] ^ v[i + 8];
    }
}

static void blake2b_increment_counter(blake2b_state *S, uint64_t inc) {
    S->t[0] += inc;
    S->t[1] += (S->t[0] < inc) ? 1 : 0; /* carry */
}

static void blake2b_init(blake2b_state *S, size_t outlen) {
    memset(S, 0, sizeof(*S));
    memcpy(S->h, BLAKE2B_IV, sizeof(S->h));
    /* Simple-mode parameter block: digest_length=outlen, key_length=0,
     * fanout=1, depth=1, everything else (leaf_length, node_offset,
     * node_depth, inner_length, salt, personal) zero -- see RFC 7693
     * Section 2.5. Only h[0]'s low 4 bytes are non-zero, so this reduces to
     * a single XOR rather than building and XORing a full 64-byte block. */
    S->h[0] ^= 0x01010000ULL ^ (uint64_t)outlen;
    S->outlen = outlen;
}

static void blake2b_update(blake2b_state *S, const uint8_t *input, size_t input_len) {
    while (input_len > 0) {
        size_t left = S->buflen;
        size_t fill = 128 - left;
        if (input_len > fill) {
            memcpy(S->buf + left, input, fill);
            blake2b_increment_counter(S, 128);
            blake2b_compress(S, S->buf);
            S->buflen = 0;
            input += fill;
            input_len -= fill;
        } else {
            memcpy(S->buf + left, input, input_len);
            S->buflen += input_len;
            input += input_len;
            input_len = 0;
        }
    }
}

static void blake2b_final(blake2b_state *S, uint8_t *out) {
    blake2b_increment_counter(S, (uint64_t)S->buflen);
    S->f[0] = ~0ULL; /* last-block flag (sequential, non-last-node mode) */
    memset(S->buf + S->buflen, 0, 128 - S->buflen);
    blake2b_compress(S, S->buf);

    uint8_t full[64];
    for (int i = 0; i < 8; i++) {
        store64_le(full + i * 8, S->h[i]);
    }
    memcpy(out, full, S->outlen);
}

void blake2b_hash(const void *input, size_t input_len, uint8_t *out, size_t out_len) {
    blake2b_state S;
    blake2b_init(&S, out_len);
    blake2b_update(&S, (const uint8_t *)input, input_len);
    blake2b_final(&S, out);
}
