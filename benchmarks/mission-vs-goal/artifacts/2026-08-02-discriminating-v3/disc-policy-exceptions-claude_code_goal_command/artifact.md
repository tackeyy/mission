# Goal

Audit six access exception requests (REQ-01 through REQ-06) against the Data Access Exception Policy (v3), citing the approver roster and exact policy sections, and deliver a verdict (compliant / violation) for every request. This artifact is the sole deliverable; no commits, pushes, package installs, or network access were used to produce it.

## Result

All six requests received a verdict. Three are violations (REQ-02, REQ-05, REQ-06); three are compliant (REQ-01, REQ-03, REQ-04). Two of the compliant requests (REQ-01, REQ-04) look irregular on first read but are explicitly permitted by named policy clauses (4.2 emergency clause, 3.2 delegation clause).

### Verdict table

| Request | Verdict | Policy Section | Evidence |
|---|---|---|---|
| REQ-01 | Compliant | 4.2 (Emergency clause) | Access began 2026-06-03 02:10 during incident SEV1-2026-018; "request filed 2026-06-03 14:00 referencing SEV1-2026-018" — filed same day (<24h), references incident id. Approved by R. Duval (roster: "R. Duval \| data-steward \| 2026-02-01 — present") on 2026-06-03, within validity. Datasets: payments-raw (1, ≤2 per 3.1). |
| REQ-02 | Violation | 2.1 | Approved by K. Ilyes on 2026-06-06. Roster: "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31." Approval date is after the role validity end date, so K. Ilyes did not hold the data-steward role "at the time of approval" as required by 2.1. |
| REQ-03 | Compliant | 2.1, 3.1, 4.1 | "Filed 2026-06-09; approved by M. Sato 2026-06-09... Access began 2026-06-10." Approval precedes access (4.1). Roster: "M. Sato \| data-steward \| 2025-11-01 — present" — valid on 2026-06-09 (2.1). Datasets: churn-model, support-transcripts = 2, at the 3.1 limit of "at most two datasets," not over it. |
| REQ-04 | Compliant | 3.2 (Delegation clause) | "Filed by the growth team 2026-06-12; approved by R. Duval (platform team) 2026-06-12." Cross-team approval is explicitly permitted by 3.2: "a `data-steward` MAY approve requests originating from any team, not only their own." R. Duval is valid on roster (2026-02-01 — present). Approval precedes access (2026-06-12 approval vs. 2026-06-13 access, per 4.1). Datasets: campaign-events (1, ≤2). |
| REQ-05 | Violation | 3.1 | Datasets: "payments-raw, user-profiles, campaign-events" = 3 datasets in one request. Policy 3.1: "A single exception request MAY grant access to at most two datasets. Broader access requires separate requests per dataset pair." Three datasets in one request exceeds the two-dataset cap. |
| REQ-06 | Violation | 4.3 | "Access began 2026-06-19 (no incident declared); request filed 2026-06-20; approved by M. Sato 2026-06-21." Access preceded filing with no incident reference. Policy 4.3: "A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval." |

### Violations section (quoted evidence)

- **REQ-02 — violates 2.1.** Policy 2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster." Roster entry: "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31" and "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role." Request text: "approved by K. Ilyes 2026-06-06." 2026-06-06 falls after the 2026-05-31 validity end, so the approval is not covered by the role — a violation of 2.1.
- **REQ-05 — violates 3.1.** Policy 3.1: "A single exception request MAY grant access to at most two datasets." Request text: "Datasets: payments-raw, user-profiles, campaign-events" — three datasets under one request id (REQ-05), exceeding the two-dataset cap.
- **REQ-06 — violates 4.3.** Policy 4.3: "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval." Request text: "Access began 2026-06-19 (no incident declared); request filed 2026-06-20." Access began before filing, and the request explicitly states no incident was declared, so the 4.2 emergency exception does not apply.

### Compliant-but-suspicious section (permitting clause cited)

- **REQ-01** — Access began (2026-06-03 02:10) before approval was recorded (2026-06-03, exact time not given), which superficially resembles the retroactive-approval violation pattern in REQ-06. However, this is explicitly permitted by **policy 4.2**, the Emergency clause: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." Request text confirms both conditions: incident id "SEV1-2026-018" is referenced, and the request was "filed 2026-06-03 14:00," well within 24 hours of the 02:10 access start on the same day.
- **REQ-04** — The approver, R. Duval, is described as being on the "platform team" while the request was "filed by the growth team," which superficially resembles an unauthorized out-of-team approval. However, this is explicitly permitted by **policy 3.2**, the Delegation clause: "a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted." R. Duval holds the data-steward role continuously from 2026-02-01, covering the 2026-06-12 approval date.

## Evidence

All findings above are quoted directly from the three fixtures read for this task:
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed as part of this audit (no task definitions, scoring configuration, or answer keys were accessed).

### Rejected candidates (looked suspicious but are not real findings)

- **REQ-01 flagged as a potential 4.3 violation** — Access preceding approval/filing looks identical in shape to the REQ-06 violation. Rejected because the request explicitly references incident id "SEV1-2026-018" and was filed same-day (within 24h), which are exactly the two conditions policy 4.2 requires to make pre-approval access compliant. This is a genuine emergency-clause case, not an oversight.
- **REQ-04 flagged as a potential 2.1/authority violation** — The approver's team ("platform team") differs from the requesting team ("growth team"), which could look like an out-of-scope approval. Rejected because policy 3.2 explicitly names cross-team approval as permitted, and R. Duval's roster validity (2026-02-01 — present) covers the 2026-06-12 approval date with no gap.
- **REQ-03 flagged as a potential 3.1 violation** — Two datasets (churn-model, support-transcripts) in one request could be misread as "broader access." Rejected because 3.1 caps requests at "at most two datasets," and two is the maximum allowed, not an excess — this is compliant, not a violation.
- **REQ-05 approver (T. Okafor) checked for a 2.1 timing issue** — T. Okafor's roster validity only starts 2026-06-10 ("T. Okafor \| data-steward \| 2026-06-10 — present"), and the approval date is 2026-06-16, which is after the start date, so the approver was validly in role. This candidate was rejected as a 2.1 issue; REQ-05's actual violation is the unrelated 3.1 dataset-count breach documented above.

## Assumptions

- "At the time of approval" (2.1) is evaluated against the calendar date given for the approval action in each request; no time-of-day granularity was available for approval timestamps (only REQ-01 and REQ-06 give times, and those are for access/filing, not approval), so date-level comparison against roster validity date ranges was used throughout.
- Roster validity ranges are treated as inclusive of both boundary dates (e.g., an approval dated exactly on a role's start or end date is considered within validity), consistent with the roster's own phrasing ("2026-01-15 — 2026-05-31", "approvals dated after the validity end are not covered").
- 3.1's phrasing "at most two datasets" is read as a maximum of two (i.e., exactly two is compliant, three or more is a violation) — this determines the REQ-03 (2 datasets, compliant) vs. REQ-05 (3 datasets, violation) split.
- REQ-01's approval date is given only as "2026-06-03" with no time; it is assumed to have occurred on or after the 02:10 access start and is treated as satisfying 4.2 regardless of intra-day ordering, since 4.2's compliance conditions (filed within 24h, incident referenced) do not depend on the approval timestamp itself.
- No other information was unmeasured or inaccessible for this task — all six requests, the full policy text, and the full roster were available in the three named fixtures.

## Stop Condition

This artifact satisfies the Goal: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), provides a verdict-table covering all six requests (request id, verdict, policy section, evidence), a violations section with quoted roster/policy evidence, and a compliant-but-suspicious section citing the permitting clause for each of the two irregular-but-compliant requests. No commits, pushes, installs, or network access were performed; only the three named fixture files and this output file were read or written. Work on this task is complete.
