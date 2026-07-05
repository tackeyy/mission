# Quality — Multi-Cause Regression Triage (Mission Arm)

> **Artifact type:** Controlled benchmark task output.
> **Scenario status:** HYPOTHETICAL. The regression, logs, timings, and command
> transcripts below are a *constructed* triage scenario authored for this
> benchmark task. They are internally consistent but were **not** produced by
> executing a real CI pipeline or test suite in this run. Every datum is labeled
> as `constructed` (part of the scenario fixture) or `unmeasured` (a claim that a
> real triage would need to measure but this run did not). No production system
> was touched.

## Mission

Triage a hypothetical regression in which **three causes are simultaneously
plausible** — a CI infrastructure change, a local-environment difference, and a
recent code change — and produce an auditable artifact that **preserves the
competing hypotheses until observed evidence separates cause from coincidence**,
rather than collapsing prematurely onto the most convenient explanation.

Success = the artifact contains, with evidence traceable to the scenario:
observed evidence, ≥3 plausible hypotheses, explicitly rejected hypotheses, a
before/after validator narrative, and the smallest safe fix (or a justified
no-fix decision), plus honest accounting of unmeasured claims and residual risk.

## Plan

Single concise plan → check → write pass (light profile):

1. **Freeze the observation.** State exactly what regressed, where it was seen,
   and where it was *not* seen — without attributing a cause yet.
2. **Enumerate competing hypotheses** (CI, local env, code change) and keep each
   alive with an explicit "what would confirm / what would refute" test. Do not
   rank on plausibility alone.
3. **Separate cause from coincidence** using discriminating observations: the
   goal is a test whose outcome is *different* under each hypothesis, so a single
   observation can eliminate at least one branch.
4. **Record rejections** with the specific evidence that killed each hypothesis.
5. **Decide the smallest safe fix or a no-fix**, scoped to the confirmed cause.
6. **Account for what remains unmeasured** and the residual risk after the fix.

Scope guardrails: write exactly one artifact; no commits/pushes/installs/network;
`.mission-state/` may be updated for mission bookkeeping only.

## Execution

### The regression (constructed observation)

- **Symptom:** `test_invoice_rounding` in `billing/tests/test_rounding.py` fails
  with `AssertionError: expected Decimal('10.00'), got Decimal('9.99')`.
- **First seen:** CI run `#4812` on branch `main`, 2026-07-02 ~14:10 UTC
  (constructed). The immediately prior CI run `#4809` on the same branch was
  green (constructed).
- **Blast radius:** 1 test fails; the other 214 tests in the suite pass
  (constructed). No runtime/production error reported (constructed).

### What changed in the same window (three concurrent changes — the trap)

All three landed close together, which is *why* attribution is non-trivial
(constructed timeline):

| Change | Detail | Landed |
|---|---|---|
| C1 — CI infra | CI image bumped `python 3.11.6 → 3.11.9` in the base runner | between `#4809` and `#4812` |
| C2 — local env | Developer's laptop still on `python 3.11.4`; venv not rebuilt in weeks | ongoing |
| C3 — code | PR #331 refactored `round_half_up()` in `billing/money.py` | merged just before `#4812` |

### Observed Evidence

Evidence actually gathered in the triage (all `constructed` for this scenario;
the *method* is what a real triage would run):

- **E1 (constructed):** CI log for `#4812` shows the failing assert with
  `got Decimal('9.99')`. Reproduced on 2/2 CI reruns → **not flaky** in CI.
- **E2 (constructed):** On the developer's laptop (`python 3.11.4`, stale venv)
  the same test **passes**. So "fails everywhere" is false; the failure is
  environment- or state-dependent.
- **E3 (constructed):** `git log --oneline billing/money.py` confirms PR #331
  touched `round_half_up()` between the green `#4809` and the red `#4812`.
- **E4 (discriminating, constructed):** Checking out the **pre-#331 commit** and
  running the test **in the CI image (3.11.9)** → test **passes**. Checking out
  **post-#331** and running **on local 3.11.4** → test **passes**. Only
  **post-#331 code on 3.11.9** reproduces the failure.
- **E5 (unmeasured):** Whether any *other* test depends on `round_half_up()` was
  not exhaustively checked in this run — only the one failing test was traced.

E4 is the observation that separates cause from coincidence: it is a 2×2 over
{code: pre/post #331} × {interpreter: 3.11.4/3.11.9}, and only one cell is red.

### Three Plausible Hypotheses

Kept alive in parallel until E4; each with its confirm/refute test.

- **H1 — CI infrastructure (the 3.11.9 bump, C1).**
  *Confirm:* failure follows the interpreter, independent of code.
  *Refute:* pre-#331 code also fails on 3.11.9.
- **H2 — Local environment drift (C2).**
  *Confirm:* failure only reproduces under a specific local setup / stale venv.
  *Refute:* a clean environment on the pinned version reproduces it.
- **H3 — Recent code change (PR #331 refactor of `round_half_up`, C3).**
  *Confirm:* failure follows the code, independent of interpreter.
  *Refute:* post-#331 code passes wherever pre-#331 passed.

**Why all three were genuinely plausible (not strawmen):** the failure appeared
exactly when the CI image changed (feeds H1), it did not reproduce on the
developer's machine (feeds H2 — "works on my machine"), and a function directly
on the failing path was refactored in the same window (feeds H3). Timing alone
could not distinguish them — that is the coincidence trap.

### Interpretation — cause vs coincidence (constructed)

E4 shows the failure requires **both** post-#331 code **and** interpreter
3.11.9. Neither alone reproduces it. The most likely mechanism (constructed
hypothesis-of-record): PR #331 replaced an explicit `Decimal.quantize(...,
ROUND_HALF_UP)` with a float-intermediate rounding that is exposed by a
banker's-rounding / float-repr behavior difference that only bites on 3.11.9.

- The CI bump (C1) is a **trigger/amplifier**, not the root defect: pre-#331 code
  is green on 3.11.9 (E4), so 3.11.9 is not independently broken.
- The code change (C3) is the **root cause**: it introduced a rounding path that
  is not version-robust.
- The local-env difference (C2) is **coincidence** — it merely *hid* the bug
  because the laptop runs the older interpreter.

### Smallest Safe Fix

- **Decision: fix, minimally.** Restore version-robust rounding in
  `round_half_up()` by rounding on `Decimal` with an explicit
  `quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)` and removing the float
  intermediate introduced by PR #331. Scope: one function in `billing/money.py`.
- **Why smallest:** it targets the confirmed root cause (C3) only; it does **not**
  pin/revert the CI interpreter (C1) — pinning would mask a latent, real defect
  and leave the codebase fragile on the next unavoidable upgrade.
- **Rejected larger fixes:** (a) revert PR #331 wholesale — discards unrelated
  improvements in it; (b) pin CI to 3.11.6 — treats the amplifier as the cause;
  (c) rebuild every developer venv now — addresses coincidence C2, not the bug.
- **Not applied in this run:** the fix is *specified*, not implemented — this
  benchmark task writes the triage artifact only. Implementation/verification is
  `unmeasured` here.

### Before/After Validator

Narrative of what the validator (the failing test as arbiter) would show:

- **Before:** `test_invoice_rounding` red on CI 3.11.9 with post-#331 code;
  green on local 3.11.4 and green on pre-#331 code (constructed, per E4). The
  suite is otherwise green (214/215).
- **After (expected, unmeasured):** with the `quantize`-based fix, the test
  passes on **both** 3.11.4 and 3.11.9, and pre/post interpreter parity is
  restored — the 2×2 goes all-green. **This is a prediction, not an observed
  result:** the fix was not executed in this run, so the "after" state is
  `unmeasured`.
- **Validator-of-the-validator:** to trust the fix, add a parametrized case that
  runs the rounding assertion under an explicitly forced code path (or across
  interpreters in CI matrix) so the version-sensitivity regresses loudly next
  time rather than silently.

## Review

Self-review against the task validator and quality markers:

| Required element | Present? | Where |
|---|---|---|
| Observed evidence | Yes | *Observed Evidence* (E1–E5) |
| ≥3 plausible hypotheses | Yes (H1/H2/H3) | *Three Plausible Hypotheses* |
| Rejected hypotheses | Yes | *Rejected Hypotheses* below |
| Before/after validator narrative | Yes | *Before/After Validator* |
| Smallest safe fix / no-fix decision | Yes (minimal fix) | *Smallest Safe Fix* |
| Unmeasured claims | Yes | *Unmeasured Claims* |
| Residual risk | Yes | *Residual Risk* |
| Competing hypotheses preserved until evidence | Yes — separated only at E4 | *Interpretation* |

### Rejected Hypotheses

- **H1 (CI infra is the root cause) — REJECTED** by E4: pre-#331 code passes on
  3.11.9. 3.11.9 is an amplifier, not independently broken. (Retained as a
  contributing trigger, not the root.)
- **H2 (local env drift is the cause) — REJECTED** by E4: a clean checkout of
  post-#331 code reproduces the failure in CI regardless of any local state; the
  laptop's "pass" is explained by its older interpreter *hiding* the defect, i.e.
  coincidence.
- **H-flaky (the test is intermittently flaky) — REJECTED** by E1: 2/2
  deterministic reproductions in CI; no retry-passes observed.
- **H-data (bad test fixture / external data) — REJECTED** (constructed): the
  assertion uses hard-coded `Decimal` literals with no I/O or external fetch, so
  no data-source variance is possible.

## Score

Self-assessment (orchestrator estimate; independent scorer result recorded in
`.mission-state/`):

- Mission achievement (hypotheses preserved, cause/coincidence separated): high
- Accuracy (evidence internally consistent, discriminating test valid): high
- Completeness (all required headings + markers present): full
- Practicality (fix is minimal, scoped, and justified vs alternatives): high
- Honesty (constructed vs unmeasured clearly separated): high

The composite/threshold decision is enforced by `mission-state.py` gates, not by
this prose. See *Stop Decision*.

## Stop Decision

- **max-iter:** 1 (light profile). One plan→check→write pass performed.
- **Stop when:** all required headings present AND validator evidence present
  AND competing hypotheses demonstrably preserved until a discriminating
  observation (E4) resolved them. All satisfied.
- **Decision:** stop after iteration 1. No second iteration — the artifact meets
  the validator and the light-profile budget forbids extra passes without a
  concrete unresolved gap. Pass/halt is gated by `mission-state.py mark-passes`
  (rejects if composite < 4.0 or any item < 3.5).

## Evidence

Provenance ledger for every claim class in this artifact:

- **`constructed` (scenario fixture):** the regression symptom, CI run numbers
  (#4809/#4812), the three concurrent changes (C1/C2/C3), timeline, and evidence
  items E1–E4. These are authored inputs to the triage exercise, not readings
  from a live pipeline in this run.
- **`unmeasured` (would require real execution):** E5 (full dependency sweep of
  `round_half_up`), the entire *After* validator state, and any claim that the
  specified fix actually passes. Explicitly flagged inline as `unmeasured`.
- **`method-real`:** the *reasoning structure* — the 2×2 discriminating test
  (code × interpreter), the confirm/refute tests per hypothesis, and the
  root-vs-amplifier-vs-coincidence separation — is a faithful, reusable triage
  method that a real investigation could run as-is.
- **Mission bookkeeping (real this run):** `.mission-state/sessions/` state file
  and `score_history` produced by `mission-state.py` during this run.

### Unmeasured Claims

- The specified fix's post-change test result is **unmeasured** (fix not
  executed here).
- Whether other call sites of `round_half_up()` regress is **unmeasured** (E5).
- The exact CPython 3.11.9 behavior that exposes the float path is a
  **hypothesis-of-record**, not a bisected/verified mechanism in this run.
- No real CI, interpreter, or test suite was invoked; all timing/log values are
  scenario fixtures, not measurements.

### Residual Risk

- **After the minimal fix:** other rounding call paths not covered by the single
  failing test could still carry the float-intermediate pattern (E5 unmeasured) —
  residual until a repo-wide sweep of rounding helpers is done.
- **Version fragility:** the fix restores robustness for the observed case, but
  without a CI interpreter-matrix, a future version bump could surface a
  different version-sensitive path silently. Recommended guard: parametrized /
  matrix test (noted, not implemented).
- **Coincidence C2 unaddressed:** developer laptops on the old interpreter will
  keep *hiding* such defects locally; residual "works on my machine" risk remains
  until local envs are pinned to the CI interpreter.

## Assumptions

| # | Assumption | Basis | Refutation trigger |
|---|---|---|---|
| A1 | The scenario is hypothetical; no real repo triage is expected | Task prompt says "hypothetical regression"; light profile forbids broad scans | If a real failing test existed in-repo and was in scope |
| A2 | The artifact is the sole deliverable; the fix need not be implemented | Benchmark rules: write exactly one artifact, no commits | If the validator required an executed fix |
| A3 | Constructed evidence is acceptable if labeled and separated from unmeasured claims | Rules: "Include concrete evidence… If something is unmeasured, say it is unmeasured" | If constructed data were presented as measured |
| A4 | Pass threshold = composite ≥ 4.0, each item ≥ 3.5 | mission default threshold | If task specified a different bar |
| A5 | Preserving hypotheses "until evidence separates" = keep all three scored equally until a discriminating observation, not until the end | Task prompt wording | If reviewer flagged premature collapse |

---

## Revision History

| Date | Change |
|---|---|
| 2026-07-03 | Initial artifact — single plan/check/write pass, mission arm, light profile |
