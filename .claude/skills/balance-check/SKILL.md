---
name: balance-check
description: Runs the farm-economy-sandbox balance-testing workflow documented in CLAUDE.md/README.md - batch run with a fixed seed, surface the ## Warnings section, help isolate a config/agent fix under the same seed, then do a final unseeded run. Use when asked to balance-test, check economy balance, chase a warning, or evaluate a config/agent change's effect on the economy.
---

# balance-check

Encodes the "Balance-testing workflow" from `CLAUDE.md` / `README.md` as a
repeatable checklist instead of re-deriving it each time. This skill only
runs the existing `main.py batch` CLI and reads its output — it never edits
`config/*.json` or agent code itself; config/agent edits stay a
human-in-the-loop decision, per CLAUDE.md's "agents decide, engine mutates"
philosophy.

## Workflow

1. **Pick a fixed seed.** Ask the user for one, or use a stable default
   (e.g. `42`) if they don't care — the point is reusing the *same* seed
   across steps 2 and 5 so any difference in warnings is attributable to the
   config/agent change, not run-to-run noise.

2. **Baseline batch run:**
   ```bash
   python3 main.py batch --runs 1000 --seed <seed>
   ```
   Pass through `--days`/`--start-money` if the user wants a specific
   diagnostic scenario instead of the configured defaults.

3. **Surface the warnings** (don't re-read the whole report by hand):
   ```bash
   python3 .claude/skills/balance-check/scripts/report_diff.py warnings reports/summary_report.md
   ```
   Save a copy of `reports/summary_report.md` (e.g. `cp` it to a scratch
   path) before making any change — step 6 needs both the before and after
   report to diff.

4. **Report warnings to the user verbatim.** If there are none, say so
   plainly and stop — don't invent a problem to fix.

5. **Before proposing a fix**, check CLAUDE.md's rule: if a warning traces
   back to an agent's decision logic contradicting that agent's own
   documented purpose (see its module docstring in `agents/*.py`), that's an
   agent bug — fix the agent. Otherwise it's a modeling/config question — the
   fix belongs in `config/*.json` (validated via
   `simulation/configuration.py`).

6. **After the user approves and you apply the edit**, re-run batch with the
   **same seed** and diff against the saved baseline report:
   ```bash
   python3 main.py batch --runs 1000 --seed <seed>
   python3 .claude/skills/balance-check/scripts/report_diff.py diff <old_report_copy> reports/summary_report.md
   ```
   This isolates the effect of just that one change.

7. **Final run without `--seed`** once satisfied, to confirm the fix holds
   under fresh randomized conditions — report this as the final result:
   ```bash
   python3 main.py batch --runs 1000
   ```

## Files

- `scripts/report_diff.py` — `warnings <report.md>` and
  `diff <old_report.md> <new_report.md>` subcommands.
