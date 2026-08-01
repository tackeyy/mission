# portfolio-cx-config — Mission Artifact (Arm: mission)

## Mission

Audit configuration compliance for four services (`auth-service`, `billing-service`,
`search-service`, `notify-service`) against the platform canonical defaults
(PLAT-CONFIG v4), using only the five named fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

Deliverable: a complete per-service compliance table, a violations section with
quoted evidence, and a rejected-candidates section for documented overrides
(with approval reference).

Mission complexity: **Complex** (cross-service audit, 4 services × 9 constants
= 36 cells, requires distinguishing compliant vs. undocumented divergence vs.
documented override — a judgment call per cell, not a single lookup).

State tracking: `.mission-state/sessions/cc-ad2c881b-dc55-44d3-96ad-2f658e45443a.json`
(`mission_id: 2bd4fe70c66046ab`).

## Plan

1. Read the platform defaults fixture and extract the canonical value for each
   of the 9 constants.
2. Read each of the 4 service config fixtures and extract the actual value and
   note field for each constant.
3. For every (service, constant) cell, classify as:
   - **Compliant** — actual value equals canonical value.
   - **Documented override** — actual value differs, and the note cites an
     approval reference matching the `PLAT-<id>` pattern. → non-finding,
     listed in "Rejected candidates".
   - **Violation (undocumented divergence)** — actual value differs and no
     approval reference is cited (including cases with an operational
     rationale but no `PLAT-<id>` citation). → listed in "Violations".
4. Build one compliance table per service covering all 9 constants (no row
   omitted).
5. Self-review: re-check every non-compliant cell against the override
   protocol text in the platform-defaults fixture ("Overrides without an
   approval reference are treated as violations") before finalizing
   classification.
6. Write this artifact; record score and stop decision in mission state.

No planner/executor/reviewer sub-agents were spawned for this task: the work
is a deterministic 4×9 lookup-and-classify job with a single ground truth
source (the 5 named fixtures), not a design or code-change task where
independent multi-agent review adds signal. The self-review in step 5
substitutes for peer review and is captured verbatim in the Review section
below.

## Execution

### Canonical values (from `platform/platform-defaults.md`)

| Constant | Canonical value |
|---|---|
| CONNECT_TIMEOUT_MS | 4000 |
| REQUEST_RETRY_MAX | 5 |
| SESSION_TTL_SEC | 3600 |
| DB_POOL_SIZE | 64 |
| BATCH_WINDOW_MS | 500 |
| TLS_MIN_VERSION | TLSv1.2 |
| CACHE_TTL_SEC | 300 |
| IDEMPOTENCY_WINDOW_SEC | 600 |
| LOG_RETENTION_DAYS | 30 |

Override protocol (quoted): "the service config must state the constant, the
overridden value, the reason, and the approval reference. Overrides without
an approval reference are treated as violations."

### Per-service compliance tables

#### auth-service (`auth/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (undocumented) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (`billing/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service (`search/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented) |

#### notify-service (`notify/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

All 4 services × 9 constants = 36 cells accounted for; no missing rows.

## Review

Self-review pass performed against the override protocol text before
finalizing (per Plan step 5):

- **auth-service `SESSION_TTL_SEC`**: fixture note says "session length was
  extended during the 2026-04 login incident and the change was kept
  afterwards." No `PLAT-<id>` approval reference anywhere in the note or the
  constant's own note field (which is blank). Confirmed as undocumented
  violation, not an override.
- **auth-service `TLS_MIN_VERSION`**: constant's own note says "legacy SDK
  compat"; operational notes add "the SDK deprecation ticket is still open."
  Neither cites a `PLAT-<id>` reference. A stated *reason* without an approval
  reference does not meet the override protocol ("must state ... the approval
  reference"). Confirmed as undocumented violation.
- **billing-service `IDEMPOTENCY_WINDOW_SEC`**: operational notes explain the
  window "was widened while debugging duplicate settlement webhooks in
  2026-03" but cite no approval reference (contrast with the same file's
  `CONNECT_TIMEOUT_MS` row, which explicitly names "approval reference
  PLAT-482"). Confirmed as undocumented violation.
- **billing-service `CONNECT_TIMEOUT_MS`**: note reads "Override: PSP provider
  p99 latency is 9s; approved PLAT-482," and the operational notes restate
  "approval reference PLAT-482." Meets all four override-protocol elements
  (constant, value, reason, approval reference). Confirmed as documented
  override — rejected as a non-finding.
- **search-service `DB_POOL_SIZE`**: operational notes say the pool "was
  doubled during a 2026-05 load test and never reverted" — no approval
  reference. Confirmed as undocumented violation.
- **search-service `LOG_RETENTION_DAYS`**: operational notes explicitly state
  "nobody filed the retention change with the platform team" — an explicit
  admission of no approval reference. Confirmed as undocumented violation.
- **search-service `CACHE_TTL_SEC`**: note reads "Override: suggestion
  freshness SLA requires 30s; approved PLAT-511," restated in operational
  notes as "approval reference PLAT-511." Confirmed as documented override —
  rejected.
- **notify-service `BATCH_WINDOW_MS`**: operational notes say the window "was
  halved to reduce push latency during the 2026-06 campaign" — no approval
  reference cited anywhere in the file for this constant. Confirmed as
  undocumented violation.
- **notify-service `REQUEST_RETRY_MAX`**: note reads "Override: at-most-once
  delivery guarantee; approved PLAT-390," restated in operational notes as
  "approval reference PLAT-390." Confirmed as documented override — rejected.

No disagreements found between the initial classification pass and this
review pass; no cells required reclassification.

## Score

| Dimension | Assessment |
|---|---|
| Coverage | 36/36 cells classified across 4 services × 9 constants; no missing rows |
| Evidence quality | Every non-compliant cell quotes the exact fixture line/note supporting its classification (see Evidence) |
| Classification correctness | Verified against the explicit override-protocol text in the platform fixture during self-review |
| Composite score (self-assessed, single-reviewer) | 4.5 / 5.0 — full coverage and quoted evidence; not independently peer-reviewed by a second agent, so confidence is high but not multi-source-verified |

This is a self-assessed score from a single execution + self-review pass, not
a multi-reviewer `mission-review/1` aggregate. No `mission-scorer` /
`review-finalize` pipeline was run because no reviewer sub-agents were
spawned (see Plan rationale). This is stated explicitly rather than
fabricating a reviewer-aggregate score.

## Stop Decision

**Stop: task complete.** All required sections are populated, every canonical
constant is covered for all 4 services, every violation cites quoted
fixture evidence, and every documented override cites its approval reference
and is placed in the rejected-candidates list rather than the violations
list. No blockers, no missing fixtures, no ambiguous cells remained after
self-review. Iteration count: 1 of 3 allowed (`--max-iter 3`); stopped early
because coverage and evidence requirements were met on the first pass — no
unresolved High-severity gap justified a second iteration.

## Evidence

### Violations (undocumented divergence — confirmed findings)

1. **auth-service / `SESSION_TTL_SEC`**: canonical `3600`, actual `7200`.
   Quote: "SESSION_TTL_SEC | 7200" (table row); "session length was extended
   during the 2026-04 login incident and the change was kept afterwards" (no
   approval reference).
2. **auth-service / `TLS_MIN_VERSION`**: canonical `TLSv1.2`, actual
   `TLSv1.1`. Quote: "TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat"; "the SDK
   deprecation ticket is still open" (no approval reference).
3. **billing-service / `IDEMPOTENCY_WINDOW_SEC`**: canonical `600`, actual
   `86400`. Quote: "IDEMPOTENCY_WINDOW_SEC | 86400"; "the idempotency window
   was widened while debugging duplicate settlement webhooks in 2026-03" (no
   approval reference).
4. **search-service / `DB_POOL_SIZE`**: canonical `64`, actual `128`. Quote:
   "DB_POOL_SIZE | 128"; "the pool was doubled during a 2026-05 load test and
   never reverted" (no approval reference).
5. **search-service / `LOG_RETENTION_DAYS`**: canonical `30`, actual `45`.
   Quote: "LOG_RETENTION_DAYS | 45"; "nobody filed the retention change with
   the platform team."
6. **notify-service / `BATCH_WINDOW_MS`**: canonical `500`, actual `250`.
   Quote: "BATCH_WINDOW_MS | 250"; "the batch window was halved to reduce
   push latency during the 2026-06 campaign" (no approval reference).

Total confirmed violations: **6**.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service / `CONNECT_TIMEOUT_MS`**: canonical `4000`, actual
   `12000`. Approval reference: **PLAT-482**. Quote: "Override: PSP provider
   p99 latency is 9s; approved PLAT-482"; "The connect timeout override
   follows the platform override protocol with approval reference PLAT-482."
   Rejected — documented override, not a finding.
2. **search-service / `CACHE_TTL_SEC`**: canonical `300`, actual `30`.
   Approval reference: **PLAT-511**. Quote: "Override: suggestion freshness
   SLA requires 30s; approved PLAT-511"; "The cache TTL override follows the
   override protocol with approval reference PLAT-511." Rejected — documented
   override, not a finding.
3. **notify-service / `REQUEST_RETRY_MAX`**: canonical `5`, actual `2`.
   Approval reference: **PLAT-390**. Quote: "Override: at-most-once delivery
   guarantee; approved PLAT-390"; "The retry override follows the override
   protocol with approval reference PLAT-390." Rejected — documented
   override, not a finding.

Total rejected candidates (documented overrides): **3**.

### Compliant cells (no finding)

27 of 36 cells matched the canonical value exactly and required no
classification beyond "Compliant" (see per-service tables above for the full
list; not repeated here to avoid duplicating the tables as prose).

## Assumptions

- "Approval reference" is interpreted strictly as a citation matching the
  `PLAT-<id>` pattern named in the platform-defaults override protocol (e.g.
  `PLAT-482`, `PLAT-511`, `PLAT-390`). A stated operational *reason* without
  such a citation (auth-service `SESSION_TTL_SEC`, `TLS_MIN_VERSION`;
  billing-service `IDEMPOTENCY_WINDOW_SEC`; search-service `DB_POOL_SIZE`,
  `LOG_RETENTION_DAYS`; notify-service `BATCH_WINDOW_MS`) is treated as an
  undocumented divergence per the fixture's own text: "Overrides without an
  approval reference are treated as violations."
- No sub-agents (planner/executor/reviewer) were spawned for this task. This
  is a deliberate scoping decision (stated in Plan), not an omission — the
  task is a deterministic classification job against a single fixed ground
  truth, and the required self-review step (Review section) covers the same
  correctness risk a second reviewer would check (protocol-text compliance
  per cell). This is unmeasured against an independent second opinion; stated
  explicitly rather than claimed as multi-reviewer-verified.
- Per the task's out-of-bounds rule, no file under `benchmarks/mission-vs-goal/`
  other than the 5 named fixtures and this output file was opened, read,
  grepped, or listed. No benchmark task-definition, scoring-config, or
  answer-key file was consulted in producing this audit.
- No commit, push, package install, or network access was performed. Only
  this artifact file (and `.mission-state/` for mission process tracking)
  was written.
