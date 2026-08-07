# portfolio-std-policy — mission arm (rep1)

- Task id: `portfolio-std-policy` / Category: governance / Arm: mission / Profile: full
- Mission session: `cc-a11bc221-a81e-4767-b316-b45cd98d9a6b` (mission_id `e3bbaf49769c1e5a`)
- Date: 2026-08-07

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3), using exactly the three named fixtures
(`access-policy.md`, `approver-roster.md`, `exception-requests.md`), and
deliver a verdict per request with exact policy-section and roster evidence.
Complexity: Standard. Routing: `init` returned a mission state (no
`route: "goal"` verdict), so the full mission loop applies.

## Plan

Inline bounded plan (iteration 1, Standard, per #339 `plan-inline`):

| Step | Action | Depends on | Done when |
|---|---|---|---|
| 1 | Read the 3 named fixtures (only these; no other benchmark files) | — | All 3 fixture contents captured |
| 2 | Extract policy clauses (2.1, 3.1, 3.2, 4.1, 4.2, 4.3) and roster validity windows | 1 | Clause/roster table available |
| 3 | Evaluate REQ-01/02/03 on approver-role validity, dataset scope, timing/emergency clause | 2 | Verdict + clause citation per request |
| 4 | Write artifact with verdicts, evidence quotes, rejected candidates separated | 3 | Validator satisfied: verdicts for REQ-01/02/03 with policy/roster evidence |
| 5 | Scored review iteration: 2 reviewers (parallel) → review-finalize → closeout | 4 | `passes: true` or halt with reason |

Completion condition: artifact written at this path, validator headings present,
mission gate (`composite >= 4.0`, `open_high == 0`, agreement delta <= 1.5) passed.

## Execution

Read all three fixtures (full contents, lines 1–23 / 1–12 / 1–27 respectively).
Applied the policy clause-by-clause to each request.

### Verdicts (confirmed findings)

**REQ-01 — COMPLIANT (emergency clause §4.2; approver valid per roster).**
- Facts (exception-requests.md): "Access began 2026-06-03 02:10 during incident
  SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018;
  approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Policy §4.2: "Emergency clause: during a declared SEV-1 incident, access MAY
  begin before approval, provided the request is filed within 24 hours of
  access and references the incident id. Such requests are compliant."
  Access-to-filing
  gap is 11h50m (02:10 → 14:00 same day) < 24h, and the request references
  `SEV1-2026-018`. Clause conditions met, so pre-approval access is explicitly
  permitted → marked compliant with §4.2 cited.
- Policy §2.1 approver check: R. Duval is `data-steward` with validity
  "2026-02-01 — present" (approver-roster.md), covering the 2026-06-03 approval.
- Policy §3.1 scope check: 1 dataset (`payments-raw`) ≤ 2. OK.

**REQ-02 — VIOLATION (approver role expired; policy §2.1 + roster validity rule).**
- Facts: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets:
  user-profiles. Access began 2026-06-07."
- Policy §2.1: "An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster."
- Roster: K. Ilyes validity is "2026-01-15 — 2026-05-31", and the roster states
  "approvals dated after the validity end are not covered by the role." The
  approval date 2026-06-06 is after 2026-05-31 → K. Ilyes did not hold the role
  at the time of approval → violation of §2.1.
- Other clauses are not the failure: timing order Filed 06-05 → Approved 06-06 →
  Access 06-07 satisfies §4.1, and 1 dataset satisfies §3.1. The sole defect is
  the approver's expired role.

**REQ-03 — COMPLIANT (§2.1, §3.1, §4.1 all satisfied).**
- Facts: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets:
  churn-model, support-transcripts. Access began 2026-06-10."
- Policy §2.1 + roster: M. Sato is `data-steward`, validity
  "2025-11-01 — present", covering the 2026-06-09 approval.
- Policy §3.1: "A single exception request MAY grant access to at most two
  datasets." Two datasets (`churn-model`, `support-transcripts`) = the explicit
  maximum, so this is permitted by the clause → compliant with §3.1 cited (not a
  violation).
- Policy §4.1: approval 2026-06-09 precedes access 2026-06-10. OK.

### Rejected candidates (out of task scope / not findings)

- **REQ-04, REQ-05, REQ-06** appear in `exception-requests.md` but are outside
  the audited set (task instructs REQ-01..REQ-03 only). No verdicts issued.
  Noted for transparency: they were read as part of the fixture file but not
  audited or scored.
- **Rejected violation candidate for REQ-01 (timing §4.1/§4.3):** access before
  approval would normally violate §4.1, but §4.2's emergency clause explicitly
  permits it here (SEV-1 incident id referenced, filed within 24h), so the
  timing-violation candidate is rejected.
- **Rejected violation candidate for REQ-03 (scope §3.1):** two datasets might
  look broad, but §3.1 explicitly allows "at most two datasets" — the candidate
  is rejected; REQ-03 is within scope limits.
- **Rejected violation candidate for REQ-02 (timing):** filing/approval/access
  order is correct for REQ-02; the only confirmed defect is the expired
  approver role.

## Review

Iteration 1: 2 independent reviewers (Standard complexity) launched in a single
message (parallel; `review-finalize` recorded `parallel_execution: true` from
reviewer windows 06:37:11Z..06:38:08Z and 06:37:11Z..06:39:19Z). Perspectives:
(A) governance-correctness — verdicts vs. policy text; (B)
evidence-traceability — every claim backed by exact fixture quotes.

- Reviewer A (governance-correctness): scores 5/5/5/5, findings: none.
- Reviewer B (evidence-traceability): scores 5/4/5/5, findings: 2 Low on the
  accuracy axis (quote boundaries: the §4.2 quote dropped the leading
  "Emergency clause:" label; the §2.1 Execution quote started mid-sentence,
  omitting the subject "An exception request"). Both Low findings were fixed
  inline in this artifact after scoring (Low severity — M6 re-review is
  required only for Medium+; scores above are the as-reviewed values, not
  rescored after the fix).
- Raw review JSON: `.mission-state/reviews/iter1-*.json`; aggregated evidence:
  `.mission-state/archive/iter-1-e3bbaf49-reviews.json`.

## Score

Tool-computed values from `review-finalize` (iteration 1, 2026-08-07T06:40:26Z):

- Composite score: **4.88** (items: mission_achievement 5.0 / accuracy 4.5 /
  completeness 5.0 / usability 5.0). Accuracy reflects the rubric cap for
  Reviewer B's 2 Low findings (2-3 Low → axis ≤ 4.5; reviewer gave 4.0, mean
  (5.0+4.0)/2 = 4.5).
- Threshold: 4.0 (default). min(scored_items) = 4.5 ≥ 3.5.
- review_agreement: 4.0; max axis delta 1.0 (accuracy min 4.0 / max 5.0) ≤ 1.5.
- open_high = 0. findings_evidence_path:
  `.mission-state/archive/iter-1-e3bbaf49-reviews.json`; scoring evidence:
  `.mission-state/archive/iter-1-e3bbaf49-scoring.json`.
- Gate result: see Stop Decision (closeout).

## Stop Decision

Early-stop at iteration 1: composite 4.88 ≥ threshold 4.0 and `open_high == 0`,
so the loop stops per the mission early-stop rule (continuation is only
warranted for composite 4.0–4.3 with 3+ Medium findings; here there are zero
Medium+ findings).

Closeout trace: the first `closeout` call exited 2 on the specialist selection
checkpoint (no `task_profile`/`specialists_decision` recorded). Ran
`specialists recommend --record-state` (task_profile.primary=documentation;
decision policy=fallback, action=continue-core — the matched preset
`documentation-provider` is not installed), then `closeout` returned exit 0
with `mark_passes.ok=true`, `passes=true`, `forced=false`,
`next_action=report-complete`, `loop_active=false`. Max-iter was 2; only 1
iteration was needed.

## Evidence

| Claim | Evidence (exact fixture value) | Source |
|---|---|---|
| REQ-01 emergency clause applies | "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018" | exception-requests.md §REQ-01 |
| Emergency clause text | "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." | access-policy.md §4.2 |
| REQ-01 approver valid | "R. Duval \| data-steward \| 2026-02-01 — present" | approver-roster.md |
| REQ-02 approver expired | "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31"; approval dated 2026-06-06 | approver-roster.md; exception-requests.md §REQ-02 |
| Expired-role rule | "approvals dated after the validity end are not covered by the role" | approver-roster.md |
| Approval role requirement | "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval" | access-policy.md §2.1 |
| REQ-03 approver valid | "M. Sato \| data-steward \| 2025-11-01 — present"; approval dated 2026-06-09 | approver-roster.md; exception-requests.md §REQ-03 |
| REQ-03 scope permitted | "A single exception request MAY grant access to at most two datasets." (REQ-03 has exactly two: churn-model, support-transcripts) | access-policy.md §3.1; exception-requests.md §REQ-03 |
| REQ-03 timing ok | "Approval MUST precede access." Approved 2026-06-09, access began 2026-06-10 | access-policy.md §4.1; exception-requests.md §REQ-03 |
| Mission state auditable | session `cc-a11bc221-a81e-4767-b316-b45cd98d9a6b`, mission_id `e3bbaf49769c1e5a`, `.mission-state/sessions/` | mission-state CLI output |

Unmeasured: wall-clock duration and token cost of this run were not measured by
this artifact; no benchmark-comparison claims are made.

## Assumptions

- `MISSION_PLUGIN_ROOT` points at a local authoring checkout, but the mandated
  sync script was **not** run because this benchmark forbids network access;
  the repository-root `scripts/mission-state.py` was used as the CLI (allowed
  primary path per the skill).
- "Roster evidence" is satisfied by quoting the roster row (person, role,
  validity window) against the approval date.
- REQ-04..REQ-06 are distractors outside the audited set; reading them was
  unavoidable (same fixture file) and they received no verdicts.
- §3.2 (cross-team delegation) is not exercised by REQ-01..03 (no cross-team
  approval is flagged in those three requests), so it is cited nowhere.
- Reviewer identities are mission-reviewer subagents; their raw JSON is stored
  under `.mission-state/` for audit, not restated verbatim here (#280).
