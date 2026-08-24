/* Shared integer-ID typedefs, used instead of string lookups at runtime.
 * Mirrors docs/c-port-plan.md Section 1 ("Configuration Model").
 *
 * String IDs (the "fast_seller" / "purplehaze" spellings the JSON config and
 * reports use) only exist while loading configuration or printing reports --
 * everything under farm-c/src operates on these integer indexes.
 */
#ifndef FARM_TYPES_H
#define FARM_TYPES_H

#include <stdbool.h>
#include <stdint.h>

typedef uint32_t ItemId;
typedef uint32_t CropId; /* CropId and ItemId share the same index space:
                           * every crop is also an item (see config.h). */
typedef uint32_t UpgradeId;
typedef uint32_t RecipeId;
typedef uint32_t BuyerId;
typedef uint32_t ChannelId;
typedef uint32_t ContractId; /* stable identity for an offer/active contract;
                               * unlike vector positions, it survives
                               * compaction and is never reused while live. */

#define INVALID_ID UINT32_MAX
#define INVALID_DAY INT32_MIN /* "never" sentinel, e.g. no upgrade purchase yet */

static inline bool id_valid(uint32_t id) {
    return id != INVALID_ID;
}

/* simulation/state.py:4 QUALITY_ORDER -- rank order is load-bearing (used for
 * "at least this grade" comparisons throughout economy_rules/contracts), so
 * this enum's member order must never change independent of the Python one. */
typedef enum {
    QUALITY_REJECTED = 0,
    QUALITY_PROCESSING = 1,
    QUALITY_STANDARD = 2,
    QUALITY_PREMIUM = 3,
    QUALITY_COUNT = 4
} Quality;

#endif /* FARM_TYPES_H */
