# portfolio-cx-config — Configuration Compliance Audit (claude_code_goal_command / rep3)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce a full per-service compliance table, a violations section with quoted evidence, and a rejected-candidates section.

## Result

All 9 canonical constants were audited across all 4 services (36 rows, no missing rows).

- **Compliant rows**: 27
- **Confirmed violations (undocumented divergences)**: 6
  - auth: `SESSION_TTL_SEC`, `TLS_MIN_VERSION`
  - billing: `IDEMPOTENCY_WINDOW_SEC`
  - search: `DB_POOL_SIZE`, `LOG_RETENTION_DAYS`
  - notify: `BATCH_WINDOW_MS`
- **Rejected candidates (documented overrides with approval references)**: 3
  - billing: `CONNECT_TIMEOUT_MS` (PLAT-482)
  - search: `CACHE_TTL_SEC` (PLAT-511)
  - notify: `REQUEST_RETRY_MAX` (PLAT-390)

Classification rule applied (from `platform-defaults.md`): "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Overrides without an approval reference are treated as violations."

### Full per-service compliance table

Canonical values (PLAT-CONFIG v4): CONNECT_TIMEOUT_MS=4000, REQUEST_RETRY_MAX=5, SESSION_TTL_SEC=3600, DB_POOL_SIZE=64, BATCH_WINDOW_MS=500, TLS_MIN_VERSION=TLSv1.2, CACHE_TTL_SEC=300, IDEMPOTENCY_WINDOW_SEC=600, LOG_RETENTION_DAYS=30.

#### auth-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (note "legacy SDK compat" has no approval reference) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected as non-finding |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected as non-finding |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence) |

#### notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected as non-finding |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (confirmed findings, with quoted evidence)

1. **auth-service `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`.
   Evidence (auth/config.md): `| SESSION_TTL_SEC | 7200 | |` — the Note column is empty (no approval reference). Operational notes confirm the divergence is informal: "session length was extended during the 2026-04 login incident and the change was kept afterwards." Canonical row (platform-defaults.md): `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`.

2. **auth-service `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`.
   Evidence (auth/config.md): `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` — the note "legacy SDK compat" contains no `PLAT-<id>` approval reference, so per the override protocol ("Overrides without an approval reference are treated as violations") this is a violation. Canonical row: `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`.

3. **billing-service `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`.
   Evidence (billing/config.md): `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` — empty Note, no approval reference. Operational notes: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" (no approval cited). Canonical row: `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`.

4. **search-service `DB_POOL_SIZE`** — canonical `64`, actual `128`.
   Evidence (search/config.md): `| DB_POOL_SIZE | 128 | |` — empty Note, no approval reference. Operational notes: "the pool was doubled during a 2026-05 load test and never reverted." Canonical row: `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`.

5. **search-service `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`.
   Evidence (search/config.md): `| LOG_RETENTION_DAYS | 45 | |` — empty Note, no approval reference. Operational notes: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Canonical row: `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`.

6. **notify-service `BATCH_WINDOW_MS`** — canonical `500`, actual `250`.
   Evidence (notify/config.md): `| BATCH_WINDOW_MS | 250 | |` — empty Note, no approval reference. Operational notes: "the batch window was halved to reduce push latency during the 2026-06 campaign" (no approval cited). Canonical row: `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Rejected because the divergence is a documented override with approval reference **PLAT-482**.
   Evidence (billing/config.md): `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |` and "The connect timeout override follows the platform override protocol with approval reference PLAT-482."

2. **search-service `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Rejected because the divergence is a documented override with approval reference **PLAT-511**.
   Evidence (search/config.md): `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |` and "The cache TTL override follows the override protocol with approval reference PLAT-511."

3. **notify-service `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Rejected because the divergence is a documented override with approval reference **PLAT-390**.
   Evidence (notify/config.md): `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |` and "The retry override follows the override protocol with approval reference PLAT-390."

## Evidence

- Read exactly the five fixture files named in the task prompt; no other files under `benchmarks/mission-vs-goal/` were opened:
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (9 canonical constants, override protocol)
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
- All quoted constant names, canonical values, actual values, notes, and approval references above are verbatim from those fixtures.
- Coverage check: 9 constants × 4 services = 36 audited rows; every canonical constant appears in every service table above.
- Unmeasured: nothing about runtime behavior, actual deployed values, or benchmark performance was measured — this audit is based solely on the five fixture documents.

## Assumptions

- The service config tables are the authoritative record of each service's actual configuration (the task provides no runtime data).
- A note without a `PLAT-<id>` approval reference (e.g. auth's "legacy SDK compat") does not satisfy the override protocol, per platform-defaults.md: "Overrides without an approval reference are treated as violations."
- Operational-notes mentions of changes (incidents, load tests, campaigns) do not constitute approval references.
- No service defines constants beyond the 9 canonical ones, so the audit universe is exactly the canonical constant set.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-config-claude_code_goal_command-rep3.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering all 9 canonical constants for all 4 services, a violations section with quoted evidence for each of the 6 confirmed violations, and a rejected-candidates section citing the approval reference for each of the 3 documented overrides. Task complete; no further edits required.
