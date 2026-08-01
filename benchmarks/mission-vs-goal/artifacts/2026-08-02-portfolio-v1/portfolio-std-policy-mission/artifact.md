# portfolio-std-policy-mission

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access Exception Policy (v3). Deliver a compliant/non-compliant verdict for each request with exact policy section and roster evidence quoted. Permitted requests must cite the applicable clause; violations must identify the specific breach.

Fixtures read:
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

## Plan

| # | Action | Input | Output | Done condition |
|---|---|---|---|---|
| 1 | Read all three fixtures | File paths | Raw content | All three files successfully read |
| 2 | Extract policy rules per section | access-policy.md | Rule table (2.1, 3.1, 3.2, 4.1–4.3) | All sections enumerated |
| 3 | Resolve approver validity for each request date | approver-roster.md + request dates | Valid/invalid per approver per date | Roster check complete for REQ-01/02/03 |
| 4 | Apply each rule to each request | Rules + request facts | Per-request finding list | All checks recorded |
| 5 | Render verdicts with quoted evidence | Finding list | Compliant/Non-compliant with citations | Artifact written |

## Execution

### Policy rules extracted

| Section | Rule |
|---|---|
| 2.1 | Request MUST be approved by a person holding the `data-steward` role **at the time of approval**, as recorded in the approver roster. |
| 3.1 | A single request MAY grant access to at most **two datasets**. |
| 3.2 | A `data-steward` MAY approve requests from any team (cross-team approval explicitly permitted). |
| 4.1 | Approval MUST precede access. |
| 4.2 | During a declared SEV-1 incident, access MAY begin before approval provided the request is filed within 24 hours and references the incident id. Such requests are compliant. |
| 4.3 | Outside a declared SEV-1 incident, retroactive approval is forbidden. |

### Approver validity at relevant dates

| Person | Role | Validity period | Valid on 2026-06-03? | Valid on 2026-06-06? | Valid on 2026-06-09? |
|---|---|---|---|---|---|
| M. Sato | data-steward | 2025-11-01 — present | Yes | Yes | Yes |
| K. Ilyes | data-steward | 2026-01-15 — **2026-05-31** | **No** (role expired 2026-05-31) | **No** (role expired) | No |
| R. Duval | data-steward | 2026-02-01 — present | Yes | Yes | Yes |
| T. Okafor | data-steward | 2026-06-10 — present | No | No | No |

### Per-request checks

#### REQ-01

Facts:
- Access began 2026-06-03 02:10 during incident SEV1-2026-018
- Request filed 2026-06-03 14:00 referencing SEV1-2026-018
- Approved by R. Duval 2026-06-03
- Datasets: payments-raw (1 dataset)

| Rule | Check | Result |
|---|---|---|
| 2.1 | R. Duval holds data-steward from 2026-02-01 — present; approval on 2026-06-03 is within validity | Pass |
| 3.1 | 1 dataset (payments-raw); limit is 2 | Pass |
| 4.1 + 4.2 | Access preceded filing and approval, but incident SEV1-2026-018 was declared; request filed at 14:00 same day (~12 hours after 02:10 access start, within 24-hour window); incident id referenced | Pass — emergency clause §4.2 applies |

#### REQ-02

Facts:
- Filed 2026-06-05
- Approved by K. Ilyes 2026-06-06
- Access began 2026-06-07
- Datasets: user-profiles (1 dataset)

| Rule | Check | Result |
|---|---|---|
| 2.1 | K. Ilyes data-steward validity ends 2026-05-31; approval dated 2026-06-06 is 6 days after validity end | **Fail** |
| 3.1 | 1 dataset; limit is 2 | Pass |
| 4.1 | Approval (2026-06-06) preceded access (2026-06-07) | Pass |

#### REQ-03

Facts:
- Filed 2026-06-09
- Approved by M. Sato 2026-06-09
- Access began 2026-06-10
- Datasets: churn-model, support-transcripts (2 datasets)

| Rule | Check | Result |
|---|---|---|
| 2.1 | M. Sato holds data-steward from 2025-11-01 — present; approval on 2026-06-09 is within validity | Pass |
| 3.1 | 2 datasets (churn-model, support-transcripts); limit is 2 | Pass |
| 4.1 | Approval (2026-06-09) preceded access (2026-06-10) | Pass |

## Review

Confirmed findings:

- **REQ-01 COMPLIANT**: R. Duval was a valid data-steward on 2026-06-03 (Section 2.1). One dataset within scope (Section 3.1). Although access preceded approval, incident SEV1-2026-018 was declared and the request was filed within 24 hours referencing the incident id, satisfying the emergency clause (Section 4.2).

- **REQ-02 NON-COMPLIANT**: K. Ilyes' data-steward role expired 2026-05-31 (roster: "2026-01-15 — 2026-05-31"). The approval dated 2026-06-06 falls outside that validity window. Section 2.1 requires the approver to hold the `data-steward` role "at the time of approval, as recorded in the approver roster." This condition is not met. No other registered data-steward countersigned.

- **REQ-03 COMPLIANT**: M. Sato was a valid data-steward on 2026-06-09 (Section 2.1). Two datasets granted in a single request matches the maximum allowed (Section 3.1). Approval preceded access by one day (Section 4.1).

Rejected candidates (not confirmed findings):

- REQ-02 cross-team argument: Section 3.2 permits cross-team approval by a valid data-steward, but does not override Section 2.1's requirement that the approver hold the role at the time of approval. K. Ilyes did not hold the role on 2026-06-06, so Section 3.2 does not rescue REQ-02.
- REQ-03 dataset count as over-limit: Two datasets equals the maximum stated in Section 3.1 ("at most two datasets"); it is not a violation.

## Score

| Dimension | Rating | Notes |
|---|---|---|
| Policy coverage | 5/5 | All applicable sections checked for each request |
| Verdict accuracy | 5/5 | Each verdict matches the policy rules and roster facts |
| Evidence citation | 5/5 | Policy text and roster dates quoted directly |
| Compliant/non-compliant separation | 5/5 | Clear per-request verdicts |
| Rejected candidates separated | 5/5 | Two candidate findings explicitly rejected with reasoning |

**Overall: 5.0 / 5.0**

## Stop Decision

All three requests audited; verdicts reached with direct policy-section and roster citations. No further iteration required.

## Evidence

### Approver roster (verbatim excerpt)

```
| M. Sato | data-steward | 2025-11-01 — present |
| K. Ilyes | data-steward | 2026-01-15 — 2026-05-31 |
| R. Duval | data-steward | 2026-02-01 — present |
| T. Okafor | data-steward | 2026-06-10 — present |

Role validity ends on the date listed; approvals dated after the validity
end are not covered by the role.
```

### Policy sections directly applied

**Section 2.1** (approver must hold role at time of approval):
> "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster."

**Section 3.1** (dataset count limit):
> "A single exception request MAY grant access to at most two datasets."

**Section 3.2** (cross-team delegation):
> "Delegation clause: a `data-steward` MAY approve requests originating from any team, not only their own. Cross-team approval is explicitly permitted."

**Section 4.2** (emergency clause — basis of REQ-01 compliance):
> "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."

**Section 4.3** (retroactive approval forbidden outside SEV-1):
> "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."

### Request facts applied

**REQ-01**: Access 2026-06-03 02:10 (incident SEV1-2026-018 declared); filed 2026-06-03 14:00 referencing SEV1-2026-018; approved R. Duval 2026-06-03; dataset: payments-raw.

**REQ-02**: Filed 2026-06-05; approved K. Ilyes 2026-06-06; access 2026-06-07; dataset: user-profiles.

**REQ-03**: Filed 2026-06-09; approved M. Sato 2026-06-09; access 2026-06-10; datasets: churn-model, support-transcripts.

## Assumptions

- "present" in the approver roster is interpreted relative to the request dates in scope (all in June 2026); no assumption about future roster changes is made.
- "at the time of approval" in Section 2.1 means the calendar date of approval, matched against the roster validity range (inclusive on both ends).
- The 24-hour window in Section 4.2 is measured from the moment access began (02:10) to the moment the request was filed (14:00), both on 2026-06-03 — approximately 12 hours, within the 24-hour limit.
- No assumption is made about the content or scope of the declared incident SEV1-2026-018 beyond what is stated in REQ-01; the fixture states the incident was declared, which is taken at face value.
