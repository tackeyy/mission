# Configuration Compliance Audit — portfolio-cx-config (claude_code_goal_command / rep2)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (PLAT-CONFIG v4). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce this artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-config-claude_code_goal_command-rep2.md` with headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

All 9 canonical constants were audited across all 4 services (36 rows total).

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6**
- **Rejected candidates (documented overrides with approval references): 3**

### Summary of violations

| # | Service | Constant | Canonical value | Actual value |
|---|---|---|---|---|
| 1 | auth | SESSION_TTL_SEC | 3600 | 7200 |
| 2 | auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 |
| 3 | billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 |
| 4 | search | DB_POOL_SIZE | 64 | 128 |
| 5 | search | LOG_RETENTION_DAYS | 30 | 45 |
| 6 | notify | BATCH_WINDOW_MS | 500 | 250 |

### Summary of rejected candidates (documented overrides — non-findings)

| # | Service | Constant | Actual value | Approval reference |
|---|---|---|---|---|
| 1 | billing | CONNECT_TIMEOUT_MS | 12000 | PLAT-482 |
| 2 | search | CACHE_TTL_SEC | 30 | PLAT-511 |
| 3 | notify | REQUEST_RETRY_MAX | 2 | PLAT-390 |

### Full per-service compliance tables

Canonical values per `platform/platform-defaults.md` (PLAT-CONFIG v4): CONNECT_TIMEOUT_MS = 4000, REQUEST_RETRY_MAX = 5, SESSION_TTL_SEC = 3600, DB_POOL_SIZE = 64, BATCH_WINDOW_MS = 500, TLS_MIN_VERSION = TLSv1.2, CACHE_TTL_SEC = 300, IDEMPOTENCY_WINDOW_SEC = 600, LOG_RETENTION_DAYS = 30.

#### auth-service (`auth/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence — no approval reference) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (note "legacy SDK compat" has no `PLAT-<id>` approval reference) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (`billing/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — **rejected as non-finding** (approved PLAT-482) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence — no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service (`search/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence — no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override — **rejected as non-finding** (approved PLAT-511) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence — no approval reference) |

#### notify-service (`notify/config.md`)

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — **rejected as non-finding** (approved PLAT-390) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence — no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Evidence

All evidence is quoted verbatim from the five fixture files read for this audit. No other files under `benchmarks/mission-vs-goal/` were opened.

### Canonical baseline (`benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`)

Canonical table rows (verbatim):

- `| CONNECT_TIMEOUT_MS | 4000 | Upstream LB kills idle connects at 5s |`
- `| REQUEST_RETRY_MAX | 5 | Backoff budget fits the 30s request SLA |`
- `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`
- `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`
- `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`
- `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`
- `| CACHE_TTL_SEC | 300 | Balance of freshness and origin load |`
- `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`
- `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`

Override protocol (verbatim): "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Overrides without an approval reference are treated as violations."

### Confirmed violations (with quoted fixture evidence)

1. **auth / SESSION_TTL_SEC — canonical `3600`, actual `7200`.** Fixture row (verbatim): `| SESSION_TTL_SEC | 7200 | |`. The Note column is empty — no approval reference. The operational notes confirm it is undocumented: "session length was extended during the 2026-04 login incident and the change was kept afterwards" (no `PLAT-<id>` cited). Source: `auth/config.md`.
2. **auth / TLS_MIN_VERSION — canonical `TLSv1.2`, actual `TLSv1.1`.** Fixture row (verbatim): `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. The note "legacy SDK compat" gives a reason but no `PLAT-<id>` approval reference; the operational notes say "the SDK deprecation ticket is still open" — a ticket is not an approval reference. Per the override protocol, a reason without an approval reference is a violation. Source: `auth/config.md`.
3. **billing / IDEMPOTENCY_WINDOW_SEC — canonical `600`, actual `86400`.** Fixture row (verbatim): `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`. The Note column is empty. Operational notes confirm: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference. Source: `billing/config.md`.
4. **search / DB_POOL_SIZE — canonical `64`, actual `128`.** Fixture row (verbatim): `| DB_POOL_SIZE | 128 | |`. The Note column is empty. Operational notes confirm: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference. Source: `search/config.md`.
5. **search / LOG_RETENTION_DAYS — canonical `30`, actual `45`.** Fixture row (verbatim): `| LOG_RETENTION_DAYS | 45 | |`. The Note column is empty. Operational notes explicitly confirm it is unfiled: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Source: `search/config.md`.
6. **notify / BATCH_WINDOW_MS — canonical `500`, actual `250`.** Fixture row (verbatim): `| BATCH_WINDOW_MS | 250 | |`. The Note column is empty. Operational notes confirm: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference. Source: `notify/config.md`.

### Rejected candidates (documented overrides — non-findings, with approval references)

1. **billing / CONNECT_TIMEOUT_MS = 12000 — REJECTED (approved PLAT-482).** Fixture row (verbatim): `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`. It states the constant, value, reason, and approval reference `PLAT-482`, satisfying the override protocol. Source: `billing/config.md`.
2. **search / CACHE_TTL_SEC = 30 — REJECTED (approved PLAT-511).** Fixture row (verbatim): `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Reason and approval reference `PLAT-511` present. Source: `search/config.md`.
3. **notify / REQUEST_RETRY_MAX = 2 — REJECTED (approved PLAT-390).** Fixture row (verbatim): `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Reason and approval reference `PLAT-390` present. Source: `notify/config.md`.

### Coverage check

- Canonical constants: 9 (all listed in PLAT-CONFIG v4). Services: 4. Rows audited: 9 × 4 = 36, all present in the per-service tables above — no missing rows.
- Each service config table contains exactly the 9 canonical constants and no extra (non-canonical) constants; no service omits any canonical constant.

## Assumptions

- The five fixture markdown files are the sole source of truth for this audit; validity of the cited approval references (PLAT-482, PLAT-511, PLAT-390) against any external approval registry is unmeasured and taken at face value per the fixture text.
- Per the override protocol quoted above, a Note giving a reason without a `PLAT-<id>` approval reference (auth's "legacy SDK compat") is classified as a violation, not a documented override.
- An open deprecation ticket (auth TLS) and incident/debugging history (auth session TTL, billing idempotency window, search pool/retention, notify batch window) are not approval references and do not legitimize divergences.
- Runtime behavior of the services was not measured; this audit covers declared configuration values in the fixtures only.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-config-claude_code_goal_command-rep2.md` and includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering all 9 canonical constants for all 4 services (36/36 rows), a violations section with verbatim quoted evidence for all 6 undocumented divergences, and a rejected-candidates section citing each documented override's approval reference (PLAT-482, PLAT-511, PLAT-390). No commits, pushes, package installs, or network access were performed; edits were limited to this single output file. The goal condition is met — stopping.
