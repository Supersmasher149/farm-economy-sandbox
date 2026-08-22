/* `farm-c golden` -- the C port's committed replay baseline.
 *
 * The C analogue of ../.claude/skills/replay-guard for the Python
 * `simulation/` package: every registered strategy is run against a fixed
 * seed set and the result is diffed against a baseline committed to the
 * repo. Until this existed the port had no whole-trajectory regression gate
 * that ran without Python -- `c-parity` needed a working interpreter and the
 * live Python tree, so nothing guarded the C on a machine or a CI job that
 * had neither.
 *
 * The recorded value per run is a chained per-day digest (trajectory.h), not
 * a final tally, so a divergence that cancels out before the last day still
 * fails. The seed set is deliberately the same one replay-guard uses, so a
 * failure can be compared against the Python baseline combo-for-combo.
 */
#ifndef FARM_GOLDEN_H
#define FARM_GOLDEN_H

/* Dispatches `capture` / `check` / `trace` / `payload`; argv is the full
 * process argv with argv[1] == "golden". Returns a process exit code:
 * 0 pass, 1 divergence, 2 usage/IO error. */
int golden_main(int argc, char **argv);

#endif /* FARM_GOLDEN_H */
