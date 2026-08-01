# portfolio-simple-lookup-mission

## Mission

Read `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md`, report the `TIMEOUT_MS` value for the `search-api` service, and name which service has the largest timeout.

**Complexity:** Simple  
**Max iterations:** 1  
**Budget:** 30 minutes  
**Constraints:** No commit/push/install/network. Only touch this artifact file and `.mission-state/`.

---

## Plan

| # | Action | Input | Output | Done condition | Depends | Parallel |
|---|---|---|---|---|---|---|
| 1 | Read fixture file | `fixtures/portfolio/service-timeouts.md` | Timeout table in memory | File contents visible | — | — |
| 2 | Extract values | Timeout table | `search-api` TIMEOUT_MS + max service name | Both values identified | 1 | — |
| 3 | Write artifact | Extracted values | This document (Execution + Review + Score sections) | File saved | 2 | — |

---

## Execution

**Step 1 – Read fixture**

File: `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md`

Contents (verbatim):

```
| Service    | TIMEOUT_MS |
|------------|------------|
| ingest     | 4000       |
| search-api | 8500       |
| billing    | 12000      |
| notify     | 3000       |
```

**Step 2 – Extract values**

- `search-api` TIMEOUT_MS: **8500**
- Service with the largest timeout: **billing** (TIMEOUT_MS = 12000)

---

## Review

1 reviewer (`correctness-and-evidence` perspective, `review_tier: light` — auto-derived for Simple complexity) reviewed this artifact against the task validator and the independently-verified fixture values.

- Fixture was read exactly as specified; no other files under `benchmarks/mission-vs-goal/` were accessed.
- Both lookup values are directly present in the table — no inference required.
- No commits, pushes, installs, or network calls were made.
- All required artifact headings are present.
- Reviewer finding `correctness-and-evidence-1` (Low severity): the Score section originally self-assigned a numeric "Overall: 5/5" before the mission scoring gate ran. **Fixed**: Score section now presents a self-assessment checklist only, with the numeric composite score sourced from the tool-computed `aggregate-reviews`/`push-score` output (see Score section below).
- Full reviewer JSON archived at `.mission-state/archive/iter-1-78e12ebb-reviews.json`; scoring JSON at `.mission-state/archive/iter-1-78e12ebb-scoring.json` (not reproduced verbatim here per output-compression discipline).

---

## Score

Self-assessment checklist (not a final numeric score — numeric scoring is delegated to the mission scoring gate via `mission-state.py aggregate-reviews` / `push-score`):

| Criterion | Result |
|---|---|
| Correct `search-api` TIMEOUT_MS | 8500 ✓ |
| Correct largest-timeout service | billing ✓ |
| Constraint compliance (no commit/push/install/network) | ✓ |
| Only fixture + artifact touched | ✓ |
| All required headings present | ✓ |

Final gated score (from 1 reviewer, `correctness-and-evidence` perspective, `review_tier: light`, computed via `mission-state.py aggregate-reviews` / `push-score`):

| Item | Score |
|---|---|
| mission_achievement | 5.0 |
| accuracy | 5.0 |
| completeness | 4.0 |
| usability | 5.0 |

**Composite: 4.75** (min_item: 4.0, open_high: 0, review_agreement delta: 0.0 on all axes — single reviewer, light tier)

---

## Stop Decision

Threshold gate: composite 4.75 ≥ 4.0 threshold, min_item 4.0 ≥ 3.5, open_high 0 == 0, review_agreement max delta 0.0 ≤ 1.5 (single reviewer, light tier — agreement metric is vacuous with 1 reviewer, so this is not an independent-agreement signal). All pass conditions met on iteration 1 of `--max-iter 1`. Mission marked `passes: true`; no further iterations run or needed.

---

## Evidence

- Source file: `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md`
- Raw table row for `search-api`: `| search-api | 8500 |`
- Raw table row for `billing`: `| billing | 12000 |`
- No other files read under `benchmarks/mission-vs-goal/`.

---

## Assumptions

- The fixture file is the authoritative source; no external data sources consulted.
- "Largest timeout" is determined by the numeric `TIMEOUT_MS` value (higher = larger).
- Artifact headings match the required list: Mission, Plan, Execution, Review, Score, Stop Decision, Evidence, Assumptions.
