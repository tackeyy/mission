# portfolio-std-policy — Mission Artifact

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the access policy, using only:
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

Deliver a verdict for each of the three requests with the exact policy section and roster evidence cited. Requests permitted by an explicit clause are marked compliant with the clause cited. No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed.

Mission state: `.mission-state/sessions/cc-da4515ea-5cdc-4dde-8420-7093da282246.json`, mission_id `e3323ff48e82fd1d`.

## Plan

1. Read the three named fixtures only.
2. Extract each request's timing, approver, and dataset facts.
3. Cross-reference each request's approver against the roster's validity window as of the approval date, and against the access-policy clauses on approval authority, scope, and timing.
4. Assign a verdict (COMPLIANT / NON-COMPLIANT) per request, citing the exact policy section number(s) and the exact roster row used as evidence.
5. Explicitly flag any request that is compliant only because of an explicit emergency/exception clause, citing that clause.
6. Write this artifact under the required headings.

Executed via `mission-planner` (iteration 1 forked plan) followed by direct orchestrator verification against the fixtures (re-read independently to confirm exact quotes before finalizing verdicts).

## Execution

Fixtures were read directly (Read tool) in full. Verbatim extracts used as evidence:

**access-policy.md**
- §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- §3.1: "A single exception request MAY grant access to at most two datasets. Broader access requires separate requests per dataset pair."
- §4.1: "Approval MUST precede access."
- §4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."

**approver-roster.md**
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "T. Okafor | data-steward | 2026-06-10 — present"
- "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

**exception-requests.md**
- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

### Verdict: REQ-01 — COMPLIANT (via explicit clause §4.2)

- Access began 2026-06-03 02:10, before the request was filed (2026-06-03 14:00) and before approval — this would normally violate §4.1 ("Approval MUST precede access").
- However, access occurred "during incident SEV1-2026-018", and the request was "filed 2026-06-03 14:00 referencing SEV1-2026-018" — filed within 24 hours of the 02:10 access start (~12 hours), and it references the incident id. This satisfies §4.2's emergency clause exactly: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- Approver R. Duval is listed in the roster as "R. Duval | data-steward | 2026-02-01 — present" — valid on the 2026-06-03 approval date, satisfying §2.1.
- Datasets: 1 (payments-raw), within the §3.1 limit of "at most two datasets."
- **Verdict: COMPLIANT, permitted by the explicit §4.2 emergency clause**, with approver authority independently confirmed under §2.1 (R. Duval roster row) and scope confirmed under §3.1.

### Verdict: REQ-02 — NON-COMPLIANT

- Approved by K. Ilyes on 2026-06-06.
- Roster row: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31." The approval date (2026-06-06) falls after the listed validity end (2026-05-31).
- The roster explicitly states: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role." Therefore K. Ilyes did not hold a covered `data-steward` role at the time of approval.
- This violates §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- (Timing itself was otherwise fine — approval 2026-06-06 precedes access 2026-06-07, satisfying §4.1 — and the single dataset user-profiles satisfies §3.1 — but the approver-authority defect under §2.1 is independently disqualifying.)
- **Verdict: NON-COMPLIANT — violates §2.1**, evidenced by the roster row "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and the roster's own validity rule, against an approval dated 2026-06-06.

### Verdict: REQ-03 — COMPLIANT

- Approved by M. Sato on 2026-06-09.
- Roster row: "M. Sato | data-steward | 2025-11-01 — present" — valid on 2026-06-09, satisfying §2.1.
- Approval (2026-06-09) precedes access start (2026-06-10), satisfying §4.1 ("Approval MUST precede access").
- Datasets: churn-model and support-transcripts — exactly 2 datasets, at the §3.1 limit ("at most two datasets"), not exceeding it.
- No SEV-1/emergency clause was invoked or needed; the request is compliant under the ordinary (non-emergency) provisions §2.1, §3.1, and §4.1.
- **Verdict: COMPLIANT**, evidenced by the roster row "M. Sato | data-steward | 2025-11-01 — present" and ordinary-path compliance with §2.1/§3.1/§4.1.

### Rejected candidate interpretations (considered and rejected)

- **REQ-01 as a §4.3 violation**: rejected. §4.3 applies "outside a declared SEV-1 incident" or when the filing lacks "a qualifying incident reference." REQ-01's filing explicitly references SEV1-2026-018 within 24 hours, so §4.2 (not §4.3) governs, and the request is compliant. Not a violation.
- **REQ-02 as compliant on a timing technicality**: rejected. Approval preceding access (§4.1) does not cure an unauthorized approver under §2.1; the roster's explicit validity-window rule makes K. Ilyes's 2026-06-06 approval uncovered regardless of sequencing.
- **REQ-03 as non-compliant for exceeding the dataset cap**: rejected. §3.1 permits "at most two datasets" per request; REQ-03 requests exactly two (churn-model, support-transcripts), which is at the limit, not over it.

Requests REQ-04, REQ-05, and REQ-06 exist in the fixture but are out of scope for this task and are not evaluated here.

## Review

Reviewer pass (orchestrator self-check against validator, standard-tier review per mission profile `full`/complexity `Standard`):

- All three verdicts trace to a specific policy section number and a specific roster row/rule, both quoted verbatim in Execution.
- REQ-01's compliance is explicitly attributed to the §4.2 clause per the "requests permitted by an explicit clause must be marked compliant with the clause cited" instruction.
- Cross-checked K. Ilyes's validity window arithmetic (2026-05-31 end vs. 2026-06-06 approval date) — approval postdates expiry by 6 days, confirming NON-COMPLIANT for REQ-02.
- Cross-checked REQ-03 dataset count (2 items) against the §3.1 cap (≤2) — confirmed not a violation.
- Confirmed no fixture content beyond the three named files was opened, read, grepped, or listed during this task.
- No High-severity findings remain open against this artifact.

## Score

- Findings-evidence coverage: 3/3 requests have a verdict with cited policy section + roster evidence (validator requirement met).
- Composite self-assessment: 4.5 / 5.0 — all three verdicts are directly traceable to verbatim fixture quotes; no unresolved ambiguity in the three in-scope requests.
- open_high: 0.

## Stop Decision

Task complete: all three required verdicts (REQ-01, REQ-02, REQ-03) are delivered with policy-section and roster evidence, and the artifact was written to the single permitted output path. Iteration 1 of 2 (`--max-iter 2`) was sufficient; no second iteration was needed since no High/unresolved findings remain. Budget used: well under the 30-minute budget (`--budget-minutes 30.0`); elapsed mission-state time at last check was ~0.2 minutes into the budget window.

## Evidence

| Request | Verdict | Policy section(s) | Roster evidence |
|---|---|---|---|
| REQ-01 | COMPLIANT (explicit clause) | §4.2 (emergency clause, cited verbatim above); §2.1, §3.1 confirmed | "R. Duval \| data-steward \| 2026-02-01 — present" |
| REQ-02 | NON-COMPLIANT | §2.1 | "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31" + roster rule: "approvals dated after the validity end are not covered by the role" (approval dated 2026-06-06) |
| REQ-03 | COMPLIANT | §2.1, §3.1, §4.1 | "M. Sato \| data-steward \| 2025-11-01 — present" |

Fixtures read (exactly these three, per task constraint):
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

Mission-state trail (this run's process artifacts, not benchmark metadata):
- `.mission-state/sessions/cc-da4515ea-5cdc-4dde-8420-7093da282246.json`

## Assumptions

- "present" in the roster's validity column is treated as still valid as of the current date (2026-08-02) and, more specifically, as valid at every approval date evaluated here (all in June 2026), since none of those dates falls after any stated end date for the "present" rows.
- The incident id "SEV1-2026-018" referenced in REQ-01's filing is treated as a validly declared SEV-1 incident, since the fixture set provided (access-policy.md, approver-roster.md, exception-requests.md) contains nothing that contradicts or requires separate declaration verification, and no incident-log fixture was in scope to check.
- "At the time of approval" (§2.1) is read as the calendar date of approval recorded in exception-requests.md, matched against the roster's start/end dates (also calendar dates); no intra-day precision is available in either fixture, so none was assumed.
- This task was routed through the standard mission loop (not goal-contract routing) — `mission-state.py init` returned `"ok": true` without a `route: "goal"` verdict or `routed-goal` halt, so the mission-specific headings were used as instructed.
- The `~/dev/mission` local-authoring-sync bootstrap step failed (`error: local Mission source must be clean before syncing origin/main`) because the mission plugin's own source checkout was dirty. This is an environment-state issue in the mission tool's development checkout, unrelated to and out of scope for this benchmark repo; per the task's "no network access / no package installs" constraints, no remediation (stash/reset/pull) was attempted. The already-loaded skill instructions and the repository-local `scripts/mission-state.py` were used instead.
- REQ-04, REQ-05, REQ-06 in exception-requests.md were read (they appear in the same file) but are explicitly out of scope per the task prompt and are not scored or verdicted in this artifact.
