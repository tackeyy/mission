# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-std-policy-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit exception requests REQ-01, REQ-02, and REQ-03 against the access policy, using exactly the three named fixtures (`access-policy.md`, `approver-roster.md`, `exception-requests.md`), and deliver a verdict for each with exact policy section and roster evidence.

## Result

| Request | Verdict | Key basis |
|---|---|---|
| REQ-01 | **Compliant** | Emergency clause 4.2 explicitly permits pre-approval access during a declared SEV-1 incident; conditions met. |
| REQ-02 | **Violation** | Approver K. Ilyes's `data-steward` role validity had already ended before the approval date (policy 2.1). |
| REQ-03 | **Compliant** | Valid approver, approval preceded access, and dataset count is within the two-dataset limit (policy 2.1, 3.1, 4.1). |

### Confirmed findings

**REQ-01 — Compliant (via emergency clause 4.2)**
- Fixture text: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Policy 4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- Access (02:10) preceded filing (14:00) on the same day 2026-06-03 — well within the 24-hour window — and the request references incident id "SEV1-2026-018", satisfying both conditions of 4.2.
- Approver check (policy 2.1: "approved by a person holding the `data-steward` role at the time of approval"): roster shows "R. Duval | data-steward | 2026-02-01 — present", which covers the approval date 2026-06-03. Valid.
- Scope check (policy 3.1: "at most two datasets"): only one dataset (`payments-raw`) is requested — within limit.
- Conclusion: REQ-01 is compliant under the explicit emergency clause 4.2 (and independently satisfies 2.1 and 3.1).

**REQ-02 — Violation (invalid approver under 2.1)**
- Fixture text: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Roster entry: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31", with roster note: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- The approval date 2026-06-06 falls after K. Ilyes's role validity end date of 2026-05-31, so per the roster note the approval is not covered by the `data-steward` role.
- Policy 2.1 requires approval "by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster" — this condition is not met for REQ-02.
- Note: timing (policy 4.1, approval before access: filed 06-05, approved 06-06, access began 06-07) and scope (3.1, one dataset `user-profiles`) are both otherwise fine, but the invalid-approver defect under 2.1 is sufficient to make REQ-02 non-compliant.
- Conclusion: REQ-02 is a violation of policy section 2.1, evidenced by the roster's K. Ilyes validity window (2026-01-15 — 2026-05-31) and its accompanying validity note.

**REQ-03 — Compliant**
- Fixture text: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- Approver check (policy 2.1): roster shows "M. Sato | data-steward | 2025-11-01 — present", covering approval date 2026-06-09. Valid.
- Timing check (policy 4.1: "Approval MUST precede access"): approval 2026-06-09 precedes access start 2026-06-10. Compliant.
- Scope check (policy 3.1: "at most two datasets"): exactly two datasets (`churn-model`, `support-transcripts`) — at the limit, which is permitted ("at most two").
- Conclusion: REQ-03 is compliant with policy sections 2.1, 3.1, and 4.1; no exception clause needed.

### Rejected candidates

No additional non-compliance candidates were found or rejected for REQ-01, REQ-02, or REQ-03 beyond the findings above — each request received exactly one verdict based on the evidence cited. (REQ-04, REQ-05, REQ-06 appear in the exception-requests fixture but are out of scope for this task and were not audited.)

## Evidence

- `access-policy.md` §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- `access-policy.md` §3.1: "A single exception request MAY grant access to at most two datasets."
- `access-policy.md` §3.2: "a `data-steward` MAY approve requests originating from any team, not only their own." (not triggered by REQ-01/02/03; no cross-team approvals among these three)
- `access-policy.md` §4.1: "Approval MUST precede access."
- `access-policy.md` §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- `access-policy.md` §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden... a violation regardless of later approval." (not applicable to REQ-01/02/03; REQ-01 access preceded filing but is covered by the 4.2 exception, and REQ-02/REQ-03 both had approval precede access)
- `approver-roster.md`: "M. Sato | data-steward | 2025-11-01 — present"; "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"; "R. Duval | data-steward | 2026-02-01 — present"; "T. Okafor | data-steward | 2026-06-10 — present"; and the note "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- `exception-requests.md` REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- `exception-requests.md` REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- `exception-requests.md` REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

## Assumptions

- "Time of approval" in policy 2.1 is taken as the approval date stated in each request (no time-of-day is given for approvals in REQ-01/02/03 other than REQ-01's access timestamp), and roster validity windows are treated as inclusive of their end dates unless stated otherwise by the roster note.
- The roster note ("approvals dated after the validity end are not covered by the role") is read as dispositive for K. Ilyes/REQ-02: since 2026-06-06 is strictly after 2026-05-31, the approval is not covered, regardless of time-of-day granularity.
- No SEV-1 incident is referenced for REQ-02 or REQ-03, so the 4.2 emergency clause is not applicable to those two; this is based solely on the absence of any incident-id text in their fixture entries (unmeasured beyond what the fixture states).
- REQ-04, REQ-05, REQ-06 were read as part of the same fixture file (unavoidable, since they're in the same document as REQ-01–03) but were not analyzed or scored, per the task scope limited to REQ-01, REQ-02, REQ-03.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), and provides a verdict with cited policy section and roster evidence for each of REQ-01, REQ-02, and REQ-03. No further edits are planned; the task is done.
