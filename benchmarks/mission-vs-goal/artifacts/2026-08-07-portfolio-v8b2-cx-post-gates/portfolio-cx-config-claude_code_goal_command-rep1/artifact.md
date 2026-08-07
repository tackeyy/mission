# Configuration Compliance Audit — portfolio-cx-config (claude_code_goal_command, rep1)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (PLAT-CONFIG v4). For every constant in every service, classify as compliant, undocumented divergence (violation), or documented override (rejected as non-finding with approval reference). Deliver this artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-config-claude_code_goal_command-rep1.md`.

## Result

All 9 canonical constants were audited across all 4 services (36 rows total).

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6** — auth: 2, billing: 1, search: 2, notify: 1
- **Rejected candidates (documented overrides with approval references): 3** — billing PLAT-482, search PLAT-511, notify PLAT-390

### Canonical defaults (from `platform/platform-defaults.md`)

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

### Per-service compliance table

#### auth-service (`auth/config.md`)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence) |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (undocumented divergence) |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (`billing/config.md`)

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

#### search-service (`search/config.md`)

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

#### notify-service (`notify/config.md`)

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

1. **auth-service / SESSION_TTL_SEC** — canonical `3600`, actual `7200`. Fixture row: `| SESSION_TTL_SEC | 7200 | |` (no approval reference). Operational note confirms it is undocumented: "session length was extended during the 2026-04 login incident and the change was kept afterwards." No `PLAT-<id>` reference exists.
2. **auth-service / TLS_MIN_VERSION** — canonical `TLSv1.2`, actual `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. The note "legacy SDK compat" states a reason but cites no approval reference, so under the override protocol ("Overrides without an approval reference are treated as violations") this is a violation. The note adds: "the SDK deprecation ticket is still open."
3. **billing-service / IDEMPOTENCY_WINDOW_SEC** — canonical `600`, actual `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (no approval reference). Operational note: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference cited.
4. **search-service / DB_POOL_SIZE** — canonical `64`, actual `128`. Fixture row: `| DB_POOL_SIZE | 128 | |` (no approval reference). Operational note: "the pool was doubled during a 2026-05 load test and never reverted."
5. **search-service / LOG_RETENTION_DAYS** — canonical `30`, actual `45`. Fixture row: `| LOG_RETENTION_DAYS | 45 | |` (no approval reference). Operational note explicitly confirms: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team."
6. **notify-service / BATCH_WINDOW_MS** — canonical `500`, actual `250`. Fixture row: `| BATCH_WINDOW_MS | 250 | |` (no approval reference). Operational note: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference cited.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service / CONNECT_TIMEOUT_MS** — canonical `4000`, actual `12000`. Approval reference **PLAT-482**. Fixture note: "Override: PSP provider p99 latency is 9s; approved PLAT-482". Operational note confirms: "The connect timeout override follows the platform override protocol with approval reference PLAT-482." Rejected as a non-finding.
2. **search-service / CACHE_TTL_SEC** — canonical `300`, actual `30`. Approval reference **PLAT-511**. Fixture note: "Override: suggestion freshness SLA requires 30s; approved PLAT-511". Operational note confirms: "The cache TTL override follows the override protocol with approval reference PLAT-511." Rejected as a non-finding.
3. **notify-service / REQUEST_RETRY_MAX** — canonical `5`, actual `2`. Approval reference **PLAT-390**. Fixture note: "Override: at-most-once delivery guarantee; approved PLAT-390". Operational note confirms: "The retry override follows the override protocol with approval reference PLAT-390." Rejected as a non-finding.

## Evidence

- Canonical defaults read from `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (PLAT-CONFIG v4): 9 constants, plus the override protocol — "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Overrides without an approval reference are treated as violations."
- Service values read from the four named fixtures: `auth/config.md`, `billing/config.md`, `search/config.md`, `notify/config.md`. Exact constant names, canonical values, and actual values are quoted in the tables and violation entries above.
- Classification rule applied: divergence + approval reference in the config note → documented override (rejected); divergence without an approval reference (including reason-only notes like "legacy SDK compat") → violation; value equal to canonical → compliant.
- Coverage check: 9 constants × 4 services = 36 rows; all 36 appear in the per-service tables above; no missing rows.
- Unmeasured: runtime behavior of the services, whether the config files reflect deployed values, and the validity/status of the cited approval tickets (PLAT-482, PLAT-511, PLAT-390) were not verified — only the fixture text was audited.

## Assumptions

- The five named fixture files are the sole source of truth for this audit; no other files under `benchmarks/mission-vs-goal/` were read, per the run rules.
- A note stating a reason without a `PLAT-<id>` approval reference (e.g. auth's "legacy SDK compat") does not satisfy the override protocol, per the platform doc's explicit rule.
- The approval references quoted in the fixtures (PLAT-482, PLAT-511, PLAT-390) are taken at face value as valid approvals; their external validity is unmeasured.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8b2-cx-post-gates/portfolio-cx-config-claude_code_goal_command-rep1.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering all 9 canonical constants for all 4 services, a violations section with quoted evidence, and a rejected-candidates section citing each documented override's approval reference. No commits, pushes, package installs, or network access were performed; edits were limited to this single output file.
