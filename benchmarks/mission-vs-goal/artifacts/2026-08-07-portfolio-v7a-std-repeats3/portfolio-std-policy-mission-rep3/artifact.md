# portfolio-std-policy — mission arm (rep3)

- Task id: `portfolio-std-policy` / Category: governance / Arm: mission / Profile: full
- Date: 2026-08-07
- Mission session: `cc-be71d742-5e6b-4ccf-8d6f-a6a2d25fbee0` (mission_id `11d64d2748c08ae9`)

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the Data Access
Exception Policy (v3), using exactly the three named fixtures, and deliver a
verdict per request with exact policy-section and roster evidence. Requests
permitted by an explicit clause must be marked compliant with the clause cited.

## Plan

Inline bounded plan (Standard, iteration 1, per #339 `plan-inline`):

| Step | Action | Depends on | Done when |
|---|---|---|---|
| 1 | `mission-state.py init` (Standard, no goal routing) | — | active session file exists |
| 2 | Read the 3 named fixtures (parallel, no other benchmark files) | 1 | all 3 fixture contents captured |
| 3 | Audit REQ-01..REQ-03 on three checks: approver role validity (§2.1), dataset scope (§3.1), timing (§4.1–4.3) | 2 | verdict + clause + roster evidence per request |
| 4 | Write this artifact with all 8 required headings | 3 | file exists with verdicts |
| 5 | Scored review iteration: 2 reviewers in parallel → `review-finalize` (aggregate-reviews + push-score) → `closeout` (mark-passes + next) | 4 | `passes: true` or halt with reason |

Completion condition: artifact contains verdicts for REQ-01/02/03 with
policy/roster evidence, and one scored review iteration is recorded in
mission state.

## Execution

Read fixtures (only these three files under `benchmarks/mission-vs-goal/`):

- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

### Verdicts (confirmed findings)

#### REQ-01 — COMPLIANT (emergency clause §4.2)

- Fixture facts (exception-requests.md): "Access began 2026-06-03 02:10 during
  incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing
  SEV1-2026-018; approved by R. Duval 2026-06-03. Datasets: payments-raw."
- Timing: access preceded approval, but policy §4.2 (Emergency clause) states
  "during a declared SEV-1 incident, access MAY begin before approval,
  provided the request is filed within 24 hours of access and references the
  incident id. Such requests are compliant." Filing at 14:00 is 11h50m after
  access at 02:10 (< 24h) and references incident id `SEV1-2026-018` → clause
  satisfied.
- Approver (§2.1): R. Duval is on the roster as `data-steward`, validity
  "2026-02-01 — present", covering the approval date 2026-06-03.
- Scope (§3.1): 1 dataset (`payments-raw`) ≤ 2 allowed.
- Verdict: compliant, explicitly permitted by §4.2; approval valid under §2.1.

#### REQ-02 — VIOLATION (§2.1 approver role expired)

- Fixture facts (exception-requests.md): "Filed 2026-06-05; approved by
  K. Ilyes 2026-06-06. Datasets: user-profiles. Access began 2026-06-07."
- Approver (§2.1): policy requires approval "by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster." Roster: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31", and
  the roster states "approvals dated after the validity end are not covered
  by the role." Approval date 2026-06-06 is after 2026-05-31 → §2.1 violated.
- Timing (§4.1): approval 2026-06-06 precedes access 2026-06-07 — satisfied.
- Scope (§3.1): 1 dataset (`user-profiles`) ≤ 2 — satisfied.
- Verdict: violation of §2.1 (approver did not hold the data-steward role on
  the approval date, per roster validity `2026-01-15 — 2026-05-31`).

#### REQ-03 — COMPLIANT (§2.1, §3.1, §4.1 all satisfied)

- Fixture facts (exception-requests.md): "Filed 2026-06-09; approved by
  M. Sato 2026-06-09. Datasets: churn-model, support-transcripts. Access
  began 2026-06-10."
- Approver (§2.1): roster "M. Sato | data-steward | 2025-11-01 — present"
  covers approval date 2026-06-09.
- Scope (§3.1): "A single exception request MAY grant access to at most two
  datasets." Two datasets (`churn-model`, `support-transcripts`) = 2 ≤ 2 —
  explicitly permitted by §3.1.
- Timing (§4.1): approval 2026-06-09 precedes access start 2026-06-10.
- Verdict: compliant (§2.1 valid approver, §3.1 scope within the explicit
  two-dataset allowance, §4.1 approval precedes access).

### Rejected candidates (out of audit scope)

The fixture `exception-requests.md` also lists REQ-04, REQ-05, and REQ-06.
These are rejected as audit candidates for this task: the task prompt limits
the audit to REQ-01, REQ-02, and REQ-03. No verdict is issued for
REQ-04/REQ-05/REQ-06 (their compliance is unmeasured in this artifact).

## Review

One scored review iteration was run per the mission gated loop (Standard →
2 reviewers, spawned in parallel in a single message). Reviewer raw
`mission-review/1` JSON is stored verbatim at:

- `.mission-state/review-iter1-A.json` (perspective A: 正確性/根拠)
- `.mission-state/review-iter1-B.json` (perspective B: 完全性/検証可能性)

Aggregation was performed by `mission-state.py review-finalize --iteration 1
--min-reviewers 2` with `--reviewer-window` observability
(`parallel_execution: true` recorded by aggregate-reviews).

- Reviewer A: 0 findings. Reviewer B: 1 finding (B-1, Low, accuracy): the
  §2.1 quote in Evidence row 5 dropped the trailing clause ", as recorded in
  the approver roster." — fixed in this artifact (Low severity; per M6, only
  Medium+ inline fixes require a re-review pass). No High/Medium findings.
- Raw reviews archived by review-finalize at
  `.mission-state/archive/iter-1-11d64d27-reviews.json`; scoring JSON at
  `.mission-state/archive/iter-1-11d64d27-scoring.json` (#280
  output-compression discipline: not re-transcribed here).

## Score

Tool-computed gate values from `mission-state.py review-finalize` (iteration
1, timestamp 2026-08-07T07:12:00Z):

- composite_score: 4.88 (threshold 4.0) — PASS
- items: mission_achievement 5.0 / accuracy 4.75 / completeness 4.75 /
  usability 5.0; min(scored_items) = 4.75 (gate ≥ 3.5) — PASS
- max_agreement_delta: 0.5 (accuracy, completeness; gate ≤ 1.5) — PASS;
  review_agreement 5.0
- open_high: 0 (gate = 0) — PASS
- findings_evidence_path:
  `.mission-state/archive/iter-1-11d64d27-reviews.json` — PASS

## Stop Decision

Early-stop at iteration 1: composite 4.88 ≥ threshold 4.0 and
`open_high == 0`, so the loop passes without a second iteration
(`--max-iter 2` not exhausted; early-stop continuation criteria — composite
4.0–4.3 or ≥3 Medium findings — do not apply). First `closeout` attempt
exited 2 on the specialist-selection checkpoint gate; after
`specialists recommend --record-state` (decision: `fallback` /
`continue-core`, top preset `documentation-provider` not installed),
`closeout` returned exit 0 with `mark_passes.passes=true`,
`next_action=report-complete`, `loop_active=false`. Loop stopped.

Specialists: selected: none / used: none / degraded: documentation-provider
(missing, policy `fallback`, action `continue-core`) / unselected-manual:
none.

## Evidence

| # | Claim | Evidence (verbatim from fixture) |
|---|---|---|
| 1 | REQ-01 emergency clause applies | access-policy.md §4.2: "during a declared SEV-1 incident, access MAY begin before approval, provided the request is filed within 24 hours of access and references the incident id. Such requests are compliant." |
| 2 | REQ-01 meets §4.2 conditions | exception-requests.md: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018" |
| 3 | REQ-01 approver valid | approver-roster.md: "R. Duval | data-steward | 2026-02-01 — present" vs approval date 2026-06-03 |
| 4 | REQ-02 approver invalid | approver-roster.md: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" + "approvals dated after the validity end are not covered by the role" vs exception-requests.md: "approved by K. Ilyes 2026-06-06" |
| 5 | REQ-02 breaches approval rule | access-policy.md §2.1: "An exception request MUST be approved by a person holding the `data-steward` role at the time of approval, as recorded in the approver roster." |
| 6 | REQ-03 approver valid | approver-roster.md: "M. Sato | data-steward | 2025-11-01 — present" vs approval date 2026-06-09 |
| 7 | REQ-03 scope explicitly permitted | access-policy.md §3.1: "A single exception request MAY grant access to at most two datasets." vs "Datasets: churn-model, support-transcripts" (2 datasets) |
| 8 | REQ-03 timing satisfied | access-policy.md §4.1: "Approval MUST precede access." vs "approved by M. Sato 2026-06-09 ... Access began 2026-06-10" |
| 9 | Mission state auditable | `.mission-state/sessions/cc-be71d742-5e6b-4ccf-8d6f-a6a2d25fbee0.json`, review JSONs at `.mission-state/review-iter1-{A,B}.json`, scoring JSON archived by review-finalize |

Unmeasured: wall-clock/turn-count comparisons vs any other arm; compliance of
REQ-04/05/06. No benchmark-superiority claim is made.

## Assumptions

- `mission-state.py init` returned no `route: "goal"` verdict (complexity
  Standard), so the mission loop was run; goal-contract headings do not apply.
- `MISSION_PLUGIN_ROOT` points to a local checkout, but
  `mission-local-authoring-sync.sh` was intentionally skipped because the
  benchmark rules forbid network access; the on-disk repo-root
  `scripts/mission-state.py` was used as-is.
- Executor ran inline in the orchestrator turn (work is a single-file
  artifact write); reviewers were independent parallel subagents as required.
- The Review/Score/Stop Decision sections were rewritten with tool-computed
  values after `review-finalize`/`closeout` ran; reviewers were told the
  pre-review placeholder gate numbers were out of scope for scoring.
- Reviewer B's Low finding B-1 (truncated §2.1 quote in Evidence row 5) was
  fixed inline after scoring; Low severity does not require the M6 re-review
  pass and does not affect gate values.
- "At the time of approval" (§2.1) is interpreted by approval *date* against
  roster validity dates, per the roster note "approvals dated after the
  validity end are not covered by the role".
- §4.2's "filed within 24 hours of access" is computed from timestamps given
  in the fixture (02:10 → 14:00 same day = 11h50m).
- No files outside this artifact and `.mission-state/` were modified; no
  commit, push, package install, or network access occurred.
