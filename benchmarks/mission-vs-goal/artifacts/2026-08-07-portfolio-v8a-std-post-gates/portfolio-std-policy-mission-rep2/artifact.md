# portfolio-std-policy — mission arm (rep2)

Task id: `portfolio-std-policy` / Category: governance / Arm: mission (profile: full) / Complexity: Standard

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3), using exactly the three named fixtures, and deliver a
per-request verdict with exact policy-section and roster evidence. Requests
permitted by an explicit clause must be marked compliant with the clause
cited.

- Mission state: `.mission-state/sessions/cc-68bf24c2-1a39-4f01-9d5f-872b7593fd30.json` (mission_id `f7718a6907ed2bb0`)
- Routing: `init --complexity Standard` returned a normal mission session (no `route: "goal"` verdict), so the full mission loop applies.

## Plan

Inline bounded plan (iteration 1, Standard → `plan-inline` per `next`):

| # | Step | Depends on | Done when |
|---|---|---|---|
| 1 | Read the 3 named fixtures (policy, roster, requests) — nothing else under `benchmarks/mission-vs-goal/` | — | All 3 files read; quoted values available |
| 2 | For each of REQ-01..REQ-03, check approval authority (§2.1 + roster validity), scope (§3.1), timing (§4.1–§4.3) | 1 | Each check has a quoted fixture value |
| 3 | Write verdicts with clause citations; separate confirmed findings from rejected violation candidates | 2 | Artifact has verdicts for all 3 REQs with evidence |
| 4 | Scored review iteration: 2 reviewers in parallel → `review-finalize` → `closeout` | 3 | `passes: true` or documented halt |

Out of scope: REQ-04..REQ-06 (not in the task prompt), any other file under `benchmarks/mission-vs-goal/`, code changes, commits, network.

## Execution

Fixtures read (the only three permitted):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

### Verdicts

#### REQ-01 — COMPLIANT (emergency clause §4.2)

- Fixture facts: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Timing: access preceded approval, which would normally violate §4.1 ("Approval MUST precede access"). However §4.2 (emergency clause) explicitly permits this: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." Filing at 14:00 is 11h50m after access at 02:10 (< 24h) and the request "referencing SEV1-2026-018" satisfies the incident-id condition.
- Approval authority (§2.1): R. Duval is on the roster as `data-steward`, validity "2026-02-01 — present", covering the approval date 2026-06-03.
- Scope (§3.1): 1 dataset (`payments-raw`) ≤ 2 allowed.
- Rejected candidate finding: "retroactive access = violation under §4.3" — rejected because §4.3 applies only "Outside a declared SEV-1 incident", and REQ-01 references declared incident `SEV1-2026-018`, so the explicit §4.2 clause governs and the request must be marked compliant with that clause cited.

#### REQ-02 — VIOLATION (§2.1, approver role expired)

- Fixture facts: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Approval authority (§2.1): approval MUST be "by a person holding the `data-steward` role **at the time of approval**". Roster: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role." The approval date 2026-06-06 is after 2026-05-31, so K. Ilyes did not hold the role at approval time → violation of §2.1.
- Rejected candidate findings: timing is NOT a violation (approval 2026-06-06 precedes access 2026-06-07, satisfying §4.1); scope is NOT a violation (1 dataset ≤ 2, §3.1). The sole confirmed defect is the approver's expired role.

#### REQ-03 — COMPLIANT (§2.1, §3.1, §4.1 all satisfied)

- Fixture facts: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access began 2026-06-10."
- Approval authority (§2.1): roster shows "M. Sato | data-steward | 2025-11-01 — present", covering 2026-06-09.
- Scope (§3.1): "A single exception request MAY grant access to at most two datasets." Two datasets (`churn-model`, `support-transcripts`) is exactly the explicit maximum → permitted, with §3.1 cited as the explicit clause.
- Timing (§4.1): approval 2026-06-09 precedes access 2026-06-10.
- Rejected candidate finding: "two datasets exceeds scope" — rejected; §3.1 permits "at most two datasets", and two is within the limit.

### Summary table

| Request | Verdict | Governing clause(s) | Roster evidence |
|---|---|---|---|
| REQ-01 | Compliant | §4.2 (emergency clause; filed <24h, references SEV1-2026-018), §2.1, §3.1 | R. Duval, data-steward, "2026-02-01 — present" |
| REQ-02 | Violation | §2.1 (approver not holding role at approval time) | K. Ilyes, data-steward, "2026-01-15 — 2026-05-31" vs approval 2026-06-06 |
| REQ-03 | Compliant | §2.1, §3.1 (at most two datasets), §4.1 | M. Sato, data-steward, "2025-11-01 — present" |

## Review

Iteration 1: 2 independent reviewers (perspectives A: mission achievement, B:
accuracy) launched in a single parallel message; reviewer window
`2026-08-07T09:46:28Z..2026-08-07T09:49:39Z` for both (`parallel_execution:
true` per aggregate). Findings: 0 High, 1 Medium (B-1: Evidence-section §4.2
quote missing the "during a declared SEV-1 incident" qualifier), 2 Low (A-1
same quote-truncation, B-2: §4.3 quote missing its first sentence). All three
were fixed inline in this artifact's Evidence section; per M6, a differential
reviewer (perspective `verify`) re-checked and reported B-1: resolved, B-2:
resolved against the fixture text. Raw reviews archived at
`.mission-state/archive/iter-1-f7718a69-reviews.json`. Reviewer B's initial
JSON used a non-schema axis label (`正確性`) and was re-emitted by the same
reviewer as `accuracy` (scores/findings content unchanged).

## Score

From `review-finalize --iteration 1 --min-reviewers 2` (`push-score` output,
archived at `.mission-state/archive/iter-1-f7718a69-scoring.json`):

- composite: 4.5 (threshold 4.0) — pass
- items: mission_achievement 5.0 / accuracy 4.0 / completeness 4.5 / usability 4.5; min item 4.0 (gate 3.5) — pass
- open_high: 0 — pass; findings_evidence_path recorded — pass
- review_agreement 4.0; max per-axis delta 1.0 (gate ≤ 1.5) — pass

## Stop Decision

Iteration 1 met all gates (composite 4.5 ≥ 4.0, min item 4.0 ≥ 3.5, open_high
0, max agreement delta 1.0 ≤ 1.5, findings evidence recorded) with the single
Medium finding fixed and re-verified by a differential reviewer (M6). Early-stop
rule applies (threshold reached at iteration 1 with open_high == 0, composite
above the 4.0–4.3 continue band). `closeout` (`mark-passes` → `next`) exit 0
with `passes: true` — see Evidence. Stopped at iteration 1 of `--max-iter 2`.

## Evidence

- Policy quotes: §2.1 "MUST be approved by a person holding the `data-steward` role at the time of approval"; §3.1 "at most two datasets"; §4.1 "Approval MUST precede access"; §4.2 "Emergency clause: during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant."; §4.3 "Outside a declared SEV-1 incident, retroactive approval is forbidden. A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval."
- Roster quotes: "M. Sato | data-steward | 2025-11-01 — present"; "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"; "R. Duval | data-steward | 2026-02-01 — present"; "Role validity ends on the date listed; approvals dated after the validity end are not covered by the role."
- Request quotes: REQ-01 "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00"; REQ-02 "approved by K. Ilyes 2026-06-06"; REQ-03 "Datasets: churn-model, support-transcripts".
- Mission state evidence: session file `.mission-state/sessions/cc-68bf24c2-1a39-4f01-9d5f-872b7593fd30.json` (mission_id `f7718a6907ed2bb0`); reviews archived at `.mission-state/archive/iter-1-f7718a69-reviews.json`; scoring archived at `.mission-state/archive/iter-1-f7718a69-scoring.json`; reviewer input JSONs at `.mission-state/reviews/iter1-A.json` / `iter1-B.json`. `review-finalize` exit 0 at 2026-08-07T09:51:53Z; `closeout` exit 0 (`passes: true`, `next_action: report-complete`).
- Unmeasured: wall-clock duration and token cost of this run are not measured by this artifact.

## Assumptions

- `MISSION_PLUGIN_ROOT` points to `~/dev/mission` (local authoring), but the benchmark forbids network access, so the local-authoring sync script (which fetches from remote) was intentionally skipped; the in-repo `scripts/mission-state.py` is used as the authoritative CLI. This is a benchmark-rule-driven deviation, recorded here fail-open only for state CLI choice, not for gates.
- REQ-04..REQ-06 exist in the requests fixture but are outside the audited set per the task prompt (REQ-01..REQ-03 only); they were not audited.
- "2026-06-06 > 2026-05-31" role expiry uses the roster's own rule ("approvals dated after the validity end are not covered"); no timezone ambiguity is material at day granularity.
- The three fixture files are treated as the complete and authoritative evidence base; no external systems were consulted (network forbidden).
