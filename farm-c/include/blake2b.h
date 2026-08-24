/* Minimal BLAKE2b (RFC 7693), vendored for rng_hash.c's port of
 * PlayerState.decision_random (simulation/state.py:183-187), which hashes
 * with Python's `hashlib.blake2b(data, digest_size=8)` -- unkeyed, no
 * salt/personalization, sequential (non-tree) mode. This implementation
 * only supports that mode: no key, no salt, no tree parameters. Based on
 * the public-domain reference algorithm in RFC 7693 Section 3-4.
 */
#ifndef FARM_BLAKE2B_H
#define FARM_BLAKE2B_H

#include <stddef.h>
#include <stdint.h>

/* Unkeyed BLAKE2b over `input`/`input_len`, writing `out_len` bytes
 * (1..64) of digest to `out`. Matches
 * `hashlib.blake2b(input, digest_size=out_len).digest()`. */
void blake2b_hash(const void *input, size_t input_len, uint8_t *out, size_t out_len);

#endif /* FARM_BLAKE2B_H */
