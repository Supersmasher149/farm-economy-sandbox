# Statistical Analysis Framework: Validation Status

**Date:** 2026-08-22
**Covers:** `docs/design/2026-08-21-statistical-analysis-framework-plan.md`
**Status:** Phases 1-4 and 6 delivered. Phase 5 deliberately not implemented.
Phase 7 partially delivered, and the unmet part is unmet for a structural
reason recorded below rather than for lack of time.

This document exists because the plan's Phase 7 asks for three things —
calibrate the uncertainty distributions from evidence, compare behaviour
against real-world or expert ranges, and document model limitations and
validation status — and only the third is honestly achievable for this
simulator. Saying which is which is the deliverable.

---

## 1. Delivery status

| Plan phase | Status | Where |
| --- | --- | --- |
| 1. Fixed-sample inference | Delivered | `metrics/estimands.py`, `metrics/inference.py` |
| 2. Exact distributions | Delivered | `metrics/distributions.py` |
| 3. Convergence and adaptive sampling | Delivered | `runner/adaptive.py`, `convergence.json` |
| 4. Strategy comparisons | Delivered | `metrics/comparisons.py`, `runner/sampling_plan.py` |
| 5. Strong common random numbers | **Not implemented — see §5** | — |
| 6. Epistemic uncertainty | Delivered | `experiments/uncertainty.py`, `experiments/sensitivity.py` |
| 7. Domain validation | Partial — §3 and §4 done, §6 unmet | this document |

Section 7 of the plan is a six-stage staircase toward common random numbers.
Stages 1-3 are delivered (`shared-initial-seed-v1`, one seed per replicate
shared across strategies, and the measurement of what that actually buys).
Stages 4-6 are the ones not implemented.

---

## 2. Method validation: do the intervals cover?

`tests/test_method_coverage.py` draws from distributions whose truth is known
and counts how often each interval contains it. Every stream is a fixed seed,
so these numbers are reproducible, not indicative. Nominal level is 95%
throughout; 2000 replications gives a Monte Carlo standard error of about
0.49 percentage points.

### Means (Student-t), normal outcomes

| n | Coverage |
| --- | --- |
| 5 | 0.9485 |
| 25 | 0.9555 |
| 200 | 0.9460 |

Exact at every n, which is the whole reason Student-t is the default rather
than a normal interval. On the *same* n=5 samples the normal interval covers
**0.8915** against the t-interval's 0.9570 — a nominally 95% interval that is
wrong one time in nine. That is why `method="normal"` is opt-in.

### Means, skewed outcomes

Final money is not normal, so the more relevant case is lognormal data at
n=200 (roughly a real batch): coverage **0.9340**, a little below nominal.
The t-interval's normality assumption is about the sampling distribution of
the *mean*, which the CLT supplies at that n; it does not hold at n=5 on
skewed data, which is the reason adaptive sampling has a minimum run count
rather than starting to test at its first checkpoint.

### Proportions (bankruptcy probability), n=100

| True p | Wilson | Clopper-Pearson |
| --- | --- | --- |
| 0.02 | 0.9545 | 0.9885 |
| 0.15 | 0.9390 | 0.9635 |
| 0.50 | 0.9480 | 0.9600 |

Wilson tracks nominal, oscillating with the discreteness of the binomial;
Clopper-Pearson is at or above nominal everywhere, as an exact interval must
be, and pays for it in width. This is the evidence behind the default
(Wilson) and the audit option (Clopper-Pearson). A Wald interval was not
implemented, because at p=0.02 it produces a lower bound below zero.

### Quantiles (percentile bootstrap), lognormal, n=200

| Quantile | Coverage |
| --- | --- |
| p50 | 0.9567 |
| p90 | 0.9267 |

The bootstrap is the only interval in the layer with no closed form to check
against, so this is its only real validation. Under-coverage at p90 is
expected behaviour for a percentile bootstrap of an extreme quantile at
moderate n; it is recorded here rather than smoothed over, and it is the
reason quantile estimands are excluded from adaptive stopping rules
(`supports_adaptive=False` on `final_money_quantile`).

### Paired variance reduction

Synthetic bivariate normal pairs with a known population correlation: the
measured `variance_reduction` recovers rho to within 0.03 at rho = 0.0, 0.5
and 0.9, which is the correct behaviour for the definition
`1 - Var(A-B) / (Var A + Var B)` under equal variances. On correlated arms
the paired difference interval is under half the width of the independent
one; on uncorrelated arms it is within 3% of it. This is the synthetic
control for the farm measurement in §4.

---

## 3. Sensitivity validation: the Ishigami benchmark

`sobol_indices` is checked against the Ishigami function, whose Sobol indices
are analytic. At N=4096 base samples (20,480 configurations, N(k+2) with
k=3):

| Parameter | S1 measured | S1 analytic | ST measured | ST analytic |
| --- | --- | --- | --- | --- |
| x1 | +0.3395 | +0.3139 | +0.5766 | — |
| x2 | +0.4452 | +0.4424 | +0.4226 | — |
| x3 | +0.0366 | +0.0000 | +0.2485 | +0.2437 |

x3 is the discriminating case: it has *zero* first-order effect but a large
total effect, purely through its interaction with x1. An estimator wired up
with the A and B matrices swapped reproduces neither, which is exactly the
bug this benchmark caught during implementation — the first version built
`AB_i` as B-with-column-from-A and reported S1 = 0.746 for x3.

---

## 4. Common random numbers: what shared seeds actually bought

Plan Section 7 stage 3 asks for the paired covariance and actual variance
reduction to be measured. Measured on the real economy: 400 replicates,
`--seed 20260822`, `--sampling-plan paired`, all ten pairs against
`profit_optimizer`, estimand `expected_final_money`.

| Pair (vs `profit_optimizer`) | Correlation | Variance reduction |
| --- | --- | --- |
| `progression_player` | +0.198 | +19.8% |
| `no_upgrade_player` | +0.232 | +17.3% |
| `diversifier` | +0.081 | +8.1% |
| `fertilizer_maximalist` | +0.057 | +4.7% |
| `upgrade_rusher` | +0.027 | +1.0% |
| `fast_seller` | +0.066 | +0.1% |
| `risk_averse_grower` | +0.030 | +0.0% |
| `reckless_spender` | +0.085 | +0.0% |
| `random_agent` | -0.073 | -0.0% |
| `neglectful_grower` | -0.003 | -0.2% |

Median correlation 0.069, median variance reduction 0.5%.

The consequence, measured directly as the ratio of paired to independent
difference-interval half-widths on the same seed and run count:

| Pair | Paired / independent half-width |
| --- | --- |
| `progression_player` | 0.894 |
| `no_upgrade_player` | 0.915 |
| `diversifier` | 0.952 |
| `fertilizer_maximalist` | 0.992 |
| the remaining six | 1.003 - 1.013 |

Median ratio **1.006**. For most pairs the paired plan produces a *slightly
wider* interval than the independent one, because it pays a degree of freedom
for a correlation that is not there.

**This is the expected result, not a defect.** The plan says so up front:
sharing an initial seed is weak pairing, because the two strategies' agents
make different decisions, consume different numbers of RNG draws, and
desynchronise the stream within the first simulated days. The pairs that do
benefit are exactly the ones whose agents behave most like the baseline
(`progression_player` and `no_upgrade_player` are `ProfitOptimizer`
subclasses that override little). The value of stages 1-3 is therefore this
measurement itself: it establishes that real variance reduction on this model
requires stages 4-6, not that pairing is free.

**Practical guidance:** leave the default (`legacy-mt19937-v1`, independent
comparisons) unless comparing two closely related agents. `--sampling-plan
paired` is not a free precision win here, and `comparisons.json` reports the
measured correlation on every pair so this is checkable per batch rather than
assumed.

---

## 5. Deliberately not implemented: Section 7 stages 4-6

Stages 4-6 — separate environment and policy RNG roots, per-domain streams
(weather / market / harvest / contracts / policy), and event-addressed random
values keyed by `(replicate, day, plot_id, ...)` — are **not implemented**,
and this is a decision rather than an omission.

**Why.** All three change how `simulation/random_events.py` draws. The
repository's two hard invariants are that a recorded seed reproduces its run
day for day (`.claude/skills/replay-guard`, 44 committed strategy/seed
combinations) and that `farm-c` reproduces Python bit-for-bit
(`.claude/skills/c-parity`, verified to 2200 runs). Reordering or re-rooting
the draw stream invalidates both: every committed baseline becomes a false
failure, and the C port — which mirrors the Python draw order
function-for-function — has to be rewritten in lockstep to match a stream
that no longer has a single sequence.

The plan anticipates this ("Do not modify the current `RandomEvents` stream.
Doing so would invalidate replay baselines and C parity") and its own
acceptance criteria require a *separate* replay fixture suite for the new RNG
semantics. That is a second, parallel determinism contract spanning both
languages — a project of its own scale, not a step inside this one.

**What it would take**, if it is wanted later:

1. A second `RandomEvents` implementation behind an explicit opt-in, with the
   legacy one untouched and still the default.
2. Canonical key serialisation with no use of Python `hash()` or `repr()`
   (the plan's own criterion; `derive_analysis_seed`'s blake2b approach is
   the right shape, and is already in the tree as a precedent).
3. A parallel golden-baseline suite for the new semantics, plus a farm-c port
   of the same addressing, plus a parity harness covering it.
4. Re-measuring §4 under the new streams — the whole point being that the
   correlations there should stop being ~0.

Until that exists, the honest characterisation of the paired plan is the one
in §4: weak pairing, measured, useful for similar agents only. It is labelled
that way in `describe()` (`pairing_strength: "weak"`), in the README, and in
`CLAUDE.md`, so nobody has to rediscover it.

---

## 6. Domain validation status: **not calibrated**

Phase 7's first two items are *not met*, and cannot be met in the way the
plan phrases them.

**The uncertainty distributions are placeholders.**
`experiments/specs/example-uncertainty.json` declares four parameters: a
correlated pair of crop loss chances (`quickweed`, `greenleaf`), a base
price, and the opening balance. Their `provenance` fields say what they are —
three read `placeholder`, and the fourth is labelled "design question, not
measurement: how much does the opening balance matter?". That is accurate:
the ranges were chosen to exercise the machinery (correlation groups, integer
parameters, constrained supports), not derived from anything. Nothing in the
repository licenses a claim about the *right* distribution for a crop loss
chance.

**There is no external ground truth to calibrate against.** This simulator is
not a model of a real agricultural economy. It is a designed game economy
whose config *is* the specification — `config/*.json` does not approximate a
farm, it defines one. There is no measurement of a real farm that would tell
you whether `quickweed.loss_chance` should be 0.19, and no expert range for a
fictional crop. The plan's "compare expected behavior with real-world or
expert ranges" presumes an external referent that does not exist here.

**What replaces it.** The referent that *does* exist is the design intent
recorded in the agent roster: each agent's docstring states the balance
question it exists to answer, and `../CLAUDE.md` makes it explicit that an
agent contradicting its own docstring is a bug rather than a modelling
choice. That is the calibration target for this project, it is already
enforced by `metrics/warnings.py` and the balance-testing workflow, and the
statistical layer's contribution to it is making a warning's underlying
movement distinguishable from sampling noise (README step 5).

**If real calibration is ever wanted**, the honest path is: pick a specific
external claim ("a mid-game player should go bankrupt roughly one run in
twenty"), express it as a target on a named estimand, and use
`experiments/uncertainty.py` in the inverse direction — find the config
region whose predicted bankruptcy interval contains the target. The machinery
supports it; the evidence to point it at does not currently exist.

---

## 7. Model limitations that bound every number this layer reports

Documented here because a confidence interval says nothing about them, and
reporting one alongside them without comment would imply more than is true.

1. **The intervals quantify sampling error only.** They answer "how much
   would this number move if I ran different seeds?" They do not cover
   config error, agent-design error, or engine bugs. A perfectly tight
   interval around a wrong quantity is still wrong.
2. **The estimand is the run, not the player.** Unit of analysis is one
   simulation run to completion (`UNIT_OF_ANALYSIS = "simulation_run"`).
   Nothing here estimates within-run dynamics; `convergence.json` is about
   accumulating runs, not about days.
3. **Cohort estimands are conditional and say so.** `expected_final_money_
   survivors` conditions on not going bankrupt, and
   `conditional_bankruptcy_day` conditions on going bankrupt — the latter is
   explicitly *not* a survival estimate, because runs that never fail are not
   observations of a large failure day. `distributions.survival_curve` is the
   censoring-aware view; the two disagree by construction, and
   `tests/test_distributions.py` pins that they do.
4. **Agents are probes, not optimal play.** Every estimate is conditional on
   the roster. "Expected final money" means "expected under this agent",
   never "achievable in this economy".
5. **Adaptive stopping is sequentially valid only for its declared rules.**
   Alpha spending covers the predeclared checkpoint schedule and the
   estimands named in the stopping rules. Reading any *other* number off a
   run that stopped adaptively, and treating its interval as exact, is
   inference after peeking. `analysis_metadata.json` records the stop reason
   and the rules for exactly this reason.
6. **Multiplicity families are per estimand.** Correction is applied across
   the pairs of one estimand, not across estimands. Scanning ten estimands
   for the one that turned significant is uncorrected, and no artifact can
   detect that you did it.
7. **Non-normality at small n.** See §2: the mean interval under-covers on
   skewed outcomes at small run counts. A 5-run or 20-run diagnostic batch
   gets an interval that is indicative, not exact.
8. **Sensitivity designs assume input independence unless told otherwise.**
   Only the Monte Carlo design honours declared correlation groups; OAT, LHS,
   Morris and Sobol *reject* a correlated spec rather than silently ignoring
   the correlation, and OAT additionally reports that it cannot see
   interactions at all.
9. **`RunResult` is the observation boundary.** An estimand can only be
   defined over fields that already survive into `RunResult`. Anything not
   recorded per run is not estimable without changing the simulator, which is
   outside this layer by design.

---

## 8. Reproducing every number in this document

```bash
# Sections 2 and 3: method validation and the Ishigami benchmark
python3 -m pytest tests/test_method_coverage.py tests/test_uncertainty.py -q

# Section 4: paired variance reduction on the real economy
python3 main.py batch --runs 400 --seed 20260822 \
    --sampling-plan paired --baseline profit_optimizer --no-charts
#   then read variance_reduction / correlation from reports/comparisons.json;
#   re-run without --sampling-plan for the independent half-widths.

# Section 6: the placeholder spec, and what it does say
python3 main.py uncertainty --spec experiments/specs/example-uncertainty.json \
    --method oat --replicates 50 --seed 20260822

# The invariants Section 5 protects
python3 .claude/skills/replay-guard/scripts/golden_replay.py check
python3 .claude/skills/c-parity/scripts/c_parity.py check --runs 20 --seed 4242
```
