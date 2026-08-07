# Task Artifact — portfolio-std-policy (arm: claude_code_goal_command, rep2)

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the access policy
(`access-policy.md`), using the approver roster (`approver-roster.md`) and the
request records (`exception-requests.md`). Deliver a verdict for each request
with exact policy-section and roster evidence. Requests permitted by an
explicit clause must be marked compliant with the clause cited.

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **Compliant** | Emergency clause §4.2 explicitly permits access before approval during a declared SEV-1 incident; all conditions met. |
| REQ-02 | **Violation** | §2.1 — approver K. Ilyes did not hold the `data-steward` role at the time of approval (2026-06-06; role validity ended 2026-05-31). |
| REQ-03 | **Compliant** | §2.1 approval by a valid data-steward, §3.1 dataset count within limit (2 ≤ 2), §4.1 approval preceded access. |

### Confirmed findings vs. rejected candidates

- **Confirmed finding (violation):** REQ-02 only. Evidence: approval dated "2026-06-06" by "K. Ilyes", whose roster validity is "2026-01-15 — 2026-05-31", and the roster states "approvals dated after the validity end are not covered by the role".
- **Rejected candidate — REQ-01 as a timing violation:** Access before approval would normally violate §4.1/§4.3, but §4.2 states "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." REQ-01 satisfies every condition (see Evidence), so the timing-violation candidate is rejected and REQ-01 is marked compliant under the explicit clause §4.2.
- **Rejected candidate — REQ-03 as a scope violation:** REQ-03 grants two datasets ("churn-model, support-transcripts"). §3.1 permits "at most two datasets" per request, so two datasets are within the explicit limit; the scope-violation candidate is rejected.

## Evidence

### REQ-01 — Compliant under §4.2 (emergency clause)

- Fixture record (`exception-requests.md`): "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Policy §4.2 (`access-policy.md`): "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
  - Declared incident: the record cites incident id "SEV1-2026-018".
  - Filed within 24 hours: access 2026-06-03 02:10 → filed 2026-06-03 14:00 (11 h 50 min < 24 h).
  - References the incident id: filing "referencing SEV1-2026-018".
- Approver validity §2.1: roster lists "R. Duval | data-steward | 2026-02-01 — present"; approval dated 2026-06-03 falls within validity.
- Scope §3.1: one dataset ("payments-raw") ≤ two.

### REQ-02 — Violation of §2.1 (approver lacked role at time of approval)

- Fixture record: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Policy §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- Roster: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role." The approval date 2026-06-06 is after 2026-05-31, so the approval is not covered by the role → §2.1 violated.
- Other aspects were not violations: timing ordering satisfied §4.1 (approval 2026-06-06 before access 2026-06-07) and scope satisfied §3.1 (one dataset), but the invalid approver alone makes REQ-02 a violation.

### REQ-03 — Compliant (§2.1, §3.1, §4.1)

- Fixture record: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- §2.1 approver validity: roster lists "M. Sato | data-steward | 2025-11-01 — present"; approval dated 2026-06-09 falls within validity.
- §3.1 scope: "A single exception request MAY grant access to at most two datasets." REQ-03 grants exactly two ("churn-model, support-transcripts") — within the explicit limit.
- §4.1 timing: "Approval MUST precede access." Approval 2026-06-09 precedes access 2026-06-10.

## Assumptions

- The audit scope is exactly REQ-01, REQ-02, and REQ-03 as instructed; REQ-04 through REQ-06 appear in the fixture but were not audited.
- Dates in the fixtures are taken at face value; timestamps without a timezone are assumed to share a single timezone, making the REQ-01 filing interval (11 h 50 min) unambiguous.
- "SEV1-2026-018" in REQ-01 is treated as a declared SEV-1 incident id as required by §4.2; the fixture provides no separate incident registry, so incident declaration status beyond the cited id is unmeasured.
- Only the three named fixture files were read; no benchmark metadata, task definitions, or answer keys were accessed.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-policy-claude_code_goal_command-rep2.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition) plus verdicts for REQ-01, REQ-02, and REQ-03, each backed by exact
policy-section and roster quotes. No commits, pushes, package installs, or
network access were performed; the only file written is this artifact. Task
complete.
