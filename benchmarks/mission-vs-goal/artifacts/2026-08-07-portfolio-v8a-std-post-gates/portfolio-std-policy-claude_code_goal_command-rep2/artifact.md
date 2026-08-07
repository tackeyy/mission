# portfolio-std-policy — claude_code_goal_command — rep2

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), delivering a verdict for each request with exact policy section and approver-roster evidence. Requests permitted by an explicit clause must be marked compliant with the clause cited. Completion is controlled by Claude Code's built-in `/goal` command (arm: `claude_code_goal_command`).

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **COMPLIANT** | Emergency clause §4.2 explicitly permits pre-approval access during a declared SEV-1 incident; all conditions met. Approver R. Duval held `data-steward` at time of approval (§2.1). |
| REQ-02 | **VIOLATION** | §2.1 requires the approver to hold `data-steward` **at the time of approval**. K. Ilyes's role validity ended 2026-05-31; the approval is dated 2026-06-06. |
| REQ-03 | **COMPLIANT** | Approval (2026-06-09, M. Sato, valid role) preceded access (2026-06-10) per §4.1; two datasets is within the §3.1 limit of "at most two datasets". |

### Confirmed findings vs. rejected candidates

**Confirmed findings (violations):**
- REQ-02 — invalid approver: approval by "K. Ilyes" on "2026-06-06" falls after the roster validity end "2026-05-31". Violates §2.1.

**Rejected candidates (evaluated and found compliant):**
- REQ-01 — the pattern "access before approval" looks like a §4.1/§4.3 timing violation, but is rejected as a finding: the explicit emergency clause §4.2 applies. Access began "2026-06-03 02:10 during incident SEV1-2026-018"; the request was "filed 2026-06-03 14:00" (11h50m after access, within 24 hours) and references the incident id "SEV1-2026-018". Per §4.2, "Such requests are compliant."
- REQ-03 — the two-dataset grant ("churn-model, support-transcripts") is rejected as a scope finding: §3.1 permits "at most two datasets". Approver M. Sato is a valid `data-steward` ("2025-11-01 — present"), and approval (2026-06-09) preceded access (2026-06-10) per §4.1.

REQ-04, REQ-05, and REQ-06 appear in the same fixture but are **out of scope** for this task (the prompt limits the audit to REQ-01/02/03) and were not audited.

## Evidence

All quotes are verbatim from the three permitted fixtures.

### Policy (`benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`)
- §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- §3.1: "A single exception request MAY grant access to at most two datasets."
- §4.1: "Approval MUST precede access."
- §4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden."

### Roster (`benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`)
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- Roster note: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

### Requests (`benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`)
- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
  - §4.2 satisfied: filed 11h50m after access (< 24 hours), references incident id SEV1-2026-018. §2.1 satisfied: R. Duval valid "2026-02-01 — present" covers 2026-06-03. §3.1 satisfied: one dataset (payments-raw).
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
  - §2.1 violated: K. Ilyes validity "2026-01-15 — 2026-05-31" ends before the approval date 2026-06-06. (Timing §4.1 and scope §3.1 would otherwise be satisfied, but the invalid approver alone makes the request a violation.)
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
  - §2.1 satisfied: M. Sato valid "2025-11-01 — present". §4.1 satisfied: approval 2026-06-09 precedes access 2026-06-10. §3.1 satisfied: exactly two datasets, within "at most two".

## Assumptions

- "at the time of approval" in §2.1 is interpreted by calendar date against the roster validity ranges; the roster's own note ("approvals dated after the validity end are not covered by the role") confirms this reading for REQ-02.
- REQ-01's incident id "SEV1-2026-018" is taken as a declared SEV-1 incident as stated in the fixture; no external incident registry was checked (unmeasured — no such fixture is permitted for this task).
- The 24-hour window in §4.2 is measured from access start (2026-06-03 02:10) to filing (2026-06-03 14:00) = 11 hours 50 minutes.
- Only the three named fixture files and this output file were read/written, per the run rules. Benchmark metadata, task definitions, and answer keys were not accessed.
- No commits, pushes, package installs, or network access were performed.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-policy-claude_code_goal_command-rep2.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), verdicts for REQ-01, REQ-02, and REQ-03, and policy/roster evidence for each verdict. The `/goal` Stop hook condition is therefore satisfied and the run stops here. No claim of benchmark superiority is made; this artifact only completes the task.
