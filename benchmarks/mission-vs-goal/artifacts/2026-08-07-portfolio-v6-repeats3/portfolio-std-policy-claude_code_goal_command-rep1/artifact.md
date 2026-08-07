# portfolio-std-policy — claude_code_goal_command — rep1

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), using exactly the three named fixtures, and deliver a verdict for each request with the exact policy section and roster evidence. Requests permitted by an explicit clause must be marked compliant with the clause cited.

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | Compliant | Emergency clause §4.2 explicitly permits pre-approval access during a declared SEV-1 incident; all conditions met. Approver R. Duval held `data-steward` at approval time (§2.1). |
| REQ-02 | Violation | Approver K. Ilyes's `data-steward` role validity ended 2026-05-31; approval dated 2026-06-06 is not covered by the role, so §2.1 is not satisfied. |
| REQ-03 | Compliant | Approved by valid `data-steward` M. Sato (§2.1), approval (2026-06-09) precedes access (2026-06-10) per §4.1, and 2 datasets is within the "at most two datasets" limit of §3.1. |

### Confirmed findings vs rejected candidates

Confirmed findings (verdict-determining):
- REQ-01 is compliant under the §4.2 emergency clause (evidence below).
- REQ-02 violates §2.1 because the approver's role had expired at approval time (evidence below).
- REQ-03 is compliant under §2.1, §3.1, and §4.1 (evidence below).

Rejected candidates (issues considered and ruled out):
- REQ-01 "access before approval" as a §4.1/§4.3 violation — rejected. §4.2 states "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." Access began 2026-06-03 02:10, the request was filed 2026-06-03 14:00 (≈11h50m < 24h) and "referencing SEV1-2026-018", so the explicit clause applies and the request must be marked compliant with §4.2 cited.
- REQ-02 timing as a violation — rejected as a basis. Approval (2026-06-06) precedes access (2026-06-07), satisfying §4.1. The sole violation is the approver's expired role under §2.1.
- REQ-03 dataset count as a §3.1 violation — rejected. Two datasets ("churn-model, support-transcripts") is exactly the §3.1 maximum ("at most two datasets"), not an excess.

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
- Note: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

### Requests (`benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`)

- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
  - §4.2 conditions: declared SEV-1 incident (id "SEV1-2026-018"), filed within 24 hours (02:10 → 14:00 same day), incident id referenced. Approver "R. Duval" valid "2026-02-01 — present" covers 2026-06-03 (§2.1). One dataset ≤ two (§3.1). → Compliant, clause §4.2 cited.
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
  - Roster shows "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"; approval date 2026-06-06 is after 2026-05-31, and the roster note says such approvals "are not covered by the role". → Violation of §2.1.
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
  - Approver "M. Sato" valid "2025-11-01 — present" covers 2026-06-09 (§2.1). Approval 2026-06-09 precedes access 2026-06-10 (§4.1). Two datasets = §3.1 maximum. → Compliant.

## Assumptions

- "SEV1-2026-018" in REQ-01 is a declared SEV-1 incident as required by §4.2; the fixture states access began "during incident SEV1-2026-018" and no fixture contradicts this. Whether the incident was formally "declared" is not separately recorded in the fixtures (unmeasured beyond the quoted wording).
- Dates without times (e.g. approvals) are interpreted calendar-day granular; no verdict depends on intra-day ordering except REQ-01's 24-hour filing window, which is explicit in the fixture (02:10 → 14:00).
- The audit scope is REQ-01..03 only, per the task prompt; REQ-04..06 appear in the fixture but were not audited.
- No timing, token, or cost measurements were taken for this run; any such metrics are unmeasured.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-policy-claude_code_goal_command-rep1.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition) plus verdicts for REQ-01, REQ-02, and REQ-03 with policy-section and roster evidence. Only the three permitted fixtures and this output file were touched; no commits, pushes, installs, or network access occurred. The stop condition is met.
