# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-std-policy-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition. Task: audit exception requests REQ-01, REQ-02, and REQ-03 against the access policy, citing exact policy sections and roster evidence for each verdict.

# Result

| Request | Verdict | Basis |
|---|---|---|
| REQ-01 | **Compliant** | Emergency clause 4.2 explicitly permits access before approval |
| REQ-02 | **Non-compliant (violation)** | Approver's data-steward role had expired before the approval date |
| REQ-03 | **Compliant** | Approval preceded access, approver valid, scope within limit |

Only REQ-01, REQ-02, and REQ-03 were in scope for this audit (per the task prompt). REQ-04, REQ-05, and REQ-06, which also appear in the exception-requests fixture, were not audited and no verdict is given for them.

## REQ-01 — Compliant

- Fixture facts: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Policy clause 4.2 ("Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant.") is satisfied: access began during a declared SEV-1 incident (SEV1-2026-018), the request was filed the same day (2026-06-03, well within 24 hours of the 02:10 access start), and it references the incident id.
- Approver check (policy 2.1: approval "MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster"): roster entry "R. Duval | data-steward | 2026-02-01 — present" covers the approval date 2026-06-03. Valid.
- Scope check (policy 3.1: "A single exception request MAY grant access to at most two datasets."): 1 dataset (payments-raw). Within limit.
- **Verdict: Compliant, under the explicit emergency clause 4.2.**

## REQ-02 — Non-compliant (violation)

- Fixture facts: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Timing check (policy 4.1: "Approval MUST precede access."): approval (2026-06-06) precedes access (2026-06-07). This part is fine — the emergency clause is not needed here.
- Approver check (policy 2.1, combined with roster note "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."): roster entry "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31." The approval date (2026-06-06) is **after** the validity end date (2026-05-31), so per the roster note this approval is not covered by the role — K. Ilyes did not hold the `data-steward` role at the time of approval.
- Scope check (policy 3.1): 1 dataset (user-profiles). Within limit, but irrelevant given the approver defect.
- **Verdict: Non-compliant — violates policy 2.1 because the approver (K. Ilyes) was not a valid data-steward on the approval date, per the approver-roster validity window and its explicit note that post-expiry approvals are not covered.**

## REQ-03 — Compliant

- Fixture facts: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- Timing check (policy 4.1): approval (2026-06-09) precedes access (2026-06-10). Compliant; no need for the emergency clause.
- Approver check (policy 2.1): roster entry "M. Sato | data-steward | 2025-11-01 — present" covers the approval date 2026-06-09. Valid.
- Scope check (policy 3.1: "at most two datasets"): 2 datasets (churn-model, support-transcripts) — exactly at, not over, the limit. Compliant.
- **Verdict: Compliant — approval preceded access, approver was a valid data-steward, and scope was within the two-dataset limit.**

# Evidence

Fixtures read in full (exactly the three named in the task prompt, no others under `benchmarks/mission-vs-goal/`):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
  - §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
  - §3.1: "A single exception request MAY grant access to at most two datasets. Broader access requires separate requests per dataset pair."
  - §3.2: "Delegation clause: a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted." (not needed for REQ-01/02/03 — all three requests' approvers and requesting context show no cross-team conflict flagged in the fixture; included for completeness since it is an explicit permission clause.)
  - §4.1: "Approval MUST precede access."
  - §4.2: "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
  - §4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
  - "M. Sato | data-steward | 2025-11-01 — present"
  - "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
  - "R. Duval | data-steward | 2026-02-01 — present"
  - "T. Okafor | data-steward | 2026-06-10 — present"
  - "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`
  - REQ-01: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
  - REQ-02: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
  - REQ-03: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed for this task.

# Assumptions

- "Time of approval" for the roster validity check (per policy §2.1 and the roster's validity note) is taken as the approval date stated in each request record; no more granular timestamp was given for approvals (only REQ-01 gives a time-of-day, and only for access, not approval), so date-level comparison was used throughout.
- The roster's closing note ("approvals dated after the validity end are not covered by the role") is read as a hard disqualifier, not a soft warning — this directly drives the REQ-02 non-compliant verdict.
- Delegation clause (§3.2) and REQ-01/03's requesting team are not identified in the fixture text (no team-of-origin field is given for REQ-01/02/03), so no cross-team conflict was found or asserted for these three requests; this is noted as unmeasured/inapplicable rather than assumed compliant or non-compliant on that specific dimension.
- REQ-04, REQ-05, REQ-06 are present in the exception-requests fixture but are explicitly out of scope per the task prompt ("Audit exception requests REQ-01, REQ-02, and REQ-03") and are not evaluated here.

# Stop Condition

This artifact exists at the required path with all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition) and delivers a verdict for each of REQ-01, REQ-02, and REQ-03, each backed by exact policy-section and roster quotes. No commit, push, package install, or network access was performed. No files were read, grepped, or listed under `benchmarks/mission-vs-goal/` other than the three named fixtures and this output file. The task is complete.
