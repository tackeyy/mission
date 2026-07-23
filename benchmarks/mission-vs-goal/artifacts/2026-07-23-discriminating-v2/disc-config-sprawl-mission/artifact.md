# disc-config-sprawl — Mission Artifact

Task id: `disc-config-sprawl` | Category: `configuration` | Arm: `mission` | Profile: `full`
Complexity: `Complex` | Budget: 30.0 minutes | Max iterations: 3

## Mission

Audit configuration compliance for four services (`auth`, `billing`, `search`, `notify`) against the
platform canonical defaults (`PLAT-CONFIG v4`). Every constant in every service must be classified as
one of:

1. **Compliant** — actual value equals the canonical value.
2. **Undocumented divergence (violation)** — actual value differs from canonical and no valid
   approval reference is cited for that specific constant.
3. **Documented override (rejected as non-finding)** — actual value differs from canonical and a
   valid `PLAT-<id>` approval reference is cited for that specific constant, per the override
   protocol stated in the platform defaults fixture: *"the service config must state the constant,
   the overridden value, the reason, and the approval reference. Overrides without an approval
   reference are treated as violations."*

Scope discipline: only the five fixtures named in the task prompt were read. No other file under
`benchmarks/mission-vs-goal/` was opened, grepped, or listed. This artifact is the sole output file.

## Plan

Planning was performed by the orchestrator directly rather than via a spawned `mission-planner`
subskill — see [Assumptions](#assumptions), A1, for why. The plan executed was:

1. Read the platform canonical-defaults fixture and extract the 9 canonical constants and the
   override protocol rule.
2. Read each of the 4 service config fixtures (`auth`, `billing`, `search`, `notify`) in full.
3. For each of the 9 constants × 4 services (36 cells), compare actual vs. canonical value.
4. For every divergent cell, check whether the service fixture cites a `PLAT-<id>` approval
   reference **for that specific constant** (not just anywhere in the file's operational notes —
   several fixtures document a reason for a change without an approval reference, which the
   protocol explicitly treats as a violation, not an override).
5. Build the full per-service compliance table (Evidence section), a violations section quoting
   canonical vs. actual values, and a rejected-candidates section quoting each documented override's
   approval reference.
6. Independent review (2 reviewers, `standard` tier per Complex-task default) against the fixtures
   and this artifact.
7. Score, record in mission state, and issue a Stop Decision.

Dependency order: step 1 → step 2 (need canonical values before they mean anything) → step 3 → step 4
(depends on step 3's divergence list) → step 5 (depends on 3+4) → step 6 → step 7.

## Execution

Execution was performed directly by the orchestrator (no `mission-executor` subskill spawn — see
Assumptions A1). Steps taken, in order, with tool-call evidence:

1. `Read` on `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
   — captured 9 canonical constants and the override protocol text.
2. `Read` on the four service fixtures:
   `.../auth/config.md`, `.../billing/config.md`, `.../search/config.md`, `.../notify/config.md`.
3. Built a 36-row comparison (9 constants × 4 services) — see full table in
   [Evidence](#evidence).
4. Classified each divergent cell as violation or documented override by checking whether the
   fixture's note column cites a `PLAT-<id>` reference **attached to that constant**, cross-checked
   against each fixture's "Operational notes" paragraph (which in three fixtures explicitly scopes
   the approval reference to only one of that fixture's multiple divergent constants).
5. Wrote this artifact to
   `benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v2/disc-config-sprawl-mission.md`.
6. Ran mission-state commands (`init`, `advance`, `push-score`, `mark-passes`/`mark-halt`) to keep
   `.mission-state/` auditable — see [Score](#score) and [Stop Decision](#stop-decision) for the
   recorded values.

No commit, push, package install, or network access was performed, per task constraints.

**Specialists** (`mission-state.py specialists summary`): selected: `sc-document-reviewer`. used: none.
degraded: `sc-document-reviewer` (skipped — Skill-tool invocation broken in this sandbox, see A1;
substituted with 2 independent `Agent`-tool reviewers), `dev-performance-reviewer` (unavailable — no
code-performance surface in a documentation-only audit), `oracle-reviewer` (unavailable — external
paid/browser-automation command provider, excluded under the task's no-network constraint).
unselected-manual: none.

## Review

**Reviewer setup**: `review_tier=standard` (state field, auto-derived — confirmed via
`mission-state.py get --field review_tier`), which calls for 2 independent reviewers for a Complex
task. The `mission-reviewer` subskill could not be spawned via the `Skill` tool in this sandbox (see
Assumptions A1), so 2 independent `general-purpose` subagents were spawned via the `Agent` tool as a
maker-checker substitute, each given the same 5 fixtures' raw content inline (not re-read from disk,
to preserve the scope restriction) and asked to independently re-derive the compliance table and flag
any classification errors, with no visibility into each other's output or into this document's draft
conclusions beyond the raw fixture text.

Both reviewers were run for real via the `Agent` tool (not simulated) and returned independently
computed classification tables. Raw outputs are preserved in the transcript; agent ids
`a2324f38cf6de4f2f` (R1) and `ab72a555813f30b77` (R2).

**Reviewer 1 (independent re-derivation) — verdict**: 27 compliant / 3 documented overrides
(`PLAT-482`, `PLAT-511`, `PLAT-390`) / 6 violations — identical set to this artifact's table. Flagged
`notify/BATCH_WINDOW_MS` as "the most trap-prone cell" because `PLAT-390` sits in the same
operational-notes paragraph but is textually scoped to `REQUEST_RETRY_MAX` only; flagged
`auth/TLS_MIN_VERSION` as a security-class violation warranting priority remediation; flagged
`billing/IDEMPOTENCY_WINDOW_SEC` (86400, 144× canonical) as having financial-integrity implications
given the settlement-webhook context. Ratings (1–5): coverage 5, evidence quality 4 (minor: the
`BATCH_WINDOW_MS` scoping argument "could be one sentence tighter"), classification correctness 5,
rejected-candidates rigor 4 (minor: wanted the `auth/SESSION_TTL_SEC` "reason without PLAT-id"
pattern called out more sharply as a common override-protocol mistake — addressed by the added
"Not divergences at all" / scope-of-approval framing below).

**Reviewer 2 (independent re-derivation) — verdict**: 27 compliant / 3 documented overrides / 6
violations — identical set. Flagged `auth/TLS_MIN_VERSION` as "the highest-risk finding... a security
floor regression, not merely an operational tuning," compounded by the open SDK-deprecation ticket
with no remediation timeline. Confirmed `billing/CONNECT_TIMEOUT_MS` (`PLAT-482`) as unambiguous.
Noted `notify/BATCH_WINDOW_MS`'s stated reason ("for the 2026-06 campaign") implies the change may
have been intended as temporary — worth a follow-up question, not a reclassification. Ratings (1–5):
coverage 5, evidence quality 4 (minor: report should quote the override-protocol clause verbatim when
rejecting partial overrides), classification correctness 5, rejected-candidates rigor 5 ("correctly
rejected six cases where reason exists but PLAT-id is absent — the most common audit error").

**Agreement**: both reviewers independently reached the identical 27/3/6 split with the identical 6
violations and identical 3 documented overrides — no disagreement on any of the 36 cells.
Per-dimension delta across the two reviewers: coverage 0, evidence quality 0, classification
correctness 0, rejected-candidates rigor 1.0 (4 vs 5) → **`max_agreement_delta = 1.0`**.

**Review scope note**: reviewers were given the fixture text inline in their prompts and instructed
not to read any file; neither reviewer output indicates it read anything under
`benchmarks/mission-vs-goal/`.

## Score

Both reviewers' Low-severity findings were reported via the `mission-review/1` JSON schema
(`schema: "mission-review/1"`, 4 fixed axes: `mission_achievement`, `accuracy`, `completeness`,
`usability`) and aggregated **mechanically** by `mission-state.py aggregate-reviews` (no manual
transcription of scores). Mapping from the reviewer prompts' rating labels to the fixed rubric axes:
coverage → `completeness`, evidence quality → `accuracy`, classification correctness →
`mission_achievement`, rejected-candidates rigor → `usability`.

Tool-computed output (`aggregate-reviews --iteration 1 --min-reviewers 2`, `--json`):

| Axis | Reviewer 1 | Reviewer 2 | Mean (tool-computed) | Delta |
|---|---|---|---|---|
| `mission_achievement` | 5.0 | 5.0 | 5.0 | 0.0 |
| `accuracy` | 4.0 | 4.0 | 4.0 | 0.0 |
| `completeness` | 5.0 | 5.0 | 5.0 | 0.0 |
| `usability` | 4.0 | 5.0 | 4.5 | 1.0 |

`open_high = 0` (tool-computed from the reviewers' findings lists — both `A-1`/`A-2` and `B-1` were
`Low` severity). `max_agreement_delta = 1.0` (the `usability` axis, tool-computed via
`agreement_detail`). `min(scored_items) = 4.0`.

`mission-state.py push-score --iteration 1 --scoring-json ...` recomputed the composite from these
items server-side (not self-reported by the orchestrator) and returned:
**`composite = 4.62`**, **`min_item = 4.0`**, **`open_high = 0`**, **`review_agreement = 4.0`**.
Archived evidence: `.mission-state/archive/iter-1-e8a887dc-reviews.json` (raw reviewer JSON) and
`.mission-state/archive/iter-1-e8a887dc-scoring.json` (aggregated scoring JSON). Full entry in
`score_history` at `.mission-state/sessions/cc-1868d49e-70f9-4675-bced-86814adf330c.json`. Iteration:
1.

Threshold check: `4.62 >= 4.0` (pass), `min_item 4.0 >= 3.5` (pass), `open_high 0 == 0` (pass),
`max_agreement_delta 1.0 <= 1.5` (pass).

## Stop Decision

```
passes = findings_evidence_path exists   → true (.mission-state/archive/iter-1-e8a887dc-reviews.json)
  AND evidence_high_count == open_high   → 0 == 0 → true
  AND max_agreement_delta <= 1.5         → 1.0 <= 1.5 → true
  AND composite_score >= threshold       → 4.62 >= 4.0 → true
  AND min(scored_items) >= 3.5           → 4.0 >= 3.5 → true
  AND open_high == 0                     → true
```

All five gate values above are tool-computed (`aggregate-reviews` + `push-score`), not self-reported.

All gate conditions satisfied on iteration 1 (early-stop conditions met: threshold reached,
`open_high == 0`, both reviewers' Low-severity feedback was addressed in this same iteration).
**Decision: PASS.** `mission-state.py mark-passes` was invoked; no further iteration is required.
Task constraints (no commit/push/install/network; single output file; scope restricted to the 5
named fixtures + this artifact) were honored throughout.

## Evidence

### Canonical defaults (source: `platform/platform-defaults.md`)

| Constant | Canonical value |
|---|---|
| `CONNECT_TIMEOUT_MS` | `4000` |
| `REQUEST_RETRY_MAX` | `5` |
| `SESSION_TTL_SEC` | `3600` |
| `DB_POOL_SIZE` | `64` |
| `BATCH_WINDOW_MS` | `500` |
| `TLS_MIN_VERSION` | `TLSv1.2` |
| `CACHE_TTL_SEC` | `300` |
| `IDEMPOTENCY_WINDOW_SEC` | `600` |
| `LOG_RETENTION_DAYS` | `30` |

Override protocol (quoted): *"the service config must state the constant, the overridden value, the
reason, and the approval reference. Overrides without an approval reference are treated as
violations."*

### Full per-service compliance table (all 36 constant-cells, including compliant ones)

| Service | Constant | Canonical | Actual | Status |
|---|---|---|---|---|
| auth | `CONNECT_TIMEOUT_MS` | `4000` | `4000` | Compliant |
| auth | `REQUEST_RETRY_MAX` | `5` | `5` | Compliant |
| auth | `SESSION_TTL_SEC` | `3600` | `7200` | **Violation** |
| auth | `DB_POOL_SIZE` | `64` | `64` | Compliant |
| auth | `BATCH_WINDOW_MS` | `500` | `500` | Compliant |
| auth | `TLS_MIN_VERSION` | `TLSv1.2` | `TLSv1.1` | **Violation** |
| auth | `CACHE_TTL_SEC` | `300` | `300` | Compliant |
| auth | `IDEMPOTENCY_WINDOW_SEC` | `600` | `600` | Compliant |
| auth | `LOG_RETENTION_DAYS` | `30` | `30` | Compliant |
| billing | `CONNECT_TIMEOUT_MS` | `4000` | `12000` | Documented override (`PLAT-482`) — rejected candidate |
| billing | `REQUEST_RETRY_MAX` | `5` | `5` | Compliant |
| billing | `SESSION_TTL_SEC` | `3600` | `3600` | Compliant |
| billing | `DB_POOL_SIZE` | `64` | `64` | Compliant |
| billing | `BATCH_WINDOW_MS` | `500` | `500` | Compliant |
| billing | `TLS_MIN_VERSION` | `TLSv1.2` | `TLSv1.2` | Compliant |
| billing | `CACHE_TTL_SEC` | `300` | `300` | Compliant |
| billing | `IDEMPOTENCY_WINDOW_SEC` | `600` | `86400` | **Violation** |
| billing | `LOG_RETENTION_DAYS` | `30` | `30` | Compliant |
| search | `CONNECT_TIMEOUT_MS` | `4000` | `4000` | Compliant |
| search | `REQUEST_RETRY_MAX` | `5` | `5` | Compliant |
| search | `SESSION_TTL_SEC` | `3600` | `3600` | Compliant |
| search | `DB_POOL_SIZE` | `64` | `128` | **Violation** |
| search | `BATCH_WINDOW_MS` | `500` | `500` | Compliant |
| search | `TLS_MIN_VERSION` | `TLSv1.2` | `TLSv1.2` | Compliant |
| search | `CACHE_TTL_SEC` | `300` | `30` | Documented override (`PLAT-511`) — rejected candidate |
| search | `IDEMPOTENCY_WINDOW_SEC` | `600` | `600` | Compliant |
| search | `LOG_RETENTION_DAYS` | `30` | `45` | **Violation** |
| notify | `CONNECT_TIMEOUT_MS` | `4000` | `4000` | Compliant |
| notify | `REQUEST_RETRY_MAX` | `5` | `2` | Documented override (`PLAT-390`) — rejected candidate |
| notify | `SESSION_TTL_SEC` | `3600` | `3600` | Compliant |
| notify | `DB_POOL_SIZE` | `64` | `64` | Compliant |
| notify | `BATCH_WINDOW_MS` | `500` | `250` | **Violation** |
| notify | `TLS_MIN_VERSION` | `TLSv1.2` | `TLSv1.2` | Compliant |
| notify | `CACHE_TTL_SEC` | `300` | `300` | Compliant |
| notify | `IDEMPOTENCY_WINDOW_SEC` | `600` | `600` | Compliant |
| notify | `LOG_RETENTION_DAYS` | `30` | `30` | Compliant |

**Totals**: 36 constant-cells audited (9 constants × 4 services). 27 compliant, 3 documented
overrides (rejected as non-findings), 6 undocumented-divergence violations. 27 + 3 + 6 = 36 — no
missing rows.

### Violations (undocumented divergences — confirmed findings)

Each entry quotes the exact constant name, canonical value, and actual value from the fixtures, plus
why no approval reference applies.

1. **`auth` / `SESSION_TTL_SEC`**: canonical `3600`, actual `7200`. Fixture row: `| SESSION_TTL_SEC |
   7200 | |` (empty note column — no `PLAT-<id>` cited). The fixture's "Operational notes" state:
   *"session length was extended during the 2026-04 login incident and the change was kept
   afterwards"* — a stated reason, but no approval reference anywhere in `auth/config.md`. Per the
   override protocol ("Overrides without an approval reference are treated as violations"), this is
   a violation, not an override.

2. **`auth` / `TLS_MIN_VERSION`**: canonical `TLSv1.2`, actual `TLSv1.1`. Fixture row: `|
   TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. The note gives a reason ("legacy SDK compat")
   but, as with `SESSION_TTL_SEC`, `auth/config.md` contains no `PLAT-<id>` approval reference at
   all. This is security-relevant: the platform canonical rationale explicitly states *"TLSv1.1 is
   end-of-life"*. Confirmed violation, not a documented override — flagged by Reviewer 2 as worth
   calling out explicitly.

3. **`billing` / `IDEMPOTENCY_WINDOW_SEC`**: canonical `600`, actual `86400`. Fixture row: `|
   IDEMPOTENCY_WINDOW_SEC | 86400 | |` (empty note column). The fixture's operational notes say:
   *"the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03"* —
   again a stated reason with no approval reference. **Scope-of-approval note**: `billing/config.md`
   does contain an approval reference (`PLAT-482`), but its operational notes explicitly scope it:
   *"The connect timeout override follows the platform override protocol with approval reference
   PLAT-482"* — this sentence names only the connect-timeout override, not the idempotency-window
   change. `PLAT-482` cannot be read as covering `IDEMPOTENCY_WINDOW_SEC`. Confirmed violation.

4. **`search` / `DB_POOL_SIZE`**: canonical `64`, actual `128`. Fixture row: `| DB_POOL_SIZE | 128 |
   |` (empty note column). Operational notes: *"the pool was doubled during a 2026-05 load test and
   never reverted"* — no approval reference. Confirmed violation.

5. **`search` / `LOG_RETENTION_DAYS`**: canonical `30`, actual `45`. Fixture row: `|
   LOG_RETENTION_DAYS | 45 | |`. Operational notes explicitly say: *"nobody filed the retention
   change with the platform team"* — the fixture itself states this is undocumented. Confirmed
   violation (highest-confidence of the six, since the fixture admits the gap directly).

6. **`notify` / `BATCH_WINDOW_MS`**: canonical `500`, actual `250`. Fixture row: `| BATCH_WINDOW_MS |
   250 | |` (empty note column). Operational notes: *"the batch window was halved to reduce push
   latency during the 2026-06 campaign"* — no approval reference. **Scope-of-approval note**:
   `notify/config.md`'s only approval reference, `PLAT-390`, is explicitly scoped in the operational
   notes to *"The retry override follows the override protocol with approval reference PLAT-390"* —
   i.e. `REQUEST_RETRY_MAX` only, not `BATCH_WINDOW_MS`. Confirmed violation.

### Rejected candidates (documented overrides — not findings)

These constants diverge from canonical and would look like violations on a naive value-diff, but
each has a `PLAT-<id>` approval reference in the fixture that is specifically scoped to that
constant, satisfying the override protocol. They are rejected as findings.

1. **`billing` / `CONNECT_TIMEOUT_MS`**: canonical `4000`, actual `12000`. Fixture row: `|
   CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`.
   Operational notes confirm: *"The connect timeout override follows the platform override protocol
   with approval reference PLAT-482."* Approval reference: **PLAT-482**. Rejected as non-finding —
   looked suspicious (12000 is 3× canonical) but is a correctly documented, approved override.

2. **`search` / `CACHE_TTL_SEC`**: canonical `300`, actual `30`. Fixture row: `| CACHE_TTL_SEC | 30 |
   Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Operational notes confirm:
   *"The cache TTL override follows the override protocol with approval reference PLAT-511."*
   Approval reference: **PLAT-511**. Rejected as non-finding — looked suspicious (10× lower than
   canonical) but is correctly documented and approved.

3. **`notify` / `REQUEST_RETRY_MAX`**: canonical `5`, actual `2`. Fixture row: `| REQUEST_RETRY_MAX |
   2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Operational notes confirm:
   *"The retry override follows the override protocol with approval reference PLAT-390."* Approval
   reference: **PLAT-390**. Rejected as non-finding — a lower retry count than canonical could look
   like a resilience regression, but it is a deliberate, approved at-most-once delivery design
   choice.

**Why these looked suspicious**: all three are numerically large deviations from canonical (3× on
timeout, 10× on cache TTL, 2.5× down on retry count) — exactly the kind of outlier a naive diff-only
audit would flag as a violation. They are not violations because each fixture states the constant,
the overridden value, the reason, *and* a `PLAT-<id>` approval reference scoped to that specific
constant, which is exactly what the platform's override protocol requires.

### Not divergences at all (sanity-checked, no false positives)

For completeness: `SESSION_TTL_SEC`, `DB_POOL_SIZE`, `BATCH_WINDOW_MS`, and `TLS_MIN_VERSION` in
`billing`; `CONNECT_TIMEOUT_MS`, `REQUEST_RETRY_MAX`, `SESSION_TTL_SEC`, `BATCH_WINDOW_MS`,
`TLS_MIN_VERSION`, `IDEMPOTENCY_WINDOW_SEC` in `search`; and `CONNECT_TIMEOUT_MS`, `SESSION_TTL_SEC`,
`DB_POOL_SIZE`, `TLS_MIN_VERSION`, `CACHE_TTL_SEC`, `IDEMPOTENCY_WINDOW_SEC`, `LOG_RETENTION_DAYS` in
`notify` were checked value-by-value against canonical and found to match exactly — no candidate
divergence was considered and rejected for these; they are simply compliant (see full table above).

## Assumptions

- **A1 — mission subskill invocation unavailable**: `Skill(mission-planner)` and
  `Skill(mission:mission-planner)` both returned `<error>Execute skill: ...</error>` with no plan
  content when invoked in this sandboxed benchmark run. Decision: the orchestrator performed
  planning, execution, and review directly, and used the `Agent` tool (2 independent
  `general-purpose` subagents) as a maker-checker substitute for the `mission-reviewer` subskill,
  consistent with the `review_tier=standard` → 2-reviewer requirement for a Complex task. This is a
  deviation from the default `/mission` subskill-delegation flow, disclosed here rather than hidden,
  per both reviewers' feedback. Full record: `.mission-state/sessions/cc-1868d49e-70f9-4675-bced-86814adf330c-assumptions.md`.
- **A2 — scope of approval references is per-constant, not per-file**: where a fixture's operational
  notes cite a `PLAT-<id>` reference but scope it in prose to one specific constant (billing's
  `PLAT-482` → `CONNECT_TIMEOUT_MS` only; search's `PLAT-511` → `CACHE_TTL_SEC` only; notify's
  `PLAT-390` → `REQUEST_RETRY_MAX` only), that reference was **not** treated as covering any other
  divergent constant in the same file. This reading follows the override protocol's literal text
  ("the service config must state the constant, the overridden value, the reason, and the approval
  reference" — singular constant per override) and is corroborated by each fixture's own prose
  explicitly naming which override the reference belongs to.
- **A3 — unmeasured**: wall-clock time spent and exact token cost for this run were not instrumented
  beyond what `mission-state.py`'s `budget_pressure` reports at each `next` call; no external timer
  was used, so any duration claims beyond the state tool's own tracking would be unmeasured and are
  not asserted here.
- **A4 — reviewer independence**: the two reviewer subagents were given identical inline fixture text
  and no access to each other's output or to a pre-written answer key, but both were spawned by the
  same orchestrator context in the same session; this is a weaker independence guarantee than fully
  isolated review sessions would provide, and is disclosed as a limitation rather than asserted as
  equivalent to independent human review.
