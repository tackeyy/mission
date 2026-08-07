# portfolio-std-policy — Access Policy Exception Audit (rep2, arm: claude_code_goal_command)

## Goal

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3), delivering a verdict for each request with the exact policy section and approver-roster evidence. Requests permitted by an explicit clause are marked compliant with the clause cited.

Fixtures read (exactly these three):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

## Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **COMPLIANT** | Emergency clause §4.2 explicitly permits pre-approval access during a declared SEV-1 incident; approval §2.1 satisfied (R. Duval was a valid data-steward). |
| REQ-02 | **VIOLATION** | §2.1 — approver K. Ilyes did not hold the `data-steward` role at the time of approval (2026-06-06); roster validity ended 2026-05-31. |
| REQ-03 | **COMPLIANT** | §2.1 satisfied (M. Sato valid data-steward), §4.1 satisfied (approval 2026-06-09 precedes access 2026-06-10), §3.1 satisfied (exactly two datasets, the permitted maximum). |

### Confirmed findings (verdict details)

**REQ-01 — COMPLIANT (explicit clause: §4.2 Emergency clause).**
Access began before approval (`Access began 2026-06-03 02:10`), which would normally violate §4.1 ("Approval MUST precede access"). However, §4.2 states: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." All three conditions hold: access occurred "during incident SEV1-2026-018"; the request was "filed 2026-06-03 14:00" (11h50m after 02:10 access, within 24 hours); and it was filed "referencing SEV1-2026-018". Approval requirement §2.1 is also met: approver R. Duval is on the roster as `data-steward` with validity "2026-02-01 — present", covering the approval date 2026-06-03. Scope §3.1 is met (one dataset: `payments-raw`, ≤ 2).

**REQ-02 — VIOLATION (§2.1 Approval).**
§2.1 requires approval "by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster." REQ-02 was "approved by K. Ilyes 2026-06-06", but the roster lists K. Ilyes with validity "2026-01-15 — 2026-05-31", and the roster states "approvals dated after the validity end are not covered by the role." The approval date 2026-06-06 is after 2026-05-31, so the approval is invalid. Timing (§4.1: filed 2026-06-05, approved 2026-06-06, access began 2026-06-07 — approval precedes access) and scope (§3.1: one dataset, `user-profiles`) are otherwise fine; the sole violation is the invalid approver.

**REQ-03 — COMPLIANT.**
Approval §2.1: "approved by M. Sato 2026-06-09"; roster lists M. Sato as `data-steward` with validity "2025-11-01 — present", covering 2026-06-09. Timing §4.1: approval 2026-06-09 precedes "Access began 2026-06-10". Scope §3.1: "A single exception request MAY grant access to at most two datasets"; REQ-03 grants exactly two ("churn-model, support-transcripts"), which is within the explicit limit.

### Rejected candidates (violation hypotheses considered and rejected)

- **REQ-01 as a §4.1/§4.3 timing violation — rejected.** Although access preceded approval, §4.3's retroactive-approval prohibition applies only "Outside a declared SEV-1 incident" and to requests "without a qualifying incident reference". REQ-01 explicitly references `SEV1-2026-018` and was filed within 24 hours, so the §4.2 emergency clause governs and the request "is compliant" by the policy's own wording.
- **REQ-03 as a §3.1 scope violation — rejected.** Two datasets ("churn-model, support-transcripts") equals, but does not exceed, the §3.1 maximum of "at most two datasets".
- **REQ-02 as compliant via later/other approval — rejected.** No other approver is recorded for REQ-02 in the fixture, and the roster note ("approvals dated after the validity end are not covered by the role") forecloses treating K. Ilyes's 2026-06-06 approval as valid.

## Evidence

Policy citations (from `access-policy.md`):

- §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
- §3.1: "A single exception request MAY grant access to at most two datasets."
- §4.1: "Approval MUST precede access."
- §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden."

Roster citations (from `approver-roster.md`):

- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."

Request citations (from `exception-requests.md`):

- REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

## Assumptions

- The audit scope is exactly REQ-01, REQ-02, and REQ-03 as instructed. REQ-04, REQ-05, and REQ-06 appear in the fixture but are out of scope and were not audited (their compliance status is unmeasured here).
- "Filed within 24 hours of access" (§4.2) is computed from the timestamps given in the fixture: 2026-06-03 02:10 → 2026-06-03 14:00 is 11 hours 50 minutes. No timezone information is given in the fixtures; same-day timestamps are assumed to share a timezone.
- The approver roster is assumed complete and authoritative for `data-steward` role holders, per §2.1 ("as recorded in the approver roster").
- Approval dates are given without time of day; K. Ilyes's approval on 2026-06-06 is after the 2026-05-31 validity end under any time-of-day reading, so the REQ-02 verdict is insensitive to this ambiguity.
- No policy sections other than those quoted in the fixture (§2.1, §3.1, §3.2, §4.1, §4.2, §4.3) were considered, since the fixture contains only these.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-policy-claude_code_goal_command-rep2.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition) plus verdicts for REQ-01, REQ-02, and REQ-03, each backed by exact policy-section and roster quotations. Only the three named fixture files were read; no other files under `benchmarks/mission-vs-goal/` were opened. No commits, pushes, package installs, or network access were performed. Run controls beyond artifact content (e.g., wall-clock time, token usage) are unmeasured. Task complete; stopping.
