# portfolio-std-policy — Exception Request Audit (claude_code_goal_command / rep3)

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), delivering a verdict for each request with exact policy section and approver-roster evidence. Fixtures read (exactly these three):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **Compliant** | Emergency clause §4.2 explicitly permits pre-approval access during a declared SEV-1 incident; all conditions met. Approver R. Duval held `data-steward` role on the approval date. |
| REQ-02 | **Violation** | §2.1 — approver K. Ilyes's `data-steward` role validity ended 2026-05-31, before the 2026-06-06 approval. |
| REQ-03 | **Compliant** | Approver M. Sato held a valid `data-steward` role (§2.1); two datasets is within the §3.1 limit; approval (2026-06-09) preceded access (2026-06-10) per §4.1. |

### Confirmed findings (verdicts)

**REQ-01 — Compliant (per explicit clause §4.2).**
Access began before approval, which would normally violate §4.1, but §4.2 states: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." All three conditions hold:
1. Declared incident referenced: "Access began 2026-06-03 02:10 during incident SEV1-2026-018".
2. Filed within 24 hours of access: "request filed 2026-06-03 14:00" (11h50m after 02:10 access start).
3. References the incident id: "referencing SEV1-2026-018".
Approver validity (§2.1): "approved by R. Duval 2026-06-03"; roster shows "R. Duval | data-steward | 2026-02-01 — present", so the role was held at approval time. Scope (§3.1): single dataset "payments-raw" ≤ 2 datasets.

**REQ-02 — Violation (§2.1, roster validity).**
"Filed 2026-06-05; approved by K. Ilyes 2026-06-06." The roster lists "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and states "approvals dated after the validity end are not covered by the role." The 2026-06-06 approval postdates the 2026-05-31 validity end, so §2.1 ("MUST be approved by a person holding the `data-steward` role at the time of approval") is not satisfied. Timing itself was fine (approval 2026-06-06 preceded access "Access began 2026-06-07" per §4.1) and scope was fine (single dataset "user-profiles" per §3.1), but the invalid approver makes the request a violation.

**REQ-03 — Compliant.**
- §2.1 approver: "approved by M. Sato 2026-06-09"; roster shows "M. Sato | data-steward | 2025-11-01 — present" — valid at approval time.
- §3.1 scope: "Datasets: churn-model, support-transcripts" — exactly two datasets, within "at most two datasets".
- §4.1 timing: "Filed 2026-06-09; approved by M. Sato 2026-06-09 ... Access began 2026-06-10" — approval preceded access.

### Rejected candidate violation theories (evaluated and dismissed)

- REQ-01 as a §4.1/§4.3 retroactive-approval violation — **rejected**: the §4.2 emergency clause explicitly applies ("Such requests are compliant") because the SEV-1 incident id SEV1-2026-018 is referenced and filing occurred within 24 hours.
- REQ-03 as a §3.1 scope violation — **rejected**: two datasets is exactly the permitted maximum ("MAY grant access to at most two datasets"), not an excess.
- REQ-02 as a timing (§4.1) or scope (§3.1) violation — **rejected**: approval preceded access and only one dataset was requested; the sole violation is the expired approver role.

## Evidence

Policy (`access-policy.md`):
- §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- §3.1: "A single exception request MAY grant access to at most two datasets."
- §4.1: "Approval MUST precede access."
- §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden."

Roster (`approver-roster.md`):
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

Requests (`exception-requests.md`):
- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

## Assumptions

- The audit scope is exactly REQ-01, REQ-02, and REQ-03 as instructed; REQ-04 through REQ-06 appear in the same fixture but are out of scope and were not audited.
- "SEV1-2026-018" in REQ-01 is treated as a declared SEV-1 incident id satisfying §4.2's "declared SEV-1 incident" condition; no separate incident register fixture was provided, so incident declaration status beyond the request text is unmeasured.
- Roster validity boundaries are inclusive of the listed end date, per the roster note that approvals dated *after* the validity end are not covered (K. Ilyes's 2026-06-06 approval is after 2026-05-31 under any reading).
- All timestamps are assumed to share a single timezone; the fixtures do not specify timezones (unmeasured), but no verdict depends on a timezone difference (the largest relevant gap, REQ-01's 11h50m filing delay, is far inside the 24-hour window).

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-policy-claude_code_goal_command-rep3.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), with verdicts for REQ-01, REQ-02, and REQ-03 each backed by exact policy-section and roster quotes. No commits, pushes, package installs, or network access were performed; only this single output file was written. Task complete — stopping.
