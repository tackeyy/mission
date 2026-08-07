# Configuration Compliance Audit — portfolio-cx-config (claude_code_goal_command, rep1)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce the artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-cx-config-claude_code_goal_command-rep1.md` with headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

All 5 fixture files were read (platform defaults plus 4 service configs). The platform canon defines 9 constants; all 4 services define all 9 constants, so the audit covers 36 rows with no missing rows.

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6** — auth `SESSION_TTL_SEC`, auth `TLS_MIN_VERSION`, billing `IDEMPOTENCY_WINDOW_SEC`, search `DB_POOL_SIZE`, search `LOG_RETENTION_DAYS`, notify `BATCH_WINDOW_MS`
- **Rejected candidates (documented overrides with approval references): 3** — billing `CONNECT_TIMEOUT_MS` (PLAT-482), search `CACHE_TTL_SEC` (PLAT-511), notify `REQUEST_RETRY_MAX` (PLAT-390)

### Full per-service compliance table

Canonical values per `platform/platform-defaults.md`: `CONNECT_TIMEOUT_MS = 4000`, `REQUEST_RETRY_MAX = 5`, `SESSION_TTL_SEC = 3600`, `DB_POOL_SIZE = 64`, `BATCH_WINDOW_MS = 500`, `TLS_MIN_VERSION = TLSv1.2`, `CACHE_TTL_SEC = 300`, `IDEMPOTENCY_WINDOW_SEC = 600`, `LOG_RETENTION_DAYS = 30`.

#### auth-service

| Constant | Canonical value | Actual value | Classification |
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

| Constant | Canonical value | Actual value | Classification |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (approved PLAT-482) — rejected as non-finding |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence, no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical value | Actual value | Classification |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence, no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (approved PLAT-511) — rejected as non-finding |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence, no approval reference) |

#### notify-service

| Constant | Canonical value | Actual value | Classification |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (approved PLAT-390) — rejected as non-finding |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence, no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (confirmed findings, with quoted evidence)

1. **auth-service `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`. Config row: `| SESSION_TTL_SEC | 7200 | |` (empty Note column, no approval reference). Operational notes confirm: "session length was extended during the 2026-04 login incident and the change was kept afterwards." No `PLAT-<id>` approval is cited, so this is an undocumented divergence.
2. **auth-service `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`. Config row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. The note gives a reason but no approval reference; platform defaults state "Overrides without an approval reference are treated as violations" and "TLSv1.1 is end-of-life."
3. **billing-service `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`. Config row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (empty Note column). Operational notes: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference is cited.
4. **search-service `DB_POOL_SIZE`** — canonical `64`, actual `128`. Config row: `| DB_POOL_SIZE | 128 | |` (empty Note column). Operational notes: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference.
5. **search-service `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`. Config row: `| LOG_RETENTION_DAYS | 45 | |` (empty Note column). Operational notes: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Explicitly unfiled, hence a violation of data-minimization policy DM-9's canonical value.
6. **notify-service `BATCH_WINDOW_MS`** — canonical `500`, actual `250`. Config row: `| BATCH_WINDOW_MS | 250 | |` (empty Note column). Operational notes: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Rejected because the config documents the override with an approval reference: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`. Approval reference: **PLAT-482**.
2. **search-service `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Rejected because the config documents the override with an approval reference: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Approval reference: **PLAT-511**.
3. **notify-service `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Rejected because the config documents the override with an approval reference: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Approval reference: **PLAT-390**.

## Evidence

- Canonical source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (`PLAT-CONFIG v4`). Override protocol quoted: "the service config must state the constant, the overridden value, the reason, and the approval reference. Overrides without an approval reference are treated as violations."
- Service configs read: `.../config-sprawl/auth/config.md`, `.../config-sprawl/billing/config.md`, `.../config-sprawl/search/config.md`, `.../config-sprawl/notify/config.md`.
- Every classification above quotes the exact constant name, canonical value, and actual value directly from these fixture tables; violation entries additionally quote the fixture's own table row and operational note.
- Coverage check: 9 canonical constants × 4 services = 36 rows; all 36 appear in the per-service tables above (9 rows in each table). No missing rows.
- Unmeasured: nothing beyond the five named fixture files was examined. Runtime behavior, actual deployed values, and the validity/currency of the PLAT-482/PLAT-511/PLAT-390 approvals were not verified (no external systems were consulted, per the no-network rule) — these are unmeasured.

## Assumptions

- The five fixture files are the sole source of truth for this audit; approval references (`PLAT-482`, `PLAT-511`, `PLAT-390`) are taken at face value as valid approvals since the override protocol only requires that the reference be stated in the service config.
- A note giving a reason without a `PLAT-<id>` reference (e.g. auth's "legacy SDK compat") does not satisfy the override protocol, per the platform defaults' explicit rule.
- Service config tables list each service's complete constant set; no constants beyond the 9 canonical ones were present in any service config.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-cx-config-claude_code_goal_command-rep1.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering all 9 canonical constants for all 4 services, a violations section with quoted evidence, and a rejected-candidates section citing each documented override's approval reference. The task is complete; no further work is required.
