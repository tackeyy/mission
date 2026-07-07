# Case Studies: What the Review Gate Actually Catches

Data snapshot: 2026-07-07.

## Provenance and Limitations

- Source: the maintainer's production `.mission-state` files across private
  local repositories. The state files themselves are **not published**;
  projects are anonymized below. Nothing here can be independently reproduced
  from this repository alone — treat it as disclosed operational evidence, not
  a benchmark.
- Scores are mission's own reviewer composites (1–5 scale, pass gate 4.0).
  They are produced inside the same system that is being evaluated. `push-score`
  rejects inflated self-reported scores, but this is still not independent
  blind judging.
- **No comparative claim** ("better than a plain single pass / than `/goal`")
  is supported by this page. For paired comparisons see
  [`benchmarks/mission-vs-goal/README.md`](../benchmarks/mission-vs-goal/README.md).

## Aggregate Picture

Across **451 scored production missions** (unique states with a score
history, duplicates removed):

| Measure | Value | Reading |
|---|---:|---|
| Passed the 4.0 gate at iteration 1 | 427 / 451 (95%) | For most work the loop is a pass-through: it adds review cost and changes nothing. |
| Scored below 4.0 at iteration 1 | 24 (5%) | The tail where the gate binds. These missions were forced to iterate instead of shipping. |
| Multi-iteration missions | 44 | Includes gate-forced reruns and findings-driven reruns above 4.0. |
| — composite improved across iterations | 27 | Measured improvement, e.g. 2.80 → 4.20, 3.10 → 4.60, 0.96 → 4.80. |
| — composite unchanged across iterations | 15 | Honest negative: repeat iterations that added cost without moving the score. |
| Halted pending human approval or input | 7 | Irreversible actions (production DB migrations, publishing a security audit, a production API cap change) blocked until explicit approval. |

The distribution matters more than the average: the review gate earns its
cost in a **minority tail** of missions, not by raising the mean. If your
tasks all look like the 95%, a single careful pass gives you the same
artifact faster.

## Selected Cases

Projects are anonymized: **A** = an operations bot, **B** = an
SNS-management SaaS, **C** = a small public web service, **D** = this
repository's own tooling. Findings below are condensed from the recorded
reviewer output of iteration 1 and the recorded diff of iteration 2+.

### Case 1 — Wrong root-cause analysis shipped-in-waiting (A: 2.80 → 4.04 → 4.20)

An error-regression investigation reported an error rate of 51.9%; recounting
during review showed 79.4% (mock entries had been included in the
denominator). The first pass also misread the existing code structure it
blamed (the guard it proposed already existed; the actual culprit was a
composition check that rejected compound commands without segmenting them)
and justified a fix with a sandbox property that does not hold. Iteration 2
corrected the rate, the mechanism, and the rationale; iteration 3 added an
implementation plan with tests. Without the gate, a root-cause report with
three factual errors becomes the basis for the fix.

### Case 2 — Green tests, broken layout (B: 3.10 → 4.60)

A UI change passed type checks and the full test suite on the first pass.
Review caught a server-rendered fixed width diverging from client toggle
state — a 156px visual gap on toggle — plus missing accessibility attributes
and two untested state paths. All were fixed in iteration 2. Static checks
alone would have shipped a visible layout bug.

### Case 3 — Design doc referencing a component that does not exist (B: 3.70 → 4.70)

A navigation-migration design assumed an icon component from a UI library
that has no such export, and stated several unverified external references as
fact. The first implementation step would have failed to compile. Iteration 2
replaced the component choice (verified by grep) and re-worded every
unverified assertion as verified-or-removed.

### Case 4 — Runtime UI bugs invisible to the toolchain (B: 3.60 → 4.40)

A sidebar implementation passed `tsc` and 1,200+ unit tests while shipping a
duplicated close button, a missing navigation link, an event-propagation bug
that broke the drawer, and a broken mobile toggle. All five were reviewer
findings, all fixed in iteration 2.

### Case 5 — Security-relevant gaps behind green tests (C: 3.40 → 4.65)

An auth/dashboard work plan carried an identifier regex that differed across
API, middleware, and database layers, and no expiry validation on login
links. Both are security-relevant and both survived the first pass's passing
test run. Iteration 2 fixed one High and thirteen Medium/Low findings;
the final pass verified expiry (401), duplicate (409), and invalid (400)
paths explicitly.

### Case 6 — The gate also filters reviewer noise (D: 3.87 → 4.50)

A redesign analysis of this tool's own skill architecture lost its usability
score because its "start today" items named no files or functions — not
actionable as written. Iteration 2 added concrete pointers. Notably, one
reviewer finding was itself wrong, and the orchestrator rejected it with
grep evidence before iterating: the loop is adversarial in both directions,
which is what keeps false findings from inflating the iteration count.

### Halt Case — Irreversible actions refused without approval (B: 3.78, halted)

A mission implementing four issues stopped itself instead of closing:
applying a production database migration (physical drop) and running live
posting validation were flagged as irreversible actions requiring explicit
confirmation. The state records the halt reason verbatim; a later session
completed the work at 4.50 after approval. A completion-driven single pass
with green CI has no mechanism that refuses to finish here.

## What This Evidence Does and Does Not Support

Supported:

- The review gate catches real, verifiable defects — factual errors, runtime
  UI bugs, security-relevant inconsistencies, non-actionable plans — in
  roughly 5% of production missions, and measurably improves those artifacts
  across iterations.
- The halt gate stops irreversible production actions pending human approval,
  which is a governance property independent of quality scores.

Not supported:

- Any claim that mission raises average artifact quality over a plain single
  pass. Completed paired benchmark runs to date show ties on completion and
  validator metrics with higher time and cost for mission
  (see [`benchmarks/mission-vs-goal/report.md`](../benchmarks/mission-vs-goal/report.md)).
- Any claim that the 5% catch rate transfers to other users' task mixes.
- Fifteen flat repeat iterations are recorded cost with no measured quality
  change; reducing them is open work.
