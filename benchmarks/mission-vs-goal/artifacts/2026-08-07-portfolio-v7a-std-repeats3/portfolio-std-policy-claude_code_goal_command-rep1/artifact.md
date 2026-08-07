# Task Artifact: portfolio-std-policy (arm: claude_code_goal_command, rep1)

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), delivering a verdict for each request with exact policy section and approver-roster evidence. Requests permitted by an explicit clause must be marked compliant with the clause cited.

## Result

| Request | Verdict | Governing clause |
|---|---|---|
| REQ-01 | **Compliant** | §4.2 Emergency clause (explicit permission) |
| REQ-02 | **Violation** | §2.1 — approver's `data-steward` role had expired at time of approval |
| REQ-03 | **Compliant** | §2.1, §3.1, §4.1 all satisfied |

### REQ-01 — Compliant (explicit clause: §4.2)

- Access began before approval ("Access began 2026-06-03 02:10 ... approved by R. Duval 2026-06-03"), which would normally violate §4.1 ("Approval MUST precede access").
- However, §4.2 explicitly permits this: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- All §4.2 conditions hold: access occurred "during incident SEV1-2026-018"; the request was "filed 2026-06-03 14:00" — 11 hours 50 minutes after access began at 02:10, within 24 hours; and it was filed "referencing SEV1-2026-018".
- Approver validity: roster lists "R. Duval | data-steward | 2026-02-01 — present", so the 2026-06-03 approval is covered (§2.1 satisfied).
- Scope: single dataset "payments-raw", within the §3.1 limit of "at most two datasets".

### REQ-02 — Violation (§2.1: approver role expired)

- REQ-02 was "approved by K. Ilyes 2026-06-06".
- Roster: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31", and the roster states "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- The 2026-06-06 approval postdates the 2026-05-31 validity end, so K. Ilyes did not hold the `data-steward` role "at the time of approval", violating §2.1 ("An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster").
- Other dimensions are not violated: scope is one dataset ("user-profiles", within §3.1), and timing is fine (filed 2026-06-05, approved 2026-06-06, "Access began 2026-06-07" — §4.1 satisfied). The sole defect is the invalid approver.

### REQ-03 — Compliant

- §2.1 approver: "approved by M. Sato 2026-06-09"; roster lists "M. Sato | data-steward | 2025-11-01 — present", covering the approval date.
- §3.1 scope: two datasets ("churn-model, support-transcripts"), which satisfies "at most two datasets".
- §4.1 timing: approved 2026-06-09, "Access began 2026-06-10" — approval preceded access.
- No clause is violated; compliant.

### Confirmed findings vs. rejected candidates

- **Confirmed findings (in scope, audited)**: REQ-01 compliant via §4.2; REQ-02 violation of §2.1; REQ-03 compliant.
- **Rejected candidates (considered and dismissed)**:
  - REQ-01 as a §4.1 timing violation — rejected because §4.2's emergency clause explicitly applies (SEV-1 incident referenced, filed within 24 hours).
  - REQ-02 as a scope or timing violation — rejected; its only defect is the expired approver role (§2.1).
  - REQ-03 as a §3.1 scope violation — rejected because two datasets is exactly the permitted maximum ("MAY grant access to at most two datasets").
- **Out of scope**: the fixture also contains REQ-04, REQ-05, and REQ-06, but the task limits the audit to REQ-01/02/03, so no verdicts are issued for them (unaudited/unmeasured here).

## Evidence

All quotes are verbatim from the three fixture files (the only files read for this audit):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`:
  - §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
  - §3.1: "A single exception request MAY grant access to at most two datasets."
  - §4.1: "Approval MUST precede access."
  - §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
  - §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden."
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`:
  - "M. Sato | data-steward | 2025-11-01 — present"
  - "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
  - "R. Duval | data-steward | 2026-02-01 — present"
  - "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`:
  - REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
  - REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
  - REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

## Assumptions

- Timestamps without timezone are assumed to share a single timezone, so the REQ-01 filing (2026-06-03 14:00) is within 24 hours of access start (2026-06-03 02:10).
- "2026-05-31" as K. Ilyes's validity end means approvals dated 2026-06-01 or later are not covered — this follows directly from the roster note "approvals dated after the validity end are not covered by the role", so it is a fixture-stated rule rather than a free assumption.
- Incident SEV1-2026-018 is taken as a "declared SEV-1 incident" per §4.2 because the request text says access began "during incident SEV1-2026-018"; no separate incident register was provided or read (unmeasured beyond the fixture statement).
- Team affiliations for cross-team checks (§3.2) were not relevant to REQ-01/02/03 as no team mismatch is stated in those requests.

## Stop Condition

This artifact is complete when it exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-policy-claude_code_goal_command-rep1.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, with verdicts for REQ-01, REQ-02, and REQ-03 backed by policy-section and roster evidence — all of which are satisfied above. No commits, pushes, package installs, or network access were performed; the only files read were the three named fixtures, and the only file written is this artifact.
