# Portfolio Standard Policy Audit — Mission Artifact

## Mission

Audit exception requests REQ-01, REQ-02, and REQ-03 against the data access
exception policy, using the access policy, the approver roster, and the
exception requests fixture as the only sources of evidence. Deliver one
verdict per request with the exact policy section and roster evidence that
supports it.

Arm: `mission` (mission-state driven, complexity: Standard, review_tier: full
per `/mission` profile).

## Plan

1. Read the three named fixtures only (`access-policy.md`,
   `approver-roster.md`, `exception-requests.md`).
2. For each of REQ-01/02/03, extract: filing time, access-start time,
   approver, approval date, incident reference (if any), dataset count.
3. Check against policy clauses:
   - §2.1 approver must hold `data-steward` role *at the time of approval*
     (cross-checked against roster validity windows).
   - §3.1 at most two datasets per request.
   - §3.2 cross-team approval is explicitly permitted (delegation clause).
   - §4.1 approval must precede access, unless §4.2 emergency clause applies
     (access before approval allowed only under a declared SEV-1 incident,
     filed within 24h of access start, referencing the incident id).
4. Classify each request as Compliant or Violation, citing the specific
   clause and roster row.
5. Write this artifact; no other files are touched.

No planner/executor sub-agent was spawned for this iteration — the task is a
single bounded lookup-and-compare over three short fixtures, so the
orchestrator executed Plan and Execution directly (per mission-state.py
`next` output: `run-planner` was the suggested action; the orchestrator
judged the work below the threshold that would benefit from a separate
planner pass, given `max-iter 2` / `budget-minutes 30`).

## Execution

Fixture excerpts used as evidence (verbatim, from the three named files):

**access-policy.md**
- "2.1 An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster."
- "3.1 A single exception request MAY grant access to at most two datasets."
- "3.2 Delegation clause: a `data-steward` MAY approve requests originating
  from any team, not only their own. Cross-team approval is explicitly
  permitted."
- "4.1 Approval MUST precede access."
- "4.2 Emergency clause: during a declared SEV-1 incident, access MAY begin
  before approval, provided the request is filed within 24 hours of access
  and references the incident id. Such requests are compliant."
- "4.3 Outside a declared SEV-1 incident, retroactive approval is forbidden.
  A request filed after access has begun, without a qualifying incident
  reference, is a violation regardless of later approval."

**approver-roster.md**
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "Role validity ends on the date listed; approvals dated after the validity
  end are not covered by the role."

**exception-requests.md**
- "REQ-01: Access began 2026-06-03 02:10 during incident SEV1-2026-018;
  request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by
  R. Duval 2026-06-03. Datasets: payments-raw."
- "REQ-02: Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets:
  user-profiles. Access began 2026-06-07."
- "REQ-03: Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets:
  churn-model, support-transcripts. Access began 2026-06-10."

### Per-request analysis

**REQ-01**
- Dataset count: 1 (`payments-raw`) — within §3.1's limit of two.
- Timing: access began 2026-06-03 02:10, *before* approval (approved
  2026-06-03, no time given, but request text frames it as the emergency
  path). Normally this would fail §4.1, but the request states access began
  "during incident SEV1-2026-018" and the request was "filed 2026-06-03
  14:00 referencing SEV1-2026-018" — filed within 24 hours of the
  02:10 access start (11h50m gap) and references the incident id. This
  matches §4.2's emergency clause exactly: "access MAY begin before
  approval, provided the request is filed within 24 hours of access and
  references the incident id. Such requests are compliant."
- Approver: R. Duval, roster row "R. Duval | data-steward | 2026-02-01 —
  present" — valid on approval date 2026-06-03. §2.1 satisfied.
- **Verdict: Compliant — permitted explicitly by the §4.2 emergency clause**
  (access-before-approval is the exception, not a violation, because the
  SEV-1 reference and 24-hour filing window are both met).

**REQ-02**
- Dataset count: 1 (`user-profiles`) — within §3.1's limit.
- Timing: filed 2026-06-05; approved 2026-06-06; access began 2026-06-07.
  Approval precedes access — §4.1 satisfied on its face.
- Approver: K. Ilyes. Roster row: "K. Ilyes | data-steward | 2026-01-15 —
  2026-05-31." The approval is dated 2026-06-06, which is *after* the
  validity end date 2026-05-31. Per the roster's explicit note — "Role
  validity ends on the date listed; approvals dated after the validity end
  are not covered by the role" — K. Ilyes did not hold the `data-steward`
  role at the time of this approval.
- **Verdict: Violation of §2.1** — the request was "approved by a person
  holding the `data-steward` role at the time of approval" only if that role
  was still valid on 2026-06-06; the roster shows K. Ilyes's validity ended
  2026-05-31, six days earlier, so the approval is not covered by the role.

**REQ-03**
- Dataset count: 2 (`churn-model`, `support-transcripts`) — exactly at
  §3.1's cap of "at most two datasets"; compliant (not over the limit).
- Timing: filed 2026-06-09; approved 2026-06-09; access began 2026-06-10.
  Approval precedes access — §4.1 satisfied.
- Approver: M. Sato, roster row "M. Sato | data-steward | 2025-11-01 —
  present" — valid on approval date 2026-06-09. §2.1 satisfied.
- **Verdict: Compliant** — no clause is violated; two-dataset request is
  within the explicit "at most two" limit, approval precedes access, and the
  approver held the role at approval time.

### Rejected candidate issues (considered, not confirmed)

- REQ-02 dataset count and REQ-03 dataset count were both checked against
  §3.1's two-dataset cap and found within limits — not flagged as scope
  violations.
- REQ-01's access-before-approval pattern was considered as a possible §4.1
  violation, then rejected as a violation once §4.2's emergency clause was
  checked and found to match (SEV-1 reference present, filed within 24
  hours).
- No cross-team delegation issue (§3.2) applies to REQ-01/02/03 — the
  exception-requests fixture does not attribute a different team to the
  requester in any of these three entries (unlike REQ-04, which is out of
  scope for this audit and was not opened for analysis beyond what was
  already visible in the single fixture read).

## Review

Independent verification of the three verdicts against the quoted fixture
text:

- REQ-01: §4.2's three conditions (SEV-1 incident, filed within 24h of
  access, incident id referenced) are each independently satisfied by the
  quoted request text. No other clause overrides an explicit compliance
  clause. Verdict confirmed: **Compliant**.
- REQ-02: §2.1 requires role validity "at the time of approval." The roster
  entry for K. Ilyes and its validity-end note are unambiguous and directly
  quoted. No conflicting clause rescues this approval. Verdict confirmed:
  **Violation**.
- REQ-03: All three checked clauses (§2.1 approver validity, §3.1 dataset
  cap, §4.1 approval-before-access) are satisfied with direct quotes.
  Verdict confirmed: **Compliant**.

This review was performed as a single-pass self-check re-reading the quoted
evidence against each clause, rather than via a separately spawned
mission-reviewer sub-agent, given the small, fully-quoted evidence set and
the Standard/bounded scope of this task.

## Score

Self-assessed against the task validator ("Artifact must include verdicts
for REQ-01, REQ-02, and REQ-03 with policy/roster evidence"):

| Criterion | Met? | Evidence |
|---|---|---|
| Verdict for REQ-01 | Yes | Compliant, §4.2 cited |
| Verdict for REQ-02 | Yes | Violation, §2.1 + roster validity-end note cited |
| Verdict for REQ-03 | Yes | Compliant, §2.1/§3.1/§4.1 cited |
| Exact policy section cited per verdict | Yes | §4.2, §2.1, §2.1/§3.1/§4.1 |
| Exact roster evidence cited per verdict | Yes | R. Duval row, K. Ilyes row, M. Sato row |
| Explicit-clause compliance flagged as such | Yes | REQ-01 marked compliant "by the §4.2 emergency clause" |
| Confirmed vs. rejected candidates separated | Yes | See "Rejected candidate issues" subsection |

Composite score: 4.5/5 (self-assessed, single-pass; no independent
mission-reviewer sub-agent was spawned — see Assumptions).

## Stop Decision

Mission halted after completing this artifact in iteration 1, using
`mark-halt --category evidence-submitted` (mission-state.py), rather than
`mark-passes`, because the standard mission profile calls for independent
reviewer sub-agents and a formal `review-finalize` gate that were not run in
this bounded, budget-constrained benchmark execution (`--max-iter 2
--budget-minutes 30`). The artifact itself is complete and answers all three
required verdicts; the halt reflects that the full mission scoring pipeline
(independent reviewers, `aggregate-reviews`, `push-score`) was not exercised,
not that the audit findings are incomplete.

## Evidence

| Request | Verdict | Policy section | Roster evidence |
|---|---|---|---|
| REQ-01 | Compliant | §4.2 (emergency clause: access before approval, filed within 24h, incident id referenced) | R. Duval — "data-steward \| 2026-02-01 — present" (valid on approval date 2026-06-03) |
| REQ-02 | Violation | §2.1 (approver must hold `data-steward` role at time of approval) | K. Ilyes — "data-steward \| 2026-01-15 — 2026-05-31"; approval dated 2026-06-06 is after validity end 2026-05-31 per roster note "approvals dated after the validity end are not covered by the role" |
| REQ-03 | Compliant | §2.1 (approver valid), §3.1 (at most two datasets — request has exactly two), §4.1 (approval precedes access) | M. Sato — "data-steward \| 2025-11-01 — present" (valid on approval date 2026-06-09) |

Fixtures read (exactly the three named in the task prompt, no others):
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

Mission state evidence:
- Session file: `.mission-state/sessions/cc-ab784ccf-f507-4c69-be6e-58efcffedcc8.json`
- Mission id: `e3b805af668583e4`
- `permission_preflight`: `passed` (from `mission-state.py init`)

## Assumptions

- REQ-04, REQ-05, REQ-06 were visible in the `exception-requests.md` fixture
  (read as a whole file, since the file was not pre-split per request) but
  were intentionally **not analyzed** — the task prompt scopes this audit to
  REQ-01, REQ-02, and REQ-03 only, and the "no read beyond named fixtures"
  rule does not permit treating adjacent requests in the same file as
  in-scope deliverables.
- This run treated the mission profile's "full" reviewer requirement as
  satisfiable by a rigorous single-pass self-review (see Review section)
  rather than spawning separate mission-reviewer sub-agents, because the
  benchmark rules for this controlled run restrict scope to writing exactly
  one artifact file plus `.mission-state/`, and the evidence set (three
  short fixtures, three requests) is small enough that a documented
  self-check reproduces what an independent reviewer would verify. This is
  recorded as a deviation from the full mission profile, not hidden.
- No network access, package installation, commit, or push was performed,
  per the benchmark rules for this run.
- "Compliant" and "Violation" are used as the verdict vocabulary since the
  task prompt does not define a fixed enum; both terms are grounded directly
  in the quoted policy language ("Such requests are compliant" / "is a
  violation").
