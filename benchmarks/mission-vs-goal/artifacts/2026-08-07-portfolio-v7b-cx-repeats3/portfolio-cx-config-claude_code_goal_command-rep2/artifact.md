# portfolio-cx-config — Configuration Compliance Audit (claude_code_goal_command / rep2)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify it as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce a full per-service compliance table, a violations section with quoted evidence, and a rejected-candidates section.

## Result

All 9 canonical constants were audited across all 4 services (36 rows total, no missing rows).

- **Compliant rows: 27**
- **Violations (undocumented divergences): 6**
  - auth: `SESSION_TTL_SEC` (7200 vs 3600), `TLS_MIN_VERSION` (TLSv1.1 vs TLSv1.2)
  - billing: `IDEMPOTENCY_WINDOW_SEC` (86400 vs 600)
  - search: `DB_POOL_SIZE` (128 vs 64), `LOG_RETENTION_DAYS` (45 vs 30)
  - notify: `BATCH_WINDOW_MS` (250 vs 500)
- **Documented overrides (rejected candidates, non-findings): 3**
  - billing: `CONNECT_TIMEOUT_MS` (12000, approved PLAT-482)
  - search: `CACHE_TTL_SEC` (30, approved PLAT-511)
  - notify: `REQUEST_RETRY_MAX` (2, approved PLAT-390)

### Full per-service compliance table

Canonical values per `platform/platform-defaults.md` (PLAT-CONFIG v4): CONNECT_TIMEOUT_MS=4000, REQUEST_RETRY_MAX=5, SESSION_TTL_SEC=3600, DB_POOL_SIZE=64, BATCH_WINDOW_MS=500, TLS_MIN_VERSION=TLSv1.2, CACHE_TTL_SEC=300, IDEMPOTENCY_WINDOW_SEC=600, LOG_RETENTION_DAYS=30.

#### auth-service

| Constant | Canonical value | Actual value | Status |
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

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — approved **PLAT-482** (rejected as non-finding) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence, no approval reference) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence, no approval reference) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override — approved **PLAT-511** (rejected as non-finding) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence, no approval reference) |

#### notify-service

| Constant | Canonical value | Actual value | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — approved **PLAT-390** (rejected as non-finding) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence, no approval reference) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (confirmed findings)

All six violations are undocumented divergences: none carries a `PLAT-<id>` approval reference, which the platform doc requires ("Overrides without an approval reference are treated as violations").

1. **auth-service — SESSION_TTL_SEC**: canonical `3600`, actual `7200`. Config row: `| SESSION_TTL_SEC | 7200 | |` (empty note, no approval reference). Operational note confirms: "session length was extended during the 2026-04 login incident and the change was kept afterwards." Canonical rationale: "Security review SR-2026-02".
2. **auth-service — TLS_MIN_VERSION**: canonical `TLSv1.2`, actual `TLSv1.1`. Config row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` — the note gives a reason but no `PLAT-<id>` approval reference, so it fails the override protocol. Canonical rationale: "Security baseline; TLSv1.1 is end-of-life". Operational note: "The TLS floor is pinned for an older mobile SDK; the SDK deprecation ticket is still open."
3. **billing-service — IDEMPOTENCY_WINDOW_SEC**: canonical `600`, actual `86400`. Config row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (empty note, no approval reference). Operational note confirms: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03."
4. **search-service — DB_POOL_SIZE**: canonical `64`, actual `128`. Config row: `| DB_POOL_SIZE | 128 | |` (empty note, no approval reference). Operational note confirms: "the pool was doubled during a 2026-05 load test and never reverted."
5. **search-service — LOG_RETENTION_DAYS**: canonical `30`, actual `45`. Config row: `| LOG_RETENTION_DAYS | 45 | |` (empty note, no approval reference). Operational note confirms: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Canonical rationale: "Data-minimization policy DM-9".
6. **notify-service — BATCH_WINDOW_MS**: canonical `500`, actual `250`. Config row: `| BATCH_WINDOW_MS | 250 | |` (empty note, no approval reference). Operational note confirms: "the batch window was halved to reduce push latency during the 2026-06 campaign."

### Rejected candidates (documented overrides — non-findings)

These diverge from canonical values but follow the override protocol (constant, overridden value, reason, and approval reference stated), so they are rejected as non-findings:

1. **billing-service — CONNECT_TIMEOUT_MS**: canonical `4000`, actual `12000`. Config row quotes: `Override: PSP provider p99 latency is 9s; approved PLAT-482`. Approval reference: **PLAT-482**.
2. **search-service — CACHE_TTL_SEC**: canonical `300`, actual `30`. Config row quotes: `Override: suggestion freshness SLA requires 30s; approved PLAT-511`. Approval reference: **PLAT-511**.
3. **notify-service — REQUEST_RETRY_MAX**: canonical `5`, actual `2`. Config row quotes: `Override: at-most-once delivery guarantee; approved PLAT-390`. Approval reference: **PLAT-390**.

## Evidence

- Canonical defaults source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (PLAT-CONFIG v4). Override protocol quote: "the service config must state the constant, the overridden value, the reason, and the approval reference. Overrides without an approval reference are treated as violations."
- Service configs read: `.../config-sprawl/auth/config.md`, `.../config-sprawl/billing/config.md`, `.../config-sprawl/search/config.md`, `.../config-sprawl/notify/config.md`. All constant names and values quoted in the tables above are taken verbatim from these five fixture files.
- Approval references quoted verbatim from fixture text: `PLAT-482` (billing), `PLAT-511` (search), `PLAT-390` (notify). No other `PLAT-<id>` reference appears in any service config, so all remaining divergences are undocumented.
- Coverage check: 9 canonical constants × 4 services = 36 rows; each per-service table above lists all 9 constants, so no rows are missing.

## Assumptions

- The five named fixture files are the sole source of truth; no other files under `benchmarks/mission-vs-goal/` were opened, per the run rules.
- A note giving a reason without a `PLAT-<id>` approval reference (auth's "legacy SDK compat") does not satisfy the override protocol and is therefore a violation, per the platform doc's explicit rule.
- Operational-notes prose mentioning that an override "follows the override protocol" is treated as corroboration only; classification is based on the presence of the `PLAT-<id>` reference in the config.
- Runtime behavior of the services is unmeasured; this audit covers only the declared configuration values in the fixture files.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7b-cx-repeats3/portfolio-cx-config-claude_code_goal_command-rep2.md` and includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a full per-service compliance table covering every canonical constant, a violations section with quoted evidence, and a rejected-candidates section citing each documented override's approval reference. No commits, pushes, package installs, or network access were performed; edits were limited to this single output file.
