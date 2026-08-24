/* Optional, purely additive bolt-on: pushes free-text summaries of a
 * `single` run or a `batch`'s per-strategy results to the Mem0 Platform
 * (https://mem0.ai) as "memories", so they're later searchable outside
 * farm-c. This module only ever sends text a caller already built from a
 * RunResult/BatchRunResult -- it never reads FarmState/config itself, never
 * runs on the engine's hot path unless a caller puts it there, and never
 * consumes simulation RNG. That mirrors the boundary ../CLAUDE.md draws
 * around the Python statistical layer: reporting reads the engine's output,
 * it never reaches into the engine.
 *
 * Compiled out of the binary entirely unless built with `make WITH_MEM0=1`
 * (see Makefile) -- the default build links no HTTP client and needs no
 * libcurl, matching how src/../simulation/_fastplotmodule.c and the Cython
 * build are optional accelerators that must never be silently required by
 * `make test`/CI. Without WITH_MEM0, every call below still links and
 * runs -- it just always fails with a message saying so, so callers never
 * need their own #ifdef.
 */
#ifndef FARM_MEM0_CLIENT_H
#define FARM_MEM0_CLIENT_H

#include <stdbool.h>
#include <stddef.h>

#define MEM0_ERROR_BUFFER_SIZE 512

/* True only when built with WITH_MEM0=1 *and* MEM0_API_KEY is set and
 * non-empty in the environment. Callers should gate on this before
 * building a summary string, so a run that never asked for --mem0 (or a
 * default build) never touches curl or the network. */
bool mem0_client_configured(void);

/* Adds one memory: `text` becomes a single user-role message, tagged with
 * `user_id` (required, non-NULL) and `agent_id` (optional, may be NULL).
 * Blocks for the duration of one HTTPS POST to
 * https://api.mem0.ai/v1/memories/ -- callers on a hot path (a large batch)
 * should call this a handful of times, e.g. once per strategy, never once
 * per run.
 *
 * Returns true on a 2xx response. On any failure -- not configured, no API
 * key, network error, non-2xx response -- writes a NUL-terminated message
 * into `error_out` (a caller-owned buffer of at least
 * MEM0_ERROR_BUFFER_SIZE bytes; may be NULL to discard it) and returns
 * false. Never aborts the process and never touches simulation state. */
bool mem0_add_memory(const char *text, const char *user_id, const char *agent_id,
                     char *error_out, size_t error_out_len);

#endif /* FARM_MEM0_CLIENT_H */
