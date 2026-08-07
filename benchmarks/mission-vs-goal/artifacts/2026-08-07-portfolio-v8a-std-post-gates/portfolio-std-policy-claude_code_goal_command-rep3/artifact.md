# portfolio-std-policy — Exception Request Audit (rep3, claude_code_goal_command)

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), delivering a verdict for each request with the exact policy section and approver-roster evidence. Requests permitted by an explicit clause must be marked compliant with the clause cited.

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **Compliant** | Emergency clause §4.2 explicitly permits pre-approval access during a declared SEV-1 incident; all conditions met. Approver valid per roster. |
| REQ-02 | **Violation** | Approved by K. Ilyes on 2026-06-06, after their `data-steward` role validity ended 2026-05-31 — fails §2.1 (approver must hold the role "at the time of approval"). |
| REQ-03 | **Compliant** | Valid approver (§2.1), exactly two datasets (within §3.1 limit), approval precedes access (§4.1). |

Confirmed findings vs rejected candidates:

- **Confirmed compliant**: REQ-01 (via explicit clause §4.2), REQ-03.
- **Confirmed violation**: REQ-02 (§2.1 approver-role failure).
- **Rejected candidate — REQ-01 as a timing violation (§4.3)**: rejected because access began during declared incident `SEV1-2026-018`, the request was filed within 24 hours and references the incident id, so the §4.2 emergency clause applies and §4.3 does not.
- **Rejected candidate — REQ-03 as a scope violation (§3.1)**: rejected because §3.1 allows "at most two datasets" and REQ-03 grants exactly two (`churn-model`, `support-transcripts`).

## Evidence

### REQ-01 — Compliant (policy §4.2 emergency clause)

- Fixture (`exception-requests.md`): "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Policy §4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
  - Access 02:10 → filing 14:00 on the same day (2026-06-03) is within 24 hours, and the filing references incident id `SEV1-2026-018`. All §4.2 conditions are satisfied.
- Policy §2.1 (approver): "approved by a person holding the `data-steward` role at the time of approval". Roster: "R. Duval | data-steward | 2026-02-01 — present" — valid on 2026-06-03.
- Policy §3.1 (scope): one dataset (`payments-raw`) ≤ two datasets — within limit.

### REQ-02 — Violation (policy §2.1, roster validity)

- Fixture (`exception-requests.md`): "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Roster (`approver-roster.md`): "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- Policy §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
  - Approval date 2026-06-06 is after K. Ilyes's validity end (2026-05-31) → the approval is not covered by the role → §2.1 violation.
- Other dimensions are not the failure: scope is one dataset (`user-profiles`, within §3.1) and access (2026-06-07) began after approval (2026-06-06), consistent with §4.1 — but a valid approval never existed, so the request is a violation.

### REQ-03 — Compliant (policy §2.1, §3.1, §4.1)

- Fixture (`exception-requests.md`): "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- Policy §2.1 (approver): Roster: "M. Sato | data-steward | 2025-11-01 — present" — valid on 2026-06-09.
- Policy §3.1 (scope): "A single exception request MAY grant access to at most two datasets." REQ-03 grants exactly two (`churn-model`, `support-transcripts`) — within the explicit limit.
- Policy §4.1 (timing): "Approval MUST precede access." Approval 2026-06-09 precedes access 2026-06-10.

## Assumptions

- Scope of audit is exactly REQ-01, REQ-02, and REQ-03 as specified in the task prompt. REQ-04, REQ-05, and REQ-06 also appear in `exception-requests.md` but are out of scope and were not audited.
- "SEV1-2026-018" in REQ-01 is treated as a declared SEV-1 incident; the fixture presents it as "incident SEV1-2026-018" and no fixture contradicts the declaration. Whether the incident was formally "declared" is otherwise unmeasured (no incident registry fixture was provided or named in the prompt).
- Roster validity is interpreted per the roster's own note ("approvals dated after the validity end are not covered by the role"): an approval dated on or before the end date would be covered; K. Ilyes's approval (2026-06-06) is strictly after the end date (2026-05-31), so no boundary ambiguity affects the REQ-02 verdict.
- Only the three fixture files named in the task prompt were read. No other files under `benchmarks/mission-vs-goal/` were opened, per the run rules.
- Timing comparisons use the dates as written in the fixtures; timezone effects are unmeasured and could not change any verdict here (all decisive gaps are ≥ 1 day, or explicitly within the same calendar day for the 24-hour window).

## Stop Condition

This run stops when this single artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-policy-claude_code_goal_command-rep3.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition) plus verdicts for REQ-01, REQ-02, and REQ-03 with policy-section and roster evidence. All conditions are now met: verdicts are Compliant (REQ-01, §4.2 explicit clause cited), Violation (REQ-02, §2.1 + roster validity), Compliant (REQ-03, §2.1/§3.1/§4.1). No commits, pushes, package installs, or network access were performed; edits were limited to this one output file.
