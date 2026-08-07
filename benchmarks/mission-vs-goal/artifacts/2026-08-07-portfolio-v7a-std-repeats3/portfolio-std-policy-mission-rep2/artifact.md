# portfolio-std-policy — mission arm (rep2)

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3), using exactly three fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

Deliver a verdict per request with exact policy-section and roster evidence.
Requests permitted by an explicit clause must be marked compliant with the
clause cited. Complexity: Standard. Arm: mission (full profile, --max-iter 2).

Mission state: `.mission-state/sessions/cc-18535bb7-b9f4-4090-9fdc-9c4e274e9ac5.json`
(mission_id `515b6b03404c4e1e`, permission preflight passed at init).

## Plan

Inline bounded plan (iteration 1, Standard → `next` returned `plan-inline`, #339):

1. Read the three named fixtures only (done in planning turn; no other
   `benchmarks/mission-vs-goal/` file opened). — no dependencies
2. For each of REQ-01..REQ-03, check four dimensions against the policy:
   (a) approver holds `data-steward` at approval date per roster (§2.1),
   (b) dataset count ≤ 2 (§3.1), (c) approval precedes access (§4.1) or
   qualifies under the SEV-1 emergency clause (§4.2/§4.3), (d) cross-team
   delegation if relevant (§3.2). — depends on 1
3. Write verdicts with quoted fixture evidence into this artifact; separate
   compliant requests from confirmed violations. — depends on 2
4. Run one scored review iteration: 2 reviewers in parallel (Standard),
   `review-finalize`, then `closeout` (mark-passes gate). — depends on 3

Completion condition: artifact contains verdicts for REQ-01, REQ-02, REQ-03
with policy/roster evidence; mission gate `passes=true` or documented halt.

## Execution

Audit matrix (evidence quoted verbatim from fixtures):

### REQ-01 — COMPLIANT (explicit clause §4.2)

- Request: "Access began 2026-06-03 02:10 during incident SEV1-2026-018;
  request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by
  R. Duval 2026-06-03. Datasets: payments-raw."
- Timing: access preceded approval, but policy §4.2 "Emergency clause: during
  a declared SEV-1 incident, access MAY begin before approval, provided the
  request is filed within 24 hours of access and references the incident id.
  Such requests are compliant." Filing at 14:00 is 11h50m after 02:10 access
  (< 24h) and references `SEV1-2026-018` → clause satisfied.
- Approver: roster "R. Duval | data-steward | 2026-02-01 — present" → held
  the role on 2026-06-03 (§2.1 satisfied).
- Scope: 1 dataset (`payments-raw`) ≤ 2 (§3.1 satisfied).
- Verdict: **compliant**, explicitly permitted by §4.2.

### REQ-02 — VIOLATION (§2.1, roster validity)

- Request: "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets:
  user-profiles. Access began 2026-06-07."
- Approver: roster "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and
  roster note "Role validity ends on the date listed; approvals dated after
  the validity end are not covered by the role." Approval date 2026-06-06 is
  after 2026-05-31, so K. Ilyes did not hold `data-steward` at approval time.
- Policy §2.1: "An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster." → not satisfied.
- Other dimensions pass (1 dataset ≤ 2 per §3.1; approval 2026-06-06 precedes
  access 2026-06-07 per §4.1) but do not cure the approver defect.
- Verdict: **violation** of §2.1 (approver's roster validity had ended).

### REQ-03 — COMPLIANT (§3.1 explicit two-dataset allowance)

- Request: "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets:
  churn-model, support-transcripts. Access began 2026-06-10."
- Scope: 2 datasets; policy §3.1 "A single exception request MAY grant access
  to at most two datasets." → exactly at the explicit limit, permitted.
- Approver: roster "M. Sato | data-steward | 2025-11-01 — present" → valid on
  2026-06-09 (§2.1 satisfied).
- Timing: approval 2026-06-09 precedes access 2026-06-10 (§4.1 "Approval MUST
  precede access." satisfied).
- Verdict: **compliant**, permitted by §3.1 (scope clause) with §2.1/§4.1 met.

Cross-team delegation (§3.2) note: none of REQ-01..REQ-03 states a filing
team different from the approver's team, so §3.2 "a `data-steward` MAY
approve requests originating from any team" is not triggered for any of the
three audited requests (it would in any case permit, not forbid).

### Confirmed findings vs rejected candidates

- Confirmed violation: **REQ-02** (§2.1 — approval by K. Ilyes dated
  2026-06-06, after roster validity end 2026-05-31).
- Rejected violation candidates (audited, found compliant):
  - **REQ-01**: pre-approval access is not a violation because §4.2 emergency
    clause applies (SEV1-2026-018 referenced, filed within 24h).
  - **REQ-03**: two datasets is not a violation because §3.1 explicitly allows
    "at most two datasets".
- Out of audit scope: REQ-04, REQ-05, REQ-06 appear in the fixture but the
  task scopes the audit to REQ-01..REQ-03; no verdicts issued for them.

## Review

Iteration 1: two independent reviewers spawned in parallel in a single
message (window 2026-08-07T06:54:33Z..2026-08-07T06:59:47Z per perspective).

- Perspective `accuracy`: score 5.0 across all four axes; 0 findings; all
  quoted evidence verified verbatim against the fixtures; all three verdicts
  confirmed correct (JSON: `.mission-state/review-iter1-accuracy.json`).
- Perspective `completeness`: mission_achievement 4.5, accuracy 5.0,
  completeness 4.5, usability 4.5; 1 Low finding (`completeness-01`: the §3.2
  cross-team delegation check was in the Plan but not documented per request
  in Execution — addressed by the §3.2 note added above); 0 High, 0 Medium
  (JSON: `.mission-state/review-iter1-completeness.json`).
- Raw reviews archived at
  `.mission-state/archive/iter-1-515b6b03-reviews.json`.
- Note: both reviewers' first JSON output failed the `mission-review/1`
  schema validator (missing per-axis `scores`); each reviewer rewrote their
  own file to the required schema (judgments unchanged) before aggregation.

## Score

`review-finalize --iteration 1 --min-reviewers 2` (tool-computed, recorded in
mission state; evidence:
`.mission-state/archive/iter-1-515b6b03-scoring.json`):

- composite: **4.81** (threshold 4.0) — mission_achievement 4.75,
  accuracy 5.0, completeness 4.75, usability 4.75; min item 4.75 (gate ≥ 3.5)
- open_high: 0; max agreement delta: 0.5 (gate ≤ 1.5); review_agreement 5.0
- findings evidence path recorded (gate satisfied)

## Stop Decision

`closeout` exit 0 at iteration 1: `mark_passes {ok: true, passes: true,
forced: false}`, `next_action: report-complete`, `loop_active: false`,
`phase: done`. Early-stop at iteration 1 is per rule: threshold reached
(4.81 ≥ 4.0) and `open_high == 0`; the single Low finding was fixed inline
(§3.2 note) and does not block pass. `--max-iter 2` not exhausted; no
`halt_reason`. Stop: mission passed.

## Evidence

- Fixtures read (only files opened under `benchmarks/mission-vs-goal/`):
  the three fixture paths listed under Mission, plus this output file.
- All quoted strings above are verbatim from the fixtures (identifiers:
  `REQ-01`, `REQ-02`, `REQ-03`, `SEV1-2026-018`, `payments-raw`,
  `user-profiles`, `churn-model`, `support-transcripts`; roster rows for
  R. Duval, K. Ilyes, M. Sato; policy sections §2.1, §3.1, §3.2, §4.1–§4.3).
- Mission state evidence: init JSON returned
  `"permission_preflight": "passed"`, session
  `cc-18535bb7-b9f4-4090-9fdc-9c4e274e9ac5`; scoring evidence at
  `.mission-state/archive/iter-1-515b6b03-scoring.json` (composite 4.81,
  open_high 0), reviews at
  `.mission-state/archive/iter-1-515b6b03-reviews.json`; score_history shows
  one scored iteration (iteration 1, score_source "scoring-json").
- Specialists: selected [] / used [] / degraded [] / unselected-manual []
  (`specialists summary --json`); task_profile.primary recorded as
  `documentation` via `specialists recommend --record-state` (no external
  specialist used; benchmark forbids extra reads/network).
- The §3.2 note and the Review/Score/Stop Decision sections were the only
  post-review edits to this artifact; verdicts and evidence are unchanged
  from the reviewed version.
- Unmeasured: wall-clock duration and token cost of this run are not measured
  by this artifact.

## Assumptions

- `MISSION_PLUGIN_ROOT` points to a local authoring checkout, but the
  benchmark forbids network access, so
  `mission-local-authoring-sync.sh` (which fetches) was not run; the
  repository-root `scripts/mission-state.py` in this checkout is used as the
  authoritative CLI. Fail-closed sync was intentionally skipped per benchmark
  rule "no network access".
- REQ-04..REQ-06 are treated as out of scope because the task prompt limits
  the audit to REQ-01..REQ-03.
- "K. Ilyes | 2026-01-15 — 2026-05-31" is interpreted per the roster's own
  note: validity ends 2026-05-31 inclusive, so a 2026-06-06 approval is not
  covered. No contrary evidence exists in the fixtures.
- Timestamps lacking timezones are compared as given (same-fixture, same
  convention); this does not change any verdict.
