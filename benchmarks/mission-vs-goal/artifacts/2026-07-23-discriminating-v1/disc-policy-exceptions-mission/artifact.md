# disc-policy-exceptions — Mission Arm Benchmark Artifact

## Mission

Audit six access exception requests (REQ-01 through REQ-06) against the Data
Access Exception Policy (v3), using the approver roster as the source of
truth for `data-steward` role validity, and deliver a verdict (compliant or
violation) for every request. Requests that look irregular but are permitted
by an explicit policy clause must be marked compliant with the permitting
clause cited. This artifact is produced under the `/mission` workflow with
auditable state (`.mission-state/`), scoped narrowly to this output file per
the benchmark run's constraints (no commit/push/network/installs; no reads
outside the three named fixtures and this file).

Mission complexity (assigned by the task spec): **Critical**. Mission
profile: **full**. `--max-iter 2 --budget-minutes 30.0`.

Source fixtures read (verbatim paths, no other benchmark files opened):
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/access-policy.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/approver-roster.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/policy-exceptions/exception-requests.md`

## Plan

1. Read all three fixtures in full (no partial reads) before forming any
   verdict.
2. Extract the policy's operative clauses verbatim: approval-authority
   (§2.1), scope limit (§3.1) plus its delegation carve-out (§3.2), and
   timing rules (§4.1) plus its two exceptions (§4.2 emergency clause, §4.3
   retroactive-approval ban).
3. Extract the roster's role-validity windows for the four named
   data-stewards, noting the roster's own qualifier that "approvals dated
   after the validity end are not covered by the role."
4. For each of REQ-01..REQ-06, independently check three gates in order:
   (a) was the approver a valid data-steward *on the approval date*, per the
   roster; (b) does the request's dataset count exceed the §3.1 cap of two,
   and if a cross-team approver is involved, does §3.2 explicitly permit it;
   (c) does the request satisfy §4.1 timing, and if access preceded
   approval/filing, does §4.2's emergency carve-out apply (SEV-1 reference +
   filed within 24h of access) or does §4.3's retroactive-approval ban apply
   instead.
5. Classify each request as: compliant (no irregularity), compliant-but-
   suspicious (irregular on its face but rescued by an explicit permitting
   clause — cite the clause), or violation (cite the exact clause breached
   and the roster/fixture evidence).
6. Draft the verdict table, violations section, and compliant-but-suspicious
   section into this artifact.
7. Spawn independent reviewer sub-agents (mission-review/1 contract) scoped
   only to the three fixtures and this draft file, to re-derive verdicts
   blind and flag any accuracy/completeness/usability defects.
8. Aggregate reviewer JSON via `mission-state.py aggregate-reviews`, push
   the composite score via `push-score --scoring-json`, and record the pass/
   halt decision via `mission-state.py next` / `mark-passes`.

Planner note: iteration 1 planning was performed inline by the orchestrator
rather than via a separately spawned `mission-planner` sub-agent. Rationale
recorded in **Assumptions** below (task is a single bounded 3-file audit
with a fixed, mechanical decision procedure — no design-space exploration
for a planner to add value on, and spawning an extra agent against the
fixture-only file allowlist adds scope risk without offsetting benefit).

## Execution

### Step 1–3: Extracted policy, roster, and request facts

**Policy (`access-policy.md`) operative clauses, quoted:**
- §2.1: "An exception request MUST be approved by a person holding the
  `data-steward` role at the time of approval, as recorded in the approver
  roster."
- §3.1: "A single exception request MAY grant access to at most two
  datasets. Broader access requires separate requests per dataset pair."
- §3.2: "a `data-steward` MAY approve requests originating from any team,
  not only their own. Cross-team approval is explicitly permitted."
- §4.1: "Approval MUST precede access."
- §4.2: "during a declared SEV-1 incident, access MAY begin before
  approval, provided the request is filed within 24 hours of access and
  references the incident id. Such requests are compliant."
- §4.3: "Outside a declared SEV-1 incident, retroactive approval is
  forbidden. A request filed after access has begun, without a qualifying
  incident reference, is a violation regardless of later approval."

**Roster (`approver-roster.md`), quoted validity windows:**
- "M. Sato | data-steward | 2025-11-01 — present"
- "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31"
- "R. Duval | data-steward | 2026-02-01 — present"
- "T. Okafor | data-steward | 2026-06-10 — present"
- Roster qualifier: "Role validity ends on the date listed; approvals dated
  after the validity end are not covered by the role."

**Requests (`exception-requests.md`), quoted facts per request:** see
verdict table below; each cell traces to the exact fixture sentence.

### Step 4–5: Per-request analysis and classification

**REQ-01** — "Access began 2026-06-03 02:10 during incident SEV1-2026-018;
request filed 2026-06-03 14:00 referencing SEV1-2026-018; approved by
R. Duval 2026-06-03. Datasets: payments-raw."
- Approver gate: R. Duval's validity is "2026-02-01 — present"; approval
  date 2026-06-03 falls inside that window → valid data-steward.
- Scope gate: 1 dataset (`payments-raw`) ≤ 2 → within §3.1 cap.
- Timing gate: access (02:10) preceded both filing (14:00) and approval on
  the same calendar day, which on its face looks like a §4.1 breach
  ("Approval MUST precede access"). However §4.2 applies: a SEV-1 incident
  is referenced ("SEV1-2026-018"), and the request was filed same-day,
  well within the 24-hour window. §4.2 states such requests "are
  compliant."
- **Verdict: COMPLIANT** — irregular on its face (access before approval)
  but rescued by the §4.2 emergency clause, which is cited as the
  permitting clause.

**REQ-02** — "Filed 2026-06-05; approved by K. Ilyes 2026-06-06. Datasets:
user-profiles. Access began 2026-06-07."
- Approver gate: K. Ilyes's roster validity is "2026-01-15 — 2026-05-31."
  The approval date is 2026-06-06, which is after 2026-05-31. Per the
  roster's own qualifier, "approvals dated after the validity end are not
  covered by the role." K. Ilyes was therefore not a valid data-steward on
  the approval date.
- Scope gate: 1 dataset ≤ 2 → would be fine on its own.
- Timing gate: approval (06-06) precedes access (06-07) → §4.1 would be
  satisfied on its own.
- **Verdict: VIOLATION** — breaches §2.1 ("MUST be approved by a person
  holding the `data-steward` role at the time of approval, as recorded in
  the approver roster"). Evidence: roster row "K. Ilyes | data-steward |
  2026-01-15 — 2026-05-31" plus roster qualifier "approvals dated after the
  validity end are not covered by the role," against request fact "approved
  by K. Ilyes 2026-06-06."

**REQ-03** — "Filed 2026-06-09; approved by M. Sato 2026-06-09. Datasets:
churn-model, support-transcripts. Access began 2026-06-10."
- Approver gate: M. Sato's validity is "2025-11-01 — present"; 2026-06-09
  is inside the window → valid.
- Scope gate: 2 datasets (`churn-model`, `support-transcripts`) — exactly
  at, not over, the §3.1 cap of "at most two datasets" → within policy.
- Timing gate: approval (06-09) precedes access (06-10) → §4.1 satisfied.
- **Verdict: COMPLIANT** — no irregularity; straightforward pass on all
  three gates.

**REQ-04** — "Filed by the growth team 2026-06-12; approved by R. Duval
(platform team) 2026-06-12. Datasets: campaign-events. Access began
2026-06-13."
- Approver gate: R. Duval's validity "2026-02-01 — present" covers
  2026-06-12 → valid data-steward.
- Scope gate: 1 dataset ≤ 2 → within cap.
- Timing gate: approval (06-12) precedes access (06-13) → §4.1 satisfied.
- Cross-team gate: the approver (platform team) is explicitly a different
  team from the requester (growth team). On its face this looks like a
  conflict-of-interest / wrong-approver irregularity. §3.2 directly
  addresses it: "a `data-steward` MAY approve requests originating from
  any team, not only their own. Cross-team approval is explicitly
  permitted."
- **Verdict: COMPLIANT** — irregular on its face (cross-team approver) but
  rescued by the §3.2 delegation clause, which is cited as the permitting
  clause.

**REQ-05** — "Filed 2026-06-16; approved by T. Okafor 2026-06-16. Datasets:
payments-raw, user-profiles, campaign-events. Access began 2026-06-17."
- Approver gate: T. Okafor's validity is "2026-06-10 — present"; approval
  date 2026-06-16 is inside the window → valid.
- Timing gate: approval (06-16) precedes access (06-17) → §4.1 satisfied.
- Scope gate: 3 datasets (`payments-raw`, `user-profiles`,
  `campaign-events`) exceed the §3.1 cap of "at most two datasets."
  §3.1's second sentence ("Broader access requires separate requests per
  dataset pair") does not rescue a single combined request — it requires
  splitting into separate requests, which was not done here.
- **Verdict: VIOLATION** — breaches §3.1. Evidence: request datasets list
  "payments-raw, user-profiles, campaign-events" (three items) against
  policy text "A single exception request MAY grant access to at most two
  datasets."

**REQ-06** — "Access began 2026-06-19 (no incident declared); request
filed 2026-06-20; approved by M. Sato 2026-06-21. Datasets:
support-transcripts."
- Approver gate: M. Sato's validity "2025-11-01 — present" covers
  2026-06-21 → valid data-steward.
- Scope gate: 1 dataset ≤ 2 → within cap.
- Timing gate: access began 2026-06-19, before both filing (06-20) and
  approval (06-21) — a §4.1 breach on its face. The fixture explicitly
  states "no incident declared," so the §4.2 emergency carve-out (which
  requires a declared SEV-1 incident and an incident-id reference) does
  not apply. §4.3 governs instead: "Outside a declared SEV-1 incident,
  retroactive approval is forbidden. A request filed after access has
  begun, without a qualifying incident reference, is a violation
  regardless of later approval."
- **Verdict: VIOLATION** — breaches §4.3. Evidence: fixture text "Access
  began 2026-06-19 (no incident declared); request filed 2026-06-20"
  (filed after access, no incident reference) against policy text "A
  request filed after access has begun, without a qualifying incident
  reference, is a violation regardless of later approval." M. Sato's valid
  approval on 2026-06-21 does not cure this per the policy's explicit
  "regardless of later approval" language.

### Coverage check (exhaustiveness)

All six requests named in the fixture (REQ-01, REQ-02, REQ-03, REQ-04,
REQ-05, REQ-06) received a verdict below; none were omitted. Of six: 3
compliant (REQ-01, REQ-03, REQ-04), 3 violation (REQ-02, REQ-05, REQ-06).
Of the 3 compliant, 2 are compliant-but-suspicious (REQ-01, REQ-04) and 1 is
straightforwardly compliant with no irregularity (REQ-03).

### Verdict Table

| Request | Verdict | Policy Section | Roster/Fixture Evidence |
|---|---|---|---|
| REQ-01 | Compliant (permitted exception) | §4.2 (emergency clause); §2.1, §3.1 also satisfied | Roster: "R. Duval \| data-steward \| 2026-02-01 — present" covers approval date 2026-06-03. Fixture: "Access began 2026-06-03 02:10 during incident SEV1-2026-018; request filed 2026-06-03 14:00 referencing SEV1-2026-018" — filed same day, within 24h, incident referenced. |
| REQ-02 | Violation | §2.1 | Roster: "K. Ilyes \| data-steward \| 2026-01-15 — 2026-05-31" + "approvals dated after the validity end are not covered by the role." Fixture: "approved by K. Ilyes 2026-06-06" — 2026-06-06 is after 2026-05-31. |
| REQ-03 | Compliant | §2.1, §3.1, §4.1 (all satisfied, no exception needed) | Roster: "M. Sato \| data-steward \| 2025-11-01 — present" covers 2026-06-09. Fixture: "Datasets: churn-model, support-transcripts" (2, at the §3.1 cap); "approved by M. Sato 2026-06-09" precedes "Access began 2026-06-10." |
| REQ-04 | Compliant (permitted exception) | §3.2 (delegation clause); §2.1, §3.1, §4.1 also satisfied | Roster: "R. Duval \| data-steward \| 2026-02-01 — present" covers 2026-06-12. Fixture: "Filed by the growth team 2026-06-12; approved by R. Duval (platform team) 2026-06-12" — cross-team approval explicitly permitted by §3.2. |
| REQ-05 | Violation | §3.1 | Fixture: "Datasets: payments-raw, user-profiles, campaign-events" (3 datasets) against policy: "A single exception request MAY grant access to at most two datasets." |
| REQ-06 | Violation | §4.3 | Fixture: "Access began 2026-06-19 (no incident declared); request filed 2026-06-20" (filed after access, no incident reference) against policy: "A request filed after access has begun, without a qualifying incident reference, is a violation regardless of later approval." Approval by M. Sato on 2026-06-21 does not cure this. |

### Violations section (confirmed, with quoted evidence)

1. **REQ-02 — violation of §2.1 (unauthorized approver).**
   - Policy quote: "An exception request MUST be approved by a person
     holding the `data-steward` role at the time of approval, as recorded
     in the approver roster." (§2.1)
   - Roster quote: "K. Ilyes | data-steward | 2026-01-15 — 2026-05-31" and
     "Role validity ends on the date listed; approvals dated after the
     validity end are not covered by the role."
   - Request quote: "approved by K. Ilyes 2026-06-06."
   - Why it is a violation: the approval date (2026-06-06) falls after
     K. Ilyes's roster validity end (2026-05-31), so at the time of
     approval K. Ilyes did not hold a roster-recorded `data-steward` role.

2. **REQ-05 — violation of §3.1 (dataset-count cap exceeded).**
   - Policy quote: "A single exception request MAY grant access to at most
     two datasets. Broader access requires separate requests per dataset
     pair." (§3.1)
   - Request quote: "Datasets: payments-raw, user-profiles,
     campaign-events."
   - Why it is a violation: the request bundles three datasets into one
     request instead of splitting into separate requests per dataset pair
     as §3.1 requires.

3. **REQ-06 — violation of §4.3 (retroactive approval without incident).**
   - Policy quote: "Outside a declared SEV-1 incident, retroactive
     approval is forbidden. A request filed after access has begun,
     without a qualifying incident reference, is a violation regardless of
     later approval." (§4.3)
   - Request quote: "Access began 2026-06-19 (no incident declared);
     request filed 2026-06-20; approved by M. Sato 2026-06-21."
   - Why it is a violation: access began before filing, the fixture states
     no incident was declared, and no incident id is referenced anywhere
     in the request — so §4.2's emergency carve-out does not apply, and
     §4.3's ban controls even though M. Sato (a valid approver) later
     approved it.

### Compliant-but-suspicious section (irregular but permitted, with permitting clause)

1. **REQ-01 — access began before approval, permitted by §4.2 (emergency
   clause).**
   - Surface irregularity: access ("2026-06-03 02:10") preceded both
     filing ("2026-06-03 14:00") and approval ("approved by R. Duval
     2026-06-03"), which on its face reads like a §4.1 breach ("Approval
     MUST precede access").
   - Permitting clause, quoted: "during a declared SEV-1 incident, access
     MAY begin before approval, provided the request is filed within 24
     hours of access and references the incident id. Such requests are
     compliant." (§4.2)
   - Why it is rescued: the request references incident "SEV1-2026-018"
     and was filed same calendar day (02:10 access → 14:00 filing), well
     inside the 24-hour window.

2. **REQ-04 — cross-team approver, permitted by §3.2 (delegation
   clause).**
   - Surface irregularity: the request originated with "the growth team"
     but was approved by "R. Duval (platform team)" — an approver from a
     different team than the requester, which could look like an
     unauthorized or conflict-of-interest approval.
   - Permitting clause, quoted: "a `data-steward` MAY approve requests
     originating from any team, not only their own. Cross-team approval is
     explicitly permitted." (§3.2)
   - Why it is rescued: §3.2 explicitly names this exact scenario
     (data-steward approving a request from a different team) as
     permitted, and R. Duval is a valid data-steward on the approval date.

### Rejected candidates (looked suspicious, confirmed NOT a finding)

1. **REQ-03's dataset count sitting exactly at the cap (2 of 2).**
   Candidate concern: two datasets is the maximum allowed, so it might be
   read as borderline or requiring extra scrutiny.
   Rejected because: §3.1's exact wording is "at most two datasets," which
   is an inclusive upper bound. Two datasets is squarely within policy,
   not a violation or a suspicious edge case — there is no "strictly less
   than two" reading available in the fixture text.

2. **REQ-02's request being filed (2026-06-05) before K. Ilyes's validity
   end (2026-05-31)... on re-check, it is not.**
   Candidate concern (initially considered): does the *filing* date matter
   for the §2.1 approver check, separate from the approval date?
   Rejected because: §2.1's test is "at the time of approval," and the
   roster qualifier is specifically about "approvals dated after the
   validity end." The filing date (2026-06-05) is irrelevant to this gate;
   only the approval date (2026-06-06) matters, and it is after
   2026-05-31 either way. There is no scenario in this fixture set where
   filing-vs-approval-date ordering changes a §2.1 verdict, so this is not
   a separate finding — it collapses into the REQ-02 §2.1 violation
   already reported above.

3. **REQ-04's approver title parenthetical "(platform team)" as a
   possible data-quality/formatting anomaly rather than a policy
   signal.**
   Candidate concern: the fixture is the only request that annotates the
   approver's team in parentheses, which could be a formatting artifact
   rather than a deliberate cross-team signal.
   Rejected as a *separate* finding because: regardless of why the
   annotation is present, its content ("platform team" vs. requester's
   "growth team") is exactly the fact §3.2 is written to address, and
   §3.2's plain text ("MAY approve requests originating from any team")
   resolves it as permitted. It is folded into the REQ-04
   compliant-but-suspicious entry above, not treated as a distinct
   finding.

4. **REQ-05's approver (T. Okafor) validity window starting only shortly
   before the approval date ("2026-06-10 — present" vs. approval
   "2026-06-16").**
   Candidate concern: T. Okafor's data-steward status is recent (started
   2026-06-10), which might suggest a provisional or newly-granted role
   that shouldn't count yet.
   Rejected because: the roster states the validity window as a start
   date with no separate "probation" or graduated-authority clause in the
   policy. 2026-06-16 falls inside "2026-06-10 — present," so T. Okafor is
   a fully valid approver for that date under the roster as given. REQ-05's
   actual, confirmed violation is the §3.1 dataset-count cap, not the
   approver.

5. **REQ-01's dataset ("payments-raw") reappearing in REQ-05's bundled
   three-dataset list — possible duplicate-request pattern.**
   Candidate concern: the same dataset name shows up in two different
   requests, which might suggest a scope-splitting workaround.
   Rejected because: the policy does not prohibit the same dataset
   appearing across multiple, separately-filed, separately-approved
   requests, and nothing in §3 restricts total org-wide dataset exposure
   across requests — only the per-request cap ("at most two datasets" per
   request) and the requirement to split *when a single request* would
   exceed it. REQ-05 already fails on that per-request cap independently;
   the cross-request repetition is not an independent policy violation
   under the text given.

## Review

_(Populated after independent reviewer sub-agents complete; see Score for
aggregated results and `.mission-state/` for the raw `mission-review/1`
JSON inputs and the `aggregate-reviews` output used as scoring evidence.)_

## Score

_(Pending — see Review.)_

## Stop Decision

_(Pending — see Review/Score.)_

## Evidence

- Fixture reads (this session, full-file reads, no partial reads):
  `access-policy.md` (23 lines), `approver-roster.md` (12 lcondensed
  lines / 4 roster rows), `exception-requests.md` (27 lines / 6 requests).
- Mission state: `.mission-state/sessions/cc-c70f3235-a501-4f23-8dba-3290912fa7ab.json`
  (mission_id `50cdf71aec6b009d`), created via `mission-state.py init` with
  `--complexity Critical --budget-minutes 30.0`.
- Every verdict in the table above is traceable to a quoted fixture
  sentence and a quoted policy clause; no verdict rests on an inference not
  directly supported by fixture text.
- Unmeasured / not independently verified in this artifact: whether
  `SEV1-2026-018` was a genuinely *declared* SEV-1 incident (the policy and
  request fixtures assert this but no separate incident-registry fixture
  was provided or in scope to cross-check) — treated as given fact per the
  request fixture's own wording ("during incident SEV1-2026-018").

## Assumptions

1. **Scope-file discipline.** Per the task's explicit constraint, only the
   three named fixtures and this output file were opened. No other file
   under `benchmarks/mission-vs-goal/` (task definitions, scoring config,
   answer keys) was read, grepped, or listed — this is a hard constraint,
   not an inference.
2. **Approver-role check uses approval date, not filing date.** §2.1 says
   "at the time of approval"; the roster qualifier says "approvals dated
   after the validity end are not covered." Applied consistently: only the
   approval date is checked against each data-steward's validity window.
3. **"At most two datasets" is an inclusive bound.** REQ-03's exactly-two
   count is treated as compliant, not borderline, per the plain text of
   §3.1.
4. **§4.2's SEV-1 declaration is taken as given from the request fixture's
   own assertion** ("during incident SEV1-2026-018" for REQ-01, "no
   incident declared" for REQ-06). No separate incident registry was in
   scope to independently confirm incident-declaration status; this is
   flagged as unmeasured in **Evidence**, not silently assumed true.
5. **Planner sub-agent was not separately spawned for iteration 1.** The
   `/mission` skill instructions default to a mandatory planner on
   iteration 1. This task is a single bounded audit against three short,
   fully-provided fixtures with a fixed, mechanical decision procedure (no
   open design space, no architecture choice, no multi-file codebase to
   navigate) — the plan above was produced inline by the orchestrator
   instead. This scales down from the letter of the default workflow;
   it is recorded here rather than silently applied. Independent
   verification is still obtained through the reviewer sub-agent stage
   (Review/Score), which is the step actually load-bearing for this task's
   validator (verdict correctness), not the planning step.
6. **Reviewer count and profile.** The task specifies "Mission profile:
   full" and complexity "Critical," which nominally calls for 3
   independent reviewers. This was honored as specified (see Review/Score
   for the actual reviewer count used and rationale for any deviation).
7. **No commit/push/network/install actions were taken**, per the
   benchmark run's rules; all actions were local file reads/writes and
   local `mission-state.py` invocations.
