# Fertilizer Purchase Atomicity Design

## Goal

Prevent `_plant_open_slots` from spending money on fertilizer when the same
planting cannot also afford its seed. A failed fertilizer-backed planting must
not leave an unused fertilizer or charge fertilizer-only cash.

## Behavior

When an agent requests fertilizer for a crop and the player has no fertilizer
in inventory, the engine will check the combined seed and fertilizer cost
before purchasing either input. If the combined cost is unaffordable, the
engine skips fertilizer and continues with an unfertilized planting. If it is
affordable, the existing fertilizer purchase and planting flow is preserved.

Existing fertilizer inventory remains usable without an additional cash check;
the seed affordability check already happens before this decision.

## Verification

Add a regression test using a fertilizer-requesting agent with $10, an $8
fertilizer, and a $5 seed. The test will assert that the crop is planted
unfertilized, no fertilizer is purchased, and the player's cash is reduced
only by the seed cost. Existing action and full engine tests must continue to
pass.
