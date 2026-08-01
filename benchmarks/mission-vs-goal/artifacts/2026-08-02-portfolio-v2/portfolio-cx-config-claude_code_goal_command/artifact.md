# Goal

Audit configuration compliance for four services (`auth`, `billing`, `search`, `notify`) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding, with approval reference cited). Quote exact constant names, canonical values, and actual values for every divergence.

## Result

Full per-service audit completed against the 9 canonical constants defined in `platform-defaults.md` across all 4 services (36 rows total, no missing rows).

- **6 confirmed violations** (undocumented divergences from canonical values, no `PLAT-<id>` approval reference cited in the service config)
- **3 rejected candidates** (documented overrides with a valid `PLAT-<id>` approval reference — not findings)
- **27 compliant rows**

### Per-service compliance table

**auth-service**

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

**billing-service**

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Rejected candidate (documented override, `PLAT-482`) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

**search-service**

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Rejected candidate (documented override, `PLAT-511`) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented) |

**notify-service**

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Rejected candidate (documented override, `PLAT-390`) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Evidence

### Confirmed violations (undocumented divergences)

1. **auth-service / `SESSION_TTL_SEC`** — canonical `3600` (platform-defaults.md: `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`). Actual value in auth/config.md: `| SESSION_TTL_SEC | 7200 | |` — no note field, no `PLAT-<id>` reference. The operational note states "session length was extended during the 2026-04 login incident and the change was kept afterwards" but cites no approval reference. Treated as a violation per the override protocol: "Overrides without an approval reference are treated as violations."

2. **auth-service / `TLS_MIN_VERSION`** — canonical `TLSv1.2` (platform-defaults.md: `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`). Actual value in auth/config.md: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. A reason ("legacy SDK compat") is stated, but no `PLAT-<id>` approval reference is quoted anywhere in auth/config.md. Per protocol, a stated reason without an approval reference is still a violation.

3. **billing-service / `IDEMPOTENCY_WINDOW_SEC`** — canonical `600` (platform-defaults.md: `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`). Actual value in billing/config.md: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`. Operational notes state "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no `PLAT-<id>` reference cited for this specific constant (only `CONNECT_TIMEOUT_MS` cites `PLAT-482` in this file).

4. **search-service / `DB_POOL_SIZE`** — canonical `64` (platform-defaults.md: `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`). Actual value in search/config.md: `| DB_POOL_SIZE | 128 | |`. Operational notes state "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference cited.

5. **search-service / `LOG_RETENTION_DAYS`** — canonical `30` (platform-defaults.md: `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`). Actual value in search/config.md: `| LOG_RETENTION_DAYS | 45 | |`. Operational notes explicitly confirm this is undocumented: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team."

6. **notify-service / `BATCH_WINDOW_MS`** — canonical `500` (platform-defaults.md: `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`). Actual value in notify/config.md: `| BATCH_WINDOW_MS | 250 | |`. Operational notes state "the batch window was halved to reduce push latency during the 2026-06 campaign" — no `PLAT-<id>` reference cited for this constant (only `REQUEST_RETRY_MAX` cites `PLAT-390` in this file).

### Rejected candidates (documented overrides — not findings)

1. **billing-service / `CONNECT_TIMEOUT_MS`** — actual `12000` vs. canonical `4000`. Note in billing/config.md: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`. Operational notes confirm: "The connect timeout override follows the platform override protocol with approval reference PLAT-482." Rejected — approval reference `PLAT-482` present.

2. **search-service / `CACHE_TTL_SEC`** — actual `30` vs. canonical `300`. Note in search/config.md: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Operational notes confirm: "The cache TTL override follows the override protocol with approval reference PLAT-511." Rejected — approval reference `PLAT-511` present.

3. **notify-service / `REQUEST_RETRY_MAX`** — actual `2` vs. canonical `5`. Note in notify/config.md: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Operational notes confirm: "The retry override follows the override protocol with approval reference PLAT-390." Rejected — approval reference `PLAT-390` present.

## Assumptions

- The override protocol quoted in `platform-defaults.md` ("the service config must state the constant, the overridden value, the reason, and the approval reference") is interpreted strictly: a stated reason without a quoted `PLAT-<id>` approval reference is insufficient and is classified as a violation, not a documented override. This affects auth-service `SESSION_TTL_SEC` and `TLS_MIN_VERSION`, billing-service `IDEMPOTENCY_WINDOW_SEC`, search-service `DB_POOL_SIZE`, and notify-service `BATCH_WINDOW_MS` — all of which have an operational-notes explanation but no approval reference.
- No constants beyond the 9 listed in `platform-defaults.md` were found in any of the four service configs; no additional service-specific constants required classification.
- Row/table order in each service config file was assumed complete as read (no truncation) — each service file contains exactly 9 constant rows matching the 9 canonical constants, so no missing rows were encountered.
- This audit is based solely on the five fixture files named in the task prompt. No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed, per the task rules.

## Stop Condition

This artifact satisfies the goal: it exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-cx-config-claude_code_goal_command.md`, includes all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), contains a full per-service compliance table covering all 9 canonical constants across all 4 services (36 rows, no missing rows), a violations section with quoted evidence for all 6 confirmed violations, and a rejected-candidates section citing each of the 3 documented overrides' approval references (`PLAT-482`, `PLAT-511`, `PLAT-390`). No benchmark superiority claims are made. No files outside the five named fixtures and this output file were accessed.
