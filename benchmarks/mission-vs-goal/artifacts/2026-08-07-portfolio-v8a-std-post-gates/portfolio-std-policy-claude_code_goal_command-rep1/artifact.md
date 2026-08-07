# portfolio-std-policy — claude_code_goal_command — rep1

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3), delivering a verdict for each request with exact policy
section and approver-roster evidence. Requests permitted by an explicit clause
must be marked compliant with the clause cited.

Fixtures read (exactly these three):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **COMPLIANT** (via explicit emergency clause 4.2) | Access before approval is permitted during a declared SEV-1 incident; all conditions of 4.2 are met, approver valid per 2.1, scope within 3.1 |
| REQ-02 | **VIOLATION** of section 2.1 | Approved by K. Ilyes on 2026-06-06, after their `data-steward` role validity ended 2026-05-31 — approver did not hold the role at the time of approval |
| REQ-03 | **COMPLIANT** | Valid approver (2.1), exactly two datasets (3.1), approval on 2026-06-09 precedes access on 2026-06-10 (4.1) |

### Confirmed findings (violations)

- **REQ-02** violates policy section 2.1 ("An exception request MUST be
  approved by a person holding the `data-steward` role at the time of
  approval, as recorded in the approver roster").
  - Request evidence: "approved by K. Ilyes 2026-06-06" (exception-requests.md, REQ-02).
  - Roster evidence: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
    and the roster note "Role validity ends on the date listed; approvals
    dated after the validity end are not covered by the role"
    (approver-roster.md).
  - 2026-06-06 > 2026-05-31, so the approval is not covered by the role.
  - Note: timing (4.1) and scope (3.1) are otherwise satisfied for REQ-02
    (approval 2026-06-06 precedes access 2026-06-07; single dataset
    `user-profiles`). The sole defect is the invalid approver.

### Rejected violation candidates (compliant requests)

- **REQ-01 — rejected as a violation; COMPLIANT under emergency clause 4.2.**
  Although "Access began 2026-06-03 02:10" precedes approval (which would
  normally breach 4.1 "Approval MUST precede access"), section 4.2 explicitly
  states: "during a declared SEV-1 incident, access MAY begin before approval,
  provided the request is filed within 24 hours of access and references the
  incident id. Such requests are compliant."
  - Incident reference: access began "during incident SEV1-2026-018" and the
    request was "filed 2026-06-03 14:00 referencing SEV1-2026-018"
    (exception-requests.md, REQ-01).
  - Filing within 24 hours: 2026-06-03 02:10 → 2026-06-03 14:00 is 11h50m < 24h.
  - Approver validity (2.1): "approved by R. Duval 2026-06-03"; roster shows
    "R. Duval | data-steward | 2026-02-01 — present" — valid on 2026-06-03.
  - Scope (3.1): single dataset "payments-raw" ≤ two datasets.

- **REQ-03 — rejected as a violation; COMPLIANT.**
  - Approver validity (2.1): "approved by M. Sato 2026-06-09"; roster shows
    "M. Sato | data-steward | 2025-11-01 — present" — valid on 2026-06-09.
  - Timing (4.1): approval 2026-06-09 precedes "Access began 2026-06-10".
  - Scope (3.1): "A single exception request MAY grant access to at most two
    datasets" — REQ-03 lists exactly two: "churn-model, support-transcripts",
    which is within the explicit limit.

## Evidence

All quotes are verbatim from the three named fixture files.

Policy sections (access-policy.md):

- 2.1: "An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster."
- 3.1: "A single exception request MAY grant access to at most two datasets."
- 4.1: "Approval MUST precede access."
- 4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin
  before approval, provided the request is filed within 24 hours of access and
  references the incident id. Such requests are compliant."

Roster (approver-roster.md):

- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "Role validity ends on the date listed; approvals dated after the validity
  end are not covered by the role."

Requests (exception-requests.md):

- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018;
  request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by
  R. Duval 2026-06-03. Datasets: payments-raw."
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets:
  user-profiles. Access began 2026-06-07."
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets:
  churn-model, support-transcripts. Access began 2026-06-10."

## Assumptions

- The audit scope is exactly REQ-01, REQ-02, and REQ-03 as instructed;
  REQ-04 through REQ-06 appear in the fixture but were not audited.
- REQ-01's "during incident SEV1-2026-018" is taken as a declared SEV-1
  incident as required by clause 4.2; the fixture provides no separate
  incident register, so declaration status beyond the stated incident id is
  unmeasured.
- Dates without times (e.g. approval dates) are compared at day granularity;
  "approval precedes access" is satisfied when the approval date is strictly
  earlier than or equal to a later-dated access start, as in REQ-02
  (2026-06-06 < 2026-06-07) and REQ-03 (2026-06-09 < 2026-06-10).
- No fixture states whether later re-approval occurred for REQ-02; whether
  any remediation happened is unmeasured and does not affect the 2.1 verdict.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-policy-claude_code_goal_command-rep1.md`
and contains the required headings (Goal, Result, Evidence, Assumptions,
Stop Condition) plus verdicts for REQ-01, REQ-02, and REQ-03 with policy
section and roster evidence. Task validator condition met; no further edits,
no commits, no network access. Run stops here.
