# disc-policy-exceptions — claude_code_goal_command

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v2/disc-policy-exceptions-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit six access exception requests (REQ-01 through REQ-06) against the access policy. Read exactly three fixtures — `access-policy.md`, `approver-roster.md`, `exception-requests.md` — and for every request deliver a verdict (compliant or violation), citing the exact policy section and roster evidence. Requests that look irregular but are permitted by an explicit policy clause must be marked compliant with the permitting clause cited. Every request must receive a verdict.

## Result

All six requests received a verdict. **3 compliant, 3 violations.**

### Verdict table

| Request | Verdict | Policy section(s) | Evidence |
|---|---|---|---|
| REQ-01 | **Compliant** | §4.2 (emergency clause) | Access began 2026-06-03 02:10 during incident `SEV1-2026-018`; filed 2026-06-03 14:00 referencing `SEV1-2026-018` (within 24h); approved by R. Duval (valid data-steward, 2026-02-01–present) 2026-06-03; 1 dataset (`payments-raw`), within the two-dataset cap. |
| REQ-02 | **Violation** | §2.1 (approver role) | Approved by K. Ilyes 2026-06-06. Roster: K. Ilyes validity is `2026-01-15 — 2026-05-31`, and "approvals dated after the validity end are not covered by the role." 2026-06-06 is after 2026-05-31, so the approver did not hold the `data-steward` role at approval time. |
| REQ-03 | **Compliant** | §3.1 (scope cap) | Datasets: `churn-model, support-transcripts` = 2 datasets, exactly at "at most two datasets." Approved by M. Sato (valid, 2025-11-01–present) 2026-06-09, preceding access (2026-06-10). No violation. |
| REQ-04 | **Compliant** | §3.2 (delegation clause) | Filed by growth team, approved by R. Duval "(platform team)" — cross-team approval. Policy: "a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted." R. Duval valid (2026-02-01–present) on approval date 2026-06-12, preceding access (2026-06-13). |
| REQ-05 | **Violation** | §3.1 (scope cap) | Datasets: `payments-raw, user-profiles, campaign-events` = 3 datasets, exceeding "at most two datasets. Broader access requires separate requests per dataset pair." Approver (T. Okafor) validity and approval-before-access timing are both fine; the violation is dataset count alone. |
| REQ-06 | **Violation** | §4.3 (retroactive approval) | Access began 2026-06-19 "(no incident declared)"; filed 2026-06-20 (after access began); approved by M. Sato 2026-06-21. §4.3: "A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval." No incident id is referenced anywhere in the request. |

### Violations section (with quoted evidence)

1. **REQ-02 — unauthorized approver (role expired).**
   - Roster quote: `| K. Ilyes | data-steward | 2026-01-15 — 2026-05-31 |` and "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
   - Request quote: "approved by K. Ilyes 2026-06-06."
   - Policy quote (§2.1): "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."
   - 2026-06-06 falls 6 days after K. Ilyes's validity end (2026-05-31) → approval is not covered by the role → violation of §2.1.

2. **REQ-05 — scope exceeds two-dataset cap.**
   - Request quote: "Datasets: payments-raw, user-profiles, campaign-events." (3 datasets)
   - Policy quote (§3.1): "A single exception request MAY grant access to at most two datasets. Broader access requires separate requests per dataset pair."
   - 3 > 2 → violation of §3.1.

3. **REQ-06 — retroactive approval without a qualifying incident.**
   - Request quote: "Access began 2026-06-19 (no incident declared); request filed 2026-06-20; approved by M. Sato 2026-06-21."
   - Policy quote (§4.3): "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."
   - Access preceded both filing and approval, and the request explicitly states no incident was declared → violation of §4.3, regardless of M. Sato being a valid approver.

### Compliant-but-suspicious section (irregular but permitted by an explicit clause)

1. **REQ-01 — access began before approval.**
   - On its face this looks like a §4.1 violation ("Approval MUST precede access") since access began 2026-06-03 02:10 and approval is dated 2026-06-03 (no earlier time given).
   - Permitting clause — §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."
   - Request quote: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018." Filing (14:00) is ~11h50m after access began (02:10), within the 24-hour window, and the incident id is referenced. → Compliant under §4.2.

2. **REQ-04 — cross-team approval.**
   - On its face this looks irregular: "Filed by the growth team ... approved by R. Duval (platform team)" — an approver from a different team than the requester.
   - Permitting clause — §3.2: "a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted."
   - → Compliant under §3.2.

3. **REQ-03 — request at the maximum dataset count.**
   - Two datasets (`churn-model, support-transcripts`) in a single request could look like it's testing the scope boundary.
   - Permitting clause — §3.1: "at most two datasets" — 2 is the ceiling, not an overage. → Compliant under §3.1 (no violation; included here only because it sits exactly at the limit, not because it required a separate exception).

### Rejected candidates (looked suspicious, not real findings)

- **REQ-02 timing (filed 2026-06-05, approved 2026-06-06 — one day later) is not itself irregular.** The gap between filing and approval is short and unremarkable; the actual defect is the approver's expired role validity (see Violations §1), not the timing gap.
- **REQ-05 approver (T. Okafor) being newly listed on the roster (validity starts 2026-06-10, only 6 days before the 2026-06-16 approval) is not a defect.** T. Okafor's validity window is "2026-06-10 — present," so the approval on 2026-06-16 falls inside the valid window. Approval also precedes access (2026-06-17). The sole defect on REQ-05 is the 3-dataset scope violation (see Violations §2); the approver's recent appointment is not disqualifying under §2.1.
- **REQ-01's approval date having no explicit time stamp (only "2026-06-03") is not treated as a violation.** Even under the strictest reading — that approval could have occurred any time on 2026-06-03, possibly after the 02:10 access start — §4.2's emergency clause explicitly permits access to precede approval during a declared SEV-1 incident, so the ambiguity does not change the verdict.

## Evidence

All verdicts above are sourced directly from the three named fixtures, quoted inline. No information outside `access-policy.md`, `approver-roster.md`, and `exception-requests.md` was used. No claim in this artifact relies on unmeasured or assumed data — every dataset count, date, and role-validity comparison is taken verbatim from the fixture text quoted above.

## Assumptions

- Roster validity comparisons treat the "Validity" end date as inclusive of that calendar date and exclusive thereafter, per the roster's own note: "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role." (K. Ilyes's 2026-05-31 end date means a 2026-06-06 approval is not covered — this is stated by the fixture, not inferred.)
- Where only a date (no time-of-day) is given for an approval and a time-of-day is given for access (REQ-01), the comparison of approval-vs-access ordering is treated as immaterial to the verdict, because §4.2's emergency clause independently permits access before approval in that scenario regardless of exact ordering.
- "Datasets" count is taken as the literal number of comma-separated dataset names listed under each request's "Datasets:" line.
- No fixture data was found ambiguous enough to require marking any field as unmeasured; every field needed for a verdict (approver identity, approval date, access date, filing date, incident reference, dataset list) was explicitly present in `exception-requests.md`.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), provides a verdict for all six requests (REQ-01–REQ-06) in a table with request id/verdict/policy section/evidence columns, includes a Violations section with quoted roster/policy evidence for each of the 3 violations (REQ-02, REQ-05, REQ-06), and includes a Compliant-but-suspicious section citing the permitting clause for each of the 3 compliant-but-irregular requests (REQ-01 §4.2, REQ-04 §3.2, REQ-03 §3.1). No further edits are needed to satisfy the task validator described in this run's instructions.
