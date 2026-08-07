# Configuration Compliance Audit — portfolio-cx-config (claude_code_goal_command, rep3)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce the artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-config-claude_code_goal_command-rep3.md` with headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

All 9 canonical constants were audited across all 4 services (36 rows total).

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6**
- **Rejected candidates (documented overrides with approval references): 3**

### Per-service compliance tables

Canonical values are from `platform/platform-defaults.md` (PLAT-CONFIG v4).

#### auth-service (`auth/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (no approval reference) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (note "legacy SDK compat" lacks approval reference) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (`billing/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — **rejected candidate** (approved PLAT-482) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service (`search/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override — **rejected candidate** (approved PLAT-511) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (no approval reference) |

#### notify-service (`notify/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — **rejected candidate** (approved PLAT-390) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (confirmed findings)

1. **auth-service `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`. Config row: `| SESSION_TTL_SEC | 7200 | |` (empty note, no approval reference). Operational notes admit: "session length was extended during the 2026-04 login incident and the change was kept afterwards" — no `PLAT-<id>` cited.
2. **auth-service `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`. Config row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. The note gives a reason but no approval reference; per platform defaults, "Overrides without an approval reference are treated as violations." Canonical rationale: "Security baseline; TLSv1.1 is end-of-life".
3. **billing-service `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`. Config row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (empty note). Operational notes: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference.
4. **search-service `DB_POOL_SIZE`** — canonical `64`, actual `128`. Config row: `| DB_POOL_SIZE | 128 | |` (empty note). Operational notes: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference.
5. **search-service `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`. Config row: `| LOG_RETENTION_DAYS | 45 | |` (empty note). Operational notes: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Canonical rationale: "Data-minimization policy DM-9".
6. **notify-service `BATCH_WINDOW_MS`** — canonical `500`, actual `250`. Config row: `| BATCH_WINDOW_MS | 250 | |` (empty note). Operational notes: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Config note: "Override: PSP provider p99 latency is 9s; approved PLAT-482". Operational notes confirm: "The connect timeout override follows the platform override protocol with approval reference PLAT-482." Approval reference: **PLAT-482**. Rejected as a non-finding.
2. **search-service `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Config note: "Override: suggestion freshness SLA requires 30s; approved PLAT-511". Operational notes confirm: "The cache TTL override follows the override protocol with approval reference PLAT-511." Approval reference: **PLAT-511**. Rejected as a non-finding.
3. **notify-service `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Config note: "Override: at-most-once delivery guarantee; approved PLAT-390". Operational notes confirm: "The retry override follows the override protocol with approval reference PLAT-390." Approval reference: **PLAT-390**. Rejected as a non-finding.

## Evidence

All evidence quoted directly from the five fixture files (the only files read under `benchmarks/mission-vs-goal/` besides this output file):

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` — canonical table (PLAT-CONFIG v4) listing all 9 constants: `CONNECT_TIMEOUT_MS 4000`, `REQUEST_RETRY_MAX 5`, `SESSION_TTL_SEC 3600`, `DB_POOL_SIZE 64`, `BATCH_WINDOW_MS 500`, `TLS_MIN_VERSION TLSv1.2`, `CACHE_TTL_SEC 300`, `IDEMPOTENCY_WINDOW_SEC 600`, `LOG_RETENTION_DAYS 30`. Override protocol: "the service config must state the constant, the overridden value, the reason, and the approval reference. Overrides without an approval reference are treated as violations."
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md` — rows `SESSION_TTL_SEC | 7200`, `TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat`; remaining 7 rows match canonical values.
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md` — rows `CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482`, `IDEMPOTENCY_WINDOW_SEC | 86400`; remaining 7 rows match canonical values.
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md` — rows `DB_POOL_SIZE | 128`, `CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511`, `LOG_RETENTION_DAYS | 45`; remaining 6 rows match canonical values.
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md` — rows `REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390`, `BATCH_WINDOW_MS | 250`; remaining 7 rows match canonical values.

Unmeasured: no runtime behavior, deployed configuration state, or the actual existence/content of approval tickets PLAT-482 / PLAT-511 / PLAT-390 was verified — the audit is limited to the fixture documents as written.

## Assumptions

- The 9 constants in `platform-defaults.md` define the complete canonical set; each service config lists exactly those 9 constants, so no constants are missing from any service (verified by row count in each fixture).
- A note giving a reason but no `PLAT-<id>` approval reference (e.g. auth's "legacy SDK compat") counts as a violation, per the platform defaults' explicit rule: "Overrides without an approval reference are treated as violations."
- Operational-notes prose mentioning an approval reference is accepted as corroborating the config-table note; the underlying approval tickets themselves were not (and could not be) verified.
- Values equal to canonical are compliant regardless of notes.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-config-claude_code_goal_command-rep3.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering all 9 canonical constants for all 4 services (36 rows), a violations section with quoted evidence for each of the 6 undocumented divergences, and a rejected-candidates section citing each documented override's approval reference (PLAT-482, PLAT-511, PLAT-390). The goal condition is met; the run stops here.
