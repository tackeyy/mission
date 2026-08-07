# Task Artifact: portfolio-cx-config (arm: claude_code_goal_command, rep1)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (PLAT-CONFIG v4). For every canonical constant in every service, classify the value as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding, with its approval reference cited). Produce a complete per-service compliance table, a violations section with quoted evidence, and a rejected-candidates section.

## Result

All 9 canonical constants were audited across all 4 services (36 rows total, no missing rows).

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6**
  - auth: `SESSION_TTL_SEC` (7200 vs canonical 3600), `TLS_MIN_VERSION` (TLSv1.1 vs canonical TLSv1.2)
  - billing: `IDEMPOTENCY_WINDOW_SEC` (86400 vs canonical 600)
  - search: `DB_POOL_SIZE` (128 vs canonical 64), `LOG_RETENTION_DAYS` (45 vs canonical 30)
  - notify: `BATCH_WINDOW_MS` (250 vs canonical 500)
- **Rejected candidates (documented overrides with approval references, non-findings): 3**
  - billing: `CONNECT_TIMEOUT_MS` = 12000 (approved PLAT-482)
  - search: `CACHE_TTL_SEC` = 30 (approved PLAT-511)
  - notify: `REQUEST_RETRY_MAX` = 2 (approved PLAT-390)

Classification rule applied (from `platform-defaults.md`): "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Overrides without an approval reference are treated as violations."

### Full per-service compliance table

Canonical values (PLAT-CONFIG v4): CONNECT_TIMEOUT_MS=4000, REQUEST_RETRY_MAX=5, SESSION_TTL_SEC=3600, DB_POOL_SIZE=64, BATCH_WINDOW_MS=500, TLS_MIN_VERSION=TLSv1.2, CACHE_TTL_SEC=300, IDEMPOTENCY_WINDOW_SEC=600, LOG_RETENTION_DAYS=30.

#### auth-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence, no approval reference) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (note "legacy SDK compat" has no approval reference) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — **rejected** (approved PLAT-482) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence, no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence, no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override — **rejected** (approved PLAT-511) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence, no approval reference) |

#### notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — **rejected** (approved PLAT-390) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence, no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Violations (confirmed findings, with quoted evidence)

1. **auth-service `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`.
   - Fixture row (auth/config.md): `| SESSION_TTL_SEC | 7200 | |` — the Note column is empty; no approval reference.
   - Operational notes confirm it is an unfiled change: "session length was extended during the 2026-04 login incident and the change was kept afterwards."
   - Canonical row (platform-defaults.md): `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`

2. **auth-service `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`.
   - Fixture row (auth/config.md): `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` — a reason is given but there is no `PLAT-<id>` approval reference, so per the override protocol ("Overrides without an approval reference are treated as violations") this is a violation.
   - Operational notes: "The TLS floor is pinned for an older mobile SDK; the SDK deprecation ticket is still open."
   - Canonical row: `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`

3. **billing-service `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`.
   - Fixture row (billing/config.md): `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` — empty Note, no approval reference.
   - Operational notes confirm: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03." No approval reference is cited for this change.
   - Canonical row: `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`

4. **search-service `DB_POOL_SIZE`** — canonical `64`, actual `128`.
   - Fixture row (search/config.md): `| DB_POOL_SIZE | 128 | |` — empty Note, no approval reference.
   - Operational notes confirm: "the pool was doubled during a 2026-05 load test and never reverted."
   - Canonical row: `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`

5. **search-service `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`.
   - Fixture row (search/config.md): `| LOG_RETENTION_DAYS | 45 | |` — empty Note, no approval reference.
   - Operational notes confirm it is unfiled: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team."
   - Canonical row: `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`

6. **notify-service `BATCH_WINDOW_MS`** — canonical `500`, actual `250`.
   - Fixture row (notify/config.md): `| BATCH_WINDOW_MS | 250 | |` — empty Note, no approval reference.
   - Operational notes confirm: "the batch window was halved to reduce push latency during the 2026-06 campaign." No approval reference is cited for this change.
   - Canonical row: `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`

## Rejected candidates (documented overrides — non-findings)

These diverge from the canonical values but follow the override protocol (constant, overridden value, reason, and `PLAT-<id>` approval reference stated in the service config), so they are rejected as non-findings:

1. **billing-service `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Approval reference: **PLAT-482**.
   - Fixture row (billing/config.md): `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`
   - Operational notes: "The connect timeout override follows the platform override protocol with approval reference PLAT-482."

2. **search-service `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Approval reference: **PLAT-511**.
   - Fixture row (search/config.md): `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`
   - Operational notes: "The cache TTL override follows the override protocol with approval reference PLAT-511."

3. **notify-service `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Approval reference: **PLAT-390**.
   - Fixture row (notify/config.md): `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`
   - Operational notes: "The retry override follows the override protocol with approval reference PLAT-390."

## Evidence

- All classifications above quote the exact constant name, canonical value, and actual value from the five fixture files read for this task:
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (canonical defaults, PLAT-CONFIG v4, 9 constants)
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
- Governing rule quoted from platform-defaults.md: "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Override protocol: the service config must state the constant, the overridden value, the reason, and the approval reference. Overrides without an approval reference are treated as violations."
- Coverage check: 9 canonical constants × 4 services = 36 rows; all 36 rows are present in the per-service tables above (27 compliant + 6 violations + 3 rejected overrides = 36).
- Unmeasured: runtime behavior of the services, whether the config files reflect deployed values, and the validity/currency of the cited approval tickets (PLAT-482, PLAT-511, PLAT-390) were not verified — only the fixture text was audited.

## Assumptions

- Each service config table is the complete set of constants for that service; each contains exactly the 9 canonical constants and no service-specific extras, so no extra-constant rows were needed.
- A reason in the Note column without a `PLAT-<id>` reference (auth's `TLS_MIN_VERSION` "legacy SDK compat") does not satisfy the override protocol and is therefore a violation, per the explicit rule "Overrides without an approval reference are treated as violations."
- The approval references quoted in the fixtures (PLAT-482, PLAT-511, PLAT-390) are taken at face value as valid approvals; their existence in an external ticket system is unmeasured.
- Only the five named fixture files were read; no other files under `benchmarks/mission-vs-goal/` were opened, per the run rules.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-config-claude_code_goal_command-rep1.md` and includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering every canonical constant for all four services, a violations section with quoted evidence, and a rejected-candidates section citing each documented override's approval reference. No commits, pushes, package installs, or network access were performed; edits were limited to this single output file. The goal condition is met and the run stops here.
