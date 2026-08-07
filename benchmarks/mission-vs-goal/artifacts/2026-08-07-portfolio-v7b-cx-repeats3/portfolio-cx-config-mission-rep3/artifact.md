# portfolio-cx-config — Configuration Compliance Audit (mission arm, rep3)

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against
the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service,
classify as compliant, undocumented divergence (violation), or documented override
(approval reference cited; rejected as a non-finding). Task id: `portfolio-cx-config`,
arm: mission, profile: full, max-iter 3.

- Mission state: `.mission-state/sessions/cc-e2bcecae-48d7-4429-b296-10f539d831a8.json` (mission_id `e0da5545bb728583`, complexity Complex)
- Fixtures read (exactly the five named in the task prompt):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

## Plan

Iteration-1 plan (mission-planner, recorded in mission state assumptions):

1. Read the 5 fixtures (done before planning was finalized; read-only).
2. Build the canonical map: 9 constants from `platform-defaults.md`.
3. Build the full compliance table: 4 services × 9 constants = 36 rows, each classified
   Compliant / Violation / Documented override.
4. Violations section: every divergence without a `PLAT-<id>` approval reference, with
   constant name, canonical value, actual value, and verbatim fixture quotes.
5. Rejected-candidates section: every divergence carrying a `PLAT-<id>` approval
   reference, rejected as a non-finding with the reference cited.
6. Self-check coverage (36 rows, violation count, `PLAT-` citations) before review.
7. Scored review: 2 independent reviewers (Complex tier) in one parallel message →
   `review-finalize` → `closeout`.

Decision rule (from `platform-defaults.md` override protocol): "the service config must
state the constant, the overridden value, the reason, and the approval reference.
Overrides without an approval reference are treated as violations."

## Execution

### Canonical defaults (platform-defaults.md, PLAT-CONFIG v4)

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

### Per-service compliance table (4 services × 9 constants = 36 rows)

#### auth-service (`config-sprawl/auth/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (no approval reference) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (note "legacy SDK compat" has no approval reference) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (`config-sprawl/billing/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected as non-finding |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service (`config-sprawl/search/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected as non-finding |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (no approval reference) |

#### notify-service (`config-sprawl/notify/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected as non-finding |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

Coverage check: 36/36 rows present (9 constants × 4 services). Compliant 27, violations 6,
documented overrides 3.

### Violations (confirmed findings — undocumented divergences, 6)

Each entry quotes the exact constant name, canonical value, actual value, and the fixture
evidence. None of these carries a `PLAT-<id>` approval reference, so per the override
protocol each is a compliance violation.

1. **auth-service `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`.
   Evidence: `auth/config.md` row "`| SESSION_TTL_SEC | 7200 | |`" (empty Note column);
   operational notes: "session length was extended during the 2026-04 login incident and
   the change was kept afterwards" — no approval reference.
2. **auth-service `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`.
   Evidence: `auth/config.md` row "`| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`";
   operational notes: "The TLS floor is pinned for an older mobile SDK; the SDK
   deprecation ticket is still open" — a reason is stated but no approval reference.
   Canonical rationale: "Security baseline; TLSv1.1 is end-of-life".
3. **billing-service `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`.
   Evidence: `billing/config.md` row "`| IDEMPOTENCY_WINDOW_SEC | 86400 | |`" (empty
   Note); operational notes: "the idempotency window was widened while debugging duplicate
   settlement webhooks in 2026-03" — no approval reference.
4. **search-service `DB_POOL_SIZE`** — canonical `64`, actual `128`.
   Evidence: `search/config.md` row "`| DB_POOL_SIZE | 128 | |`" (empty Note);
   operational notes: "the pool was doubled during a 2026-05 load test and never
   reverted" — no approval reference.
5. **search-service `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`.
   Evidence: `search/config.md` row "`| LOG_RETENTION_DAYS | 45 | |`" (empty Note);
   operational notes: "Query logs are kept 45 days to debug relevance regressions; nobody
   filed the retention change with the platform team" — explicitly unfiled, no approval
   reference. Canonical rationale: "Data-minimization policy DM-9".
6. **notify-service `BATCH_WINDOW_MS`** — canonical `500`, actual `250`.
   Evidence: `notify/config.md` row "`| BATCH_WINDOW_MS | 250 | |`" (empty Note);
   operational notes: "the batch window was halved to reduce push latency during the
   2026-06 campaign" — no approval reference.

### Rejected candidates (documented overrides — non-findings, 3)

These diverge from canonical values but follow the override protocol (constant, value,
reason, approval reference stated), so they are rejected as non-findings.

1. **billing-service `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`.
   Approval reference: **PLAT-482**. Evidence: `billing/config.md` Note "Override: PSP
   provider p99 latency is 9s; approved PLAT-482"; operational notes: "The connect timeout
   override follows the platform override protocol with approval reference PLAT-482."
2. **search-service `CACHE_TTL_SEC`** — canonical `300`, actual `30`.
   Approval reference: **PLAT-511**. Evidence: `search/config.md` Note "Override:
   suggestion freshness SLA requires 30s; approved PLAT-511"; operational notes: "The
   cache TTL override follows the override protocol with approval reference PLAT-511."
3. **notify-service `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`.
   Approval reference: **PLAT-390**. Evidence: `notify/config.md` Note "Override:
   at-most-once delivery guarantee; approved PLAT-390"; operational notes: "The retry
   override follows the override protocol with approval reference PLAT-390."

## Review

(To be completed after the scored review iteration — reviewer JSONs are archived under
`.mission-state/`.)

## Score

(To be completed after `review-finalize`.)

## Stop Decision

(To be completed after `closeout`.)

## Evidence

- All quoted constant names and values above are verbatim from the five named fixtures;
  file references are given per finding. No other files under `benchmarks/mission-vs-goal/`
  were opened.
- Mission state session: `.mission-state/sessions/cc-e2bcecae-48d7-4429-b296-10f539d831a8.json`;
  assumptions log: `.mission-state/sessions/cc-e2bcecae-48d7-4429-b296-10f539d831a8-assumptions.md`.
- Unmeasured: runtime/latency of this run, token cost, and any comparison to the goal arm
  are unmeasured and not claimed.

## Assumptions

- Classification rule: a divergence counts as a documented override only when the service
  config cites a `PLAT-<id>` approval reference (per the override protocol in
  `platform-defaults.md`); a stated reason without an approval reference (e.g. auth's
  "legacy SDK compat") is still a violation.
- Constants listed in a service config that match the canonical value are "Compliant";
  no service omitted any of the 9 canonical constants, so no missing-row audit failures.
- Local authoring sync for `MISSION_PLUGIN_ROOT` was skipped (benchmark forbids network
  access); the repo-root `scripts/mission-state.py` was used, which the skill designates
  as authoritative at the repository root.
- The artifact was written directly by the orchestrator (no executor subagent) to keep
  fixture transcription exact; quality is gated by the 2-reviewer scored review.
