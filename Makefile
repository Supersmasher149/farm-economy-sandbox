# Makefile -- wraps this repo's test/lint/replay-guard/batch-sim/chart
# commands (documented in CLAUDE.md and .github/workflows/ci.yml) behind a
# handful of stable targets, so a long run-inspect-rerun loop is one command
# instead of several remembered by hand.
#
# Every target fails loudly: .ONESHELL + `set -euo pipefail` means a failing
# step aborts the recipe immediately (no silent continuation into a later
# "success" echo), and each step is announced before it runs so the failing
# one is obvious in scrollback.
#
# sim / charts / balance never touch the real reports/ tree. main.py has no
# --output-dir flag (REPORTS_DIR is a checkout-relative module constant, on
# purpose -- see main.py's comment), so tools/scratch_run.py imports main.py
# and overrides that one attribute before dispatching, same as the
# monkeypatch tests/test_reporting_issues.py already uses. Output lands in
# reports/_scratch/ and reports/latest, reports/runs/, etc. are left alone.
# See CLAUDE.md's "Simulation Output Safety".

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := verify
.PHONY: verify test-fast sim charts balance clean-scratch

PYTHON ?= python3
SCRATCH_DIR := reports/_scratch
SCRATCH_RUN := $(PYTHON) tools/scratch_run.py

# Overridable on the command line, e.g. `make sim SIM_RUNS=1000 SIM_SEED=42`.
SIM_RUNS ?= 100
SIM_SEED ?=
CHART_RUNS ?= 100
CHART_SEED ?=
BALANCE_RUNS ?= 1000
BALANCE_SEED ?= 42

define banner
echo ""
echo "==> $(1)"
endef

# Full pre-commit gate: tests + lint + format + determinism replay-guard.
# Mirrors CLAUDE.md's "Verification Before Commit" and the `test` job in
# .github/workflows/ci.yml.
verify:
	$(call banner,ruff check)
	ruff check
	$(call banner,ruff format --check)
	ruff format --check
	$(call banner,pytest (full suite))
	$(PYTHON) -m pytest
	$(call banner,replay-guard (determinism check))
	$(PYTHON) .claude/skills/replay-guard/scripts/golden_replay.py check
	$(call banner,verify: all green)

# Quick feedback loop while iterating: stop at the first failure, skip
# lint/replay-guard. Not a substitute for `make verify` before a commit.
test-fast:
	$(call banner,pytest -x (fail fast))
	$(PYTHON) -m pytest -x -q

# Ad hoc batch run for smoke-testing, e.g. `make sim SIM_RUNS=500 SIM_SEED=42`.
# No charts (fast, no matplotlib dependency). Output: reports/_scratch/.
sim:
	$(call banner,batch sim ($(SIM_RUNS) runs) -> $(SCRATCH_DIR)/)
	mkdir -p $(SCRATCH_DIR)
	$(SCRATCH_RUN) batch --runs $(SIM_RUNS) $(if $(SIM_SEED),--seed $(SIM_SEED),) --no-charts --no-progress
	echo "scratch report: $(SCRATCH_DIR)/summary_report.md"

# Same as `sim` but renders the dashboard too (needs matplotlib -- see
# requirements-viz.txt). Fails loudly up front if matplotlib isn't
# installed rather than silently producing a placeholder page.
charts:
	$(call banner,checking matplotlib is installed)
	$(PYTHON) -c "import matplotlib" || { echo "matplotlib not installed -- run: pip install -r requirements-viz.txt" >&2; exit 1; }
	$(call banner,batch sim + charts ($(CHART_RUNS) runs) -> $(SCRATCH_DIR)/)
	mkdir -p $(SCRATCH_DIR)
	$(SCRATCH_RUN) batch --runs $(CHART_RUNS) $(if $(CHART_SEED),--seed $(CHART_SEED),) --no-progress
	echo "scratch dashboard: $(SCRATCH_DIR)/dashboard.html"

# Balance-testing workflow (CLAUDE.md's "Balance-testing workflow" / the
# balance-check skill), redirected to scratch: fixed-seed batch run, then
# the Warnings section printed straight to the terminal. This is for a
# quick, non-destructive look -- to publish a real run for `main.py view`
# or `--diff` to reference, run `python3 main.py batch --seed <seed>`
# directly per CLAUDE.md, outside this target.
balance:
	$(call banner,balance batch ($(BALANCE_RUNS) runs, seed $(BALANCE_SEED)) -> $(SCRATCH_DIR)/)
	mkdir -p $(SCRATCH_DIR)
	$(SCRATCH_RUN) batch --runs $(BALANCE_RUNS) --seed $(BALANCE_SEED) --no-charts --no-progress
	$(call banner,warnings)
	$(PYTHON) .claude/skills/balance-check/scripts/report_diff.py warnings $(SCRATCH_DIR)/summary_report.md

# Scratch output accumulates retained runs same as reports/ does; nothing
# else depends on it, so it's always safe to clear.
clean-scratch:
	$(call banner,removing $(SCRATCH_DIR)/)
	rm -rf $(SCRATCH_DIR)
