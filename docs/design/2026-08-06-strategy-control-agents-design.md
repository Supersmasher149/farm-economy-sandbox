# Strategy Control Agents Design

## Goal

Make `NeglectfulGrower`, `NoUpgradePlayer`, and `FertilizerMaximalist` isolate
one advertised behavior each. Their results must be comparable with
`ProfitOptimizer` without unrelated policy differences affecting the outcome.

## Architecture

The three control agents inherit from `ProfitOptimizer`, which is the shared
implementation of the optimizer policy:

- `NeglectfulGrower` changes only `watering_diligence` to `0.15`.
- `NoUpgradePlayer` changes only `should_buy_upgrade()` to always return
  `False`.
- `FertilizerMaximalist` changes only fertilizer decisions. It retains its
  current behavior of buying fertilizer for an affordable planting and
  fertilizing every planted crop.

The controls will not duplicate crop selection, reserve handling, contracts,
processing, sales, upgrade policy, or endgame filtering. Existing class names,
agent registry entries, and descriptions remain unchanged.

## Verification

Add differential tests at the agent decision-hook boundary. For equivalent
player and world inputs, each control agent must match a fresh
`ProfitOptimizer` for crop choice, contract selection, deliveries, processing,
sales, and all non-advertised decisions. Tests will explicitly assert the
intentional difference for watering, upgrades, or fertilizer behavior.

Run the complete `pytest` suite after the focused tests pass.
