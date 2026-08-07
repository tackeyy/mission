# portfolio-std-policy — mission arm (rep2)

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3) using exactly the three named fixtures, and deliver a
verdict for each request with the exact policy section and roster evidence.
Requests permitted by an explicit clause must be marked compliant with the
clause cited.

- Task id: `portfolio-std-policy` (category: governance)
- Arm: mission (profile: full), complexity: Standard, `--max-iter 2`
- Mission state session: `cc-a421086f-9b12-4021-adce-58c6a8e4ff15`
  (mission id `37e2dea241fe103d`, `.mission-state/sessions/`)

## Plan

Inline bounded plan (iteration 1, Standard → `next` returned `plan-inline`, #339):

| # | Step | Depends on | Done when |
|---|---|---|---|
| 1 | Read the 3 named fixtures (policy, roster, requests) — nothing else under `benchmarks/mission-vs-goal/` | — | All 3 files read verbatim |
| 2 | For each of REQ-01..03, check: approver role validity (§2.1 + roster), dataset count (§3.1), timing (§4.1–4.3) | 1 | Each check maps to a quoted clause/row |
| 3 | Write verdicts with quoted fixture evidence; separate confirmed verdicts from out-of-scope candidates (REQ-04..06 not audited) | 2 | Artifact satisfies validator: verdicts for REQ-01/02/03 with policy/roster evidence |
| 4 | Scored review iteration: 2 parallel reviewers → `review-finalize` → `closeout` | 3 | `passes: true` or halt with reason |

Completion condition: artifact written at the required path with all required
headings; at least one scored review iteration recorded in mission state.

## Execution

Read exactly the three permitted fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

### Verdicts (confirmed findings)

#### REQ-01 — COMPLIANT (explicit emergency clause §4.2)

- **Timing (§4.2 emergency clause)**: fixture states "Access began 2026-06-03
  02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00
  referencing SEV1-2026-018". Policy §4.2: "during a declared SEV-1 incident,
  access MAY begin before approval, provided the request is filed within 24
  hours of access and references the incident id. Such requests are
  compliant." Filing was ~11h50m after access began (within 24 hours) and
  references the incident id `SEV1-2026-018` → the explicit clause applies.
- **Approver (§2.1 + roster)**: "approved by R. Duval 2026-06-03". Roster:
  "R. Duval | data-steward | 2026-02-01 — present" → role valid on the
  approval date.
- **Scope (§3.1)**: "Datasets: payments-raw" — 1 dataset ≤ 2 allowed.
- **Verdict: compliant**, per the explicit emergency clause §4.2 (with §2.1
  and §3.1 also satisfied).

#### REQ-02 — VIOLATION (§2.1: approver not holding the role at approval time)

- **Approver (§2.1 + roster)**: fixture states "approved by K. Ilyes
  2026-06-06". Roster: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
  and "approvals dated after the validity end are not covered by the role."
  The approval date 2026-06-06 is after the validity end 2026-05-31, so the
  approver did not hold the `data-steward` role at the time of approval, as
  §2.1 requires ("MUST be approved by a person holding the `data-steward`
  role at the time of approval, as recorded in the approver roster").
- **Timing (§4.1)**: "Filed 2026-06-05; ... Access began 2026-06-07" —
  approval (2026-06-06) preceded access, so §4.1 alone is not violated; the
  violation is the invalid approver.
- **Scope (§3.1)**: "Datasets: user-profiles" — 1 dataset, within limit.
- **Verdict: violation** of §2.1 (approval not covered by a valid
  `data-steward` role).

#### REQ-03 — COMPLIANT (§2.1, §3.1, §4.1 all satisfied)

- **Approver (§2.1 + roster)**: "approved by M. Sato 2026-06-09". Roster:
  "M. Sato | data-steward | 2025-11-01 — present" → role valid on the
  approval date.
- **Scope (§3.1)**: "Datasets: churn-model, support-transcripts" — exactly 2
  datasets. §3.1: "A single exception request MAY grant access to at most two
  datasets" → explicitly permitted at the limit.
- **Timing (§4.1)**: approved 2026-06-09, "Access began 2026-06-10" —
  "Approval MUST precede access" is satisfied.
- **Verdict: compliant**, citing §3.1's explicit two-dataset allowance plus
  §2.1 and §4.1.

### Rejected / out-of-scope candidates

The fixture `exception-requests.md` also contains REQ-04, REQ-05, and REQ-06.
These are **not audited** here: the task scope is exactly REQ-01, REQ-02, and
REQ-03. No verdicts are claimed for REQ-04..06 (unmeasured in this run).

## Review

Iteration 1 scored review, per mission Standard tier: 2 independent reviewers
(governance-accuracy, evidence-traceability) launched in parallel in a single
message. Reviewer JSON stored under `.mission-state/` and aggregated with
`review-finalize --iteration 1 --min-reviewers 2` (results in Score section;
raw reviewer JSON preserved in mission state archive, not transcribed here
per output-compression discipline #280).

## Score

See mission state for machine-recorded values (filled after review-finalize):

- composite_score: 4.75 (threshold 4.0)
- max_agreement_delta: 0.5 (gate ≤ 1.5)
- open_high: 0; findings evidence recorded at `.mission-state/findings-evidence-iter1.md`
- min scored item: 4.5 (gate ≥ 3.5)
- Gate result: passes = true (closeout exit 0, next_action=report-complete)

## Stop Decision

Stop at iteration 1: composite 4.75 ≥ threshold 4.0, `open_high == 0`,
agreement delta within gate → early-stop conditions met (`mark-passes` via
`closeout`). `--max-iter 2` not exhausted; no second iteration needed.

## Evidence

| Claim | Evidence (exact fixture quote) |
|---|---|
| REQ-01 emergency clause applies | "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018" + §4.2 "filed within 24 hours of access and references the incident id. Such requests are compliant." |
| REQ-01 approver valid | "approved by R. Duval 2026-06-03" + roster "R. Duval \| data-steward \| 2026-02-01 — present" |
| REQ-02 approver invalid | "approved by K. Ilyes 2026-06-06" + roster "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31" + "approvals dated after the validity end are not covered by the role" |
| REQ-03 scope within limit | "Datasets: churn-model, support-transcripts" (2 datasets) + §3.1 "at most two datasets" |
| REQ-03 approver valid, timing ok | "approved by M. Sato 2026-06-09" + roster "M. Sato \| data-steward \| 2025-11-01 — present"; "Access began 2026-06-10" + §4.1 "Approval MUST precede access" |
| Mission loop ran gated review | `.mission-state/sessions/cc-a421086f-9b12-4021-adce-58c6a8e4ff15.json` (score_history iteration 1, passes flag set by `mark-passes` inside `closeout`) |

Not routed to goal: `init` returned a normal mission session (complexity
Standard; adaptive routing #276 applies only to Simple), so the mission loop
headings are used.

## Assumptions

- `MISSION_PLUGIN_ROOT` points to `~/dev/mission`, but the local-authoring
  sync script was **not** run because this benchmark forbids network access;
  the repo-root `scripts/mission-state.py` was used as the canonical CLI
  (permitted: "リポジトリ root では scripts/mission-state.py").
- Timestamps in the requests fixture without a time-of-day are treated as
  dates only; ordering conclusions (approval before access) rely only on
  date ordering, which is unambiguous for REQ-02/REQ-03.
- REQ-01's approval date (2026-06-03) relative to the 14:00 filing time is
  not specified to the hour; this does not affect the verdict because §4.2
  explicitly permits access before approval during a declared SEV-1.
- Reviewer scores are produced by subagent reviewers inside this run; they
  measure artifact quality for the gate, not benchmark superiority (no
  cross-arm comparison is made or claimed).
- Wall-clock/turn-count metrics for this run are unmeasured; no performance
  claims are made.
