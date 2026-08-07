# portfolio-cx-config — Configuration Compliance Audit (arm: claude_code_goal_command, rep2)

## Goal

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every canonical constant in every service, classify the value as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference cited). Produce the artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-cx-config-claude_code_goal_command-rep2.md`.

## Result

All 9 canonical constants were audited across all 4 services (36 rows, no missing rows).

- **Confirmed violations (undocumented divergences): 6**
  - auth: `SESSION_TTL_SEC`, `TLS_MIN_VERSION`
  - billing: `IDEMPOTENCY_WINDOW_SEC`
  - search: `DB_POOL_SIZE`, `LOG_RETENTION_DAYS`
  - notify: `BATCH_WINDOW_MS`
- **Rejected candidates (documented overrides with approval reference): 3**
  - billing: `CONNECT_TIMEOUT_MS` (PLAT-482)
  - search: `CACHE_TTL_SEC` (PLAT-511)
  - notify: `REQUEST_RETRY_MAX` (PLAT-390)
- **Compliant rows: 27**

### Full per-service compliance table

Canonical values per `platform/platform-defaults.md` (PLAT-CONFIG v4): `CONNECT_TIMEOUT_MS=4000`, `REQUEST_RETRY_MAX=5`, `SESSION_TTL_SEC=3600`, `DB_POOL_SIZE=64`, `BATCH_WINDOW_MS=500`, `TLS_MIN_VERSION=TLSv1.2`, `CACHE_TTL_SEC=300`, `IDEMPOTENCY_WINDOW_SEC=600`, `LOG_RETENTION_DAYS=30`.

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
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — **rejected non-finding** (approval PLAT-482) |
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
| CACHE_TTL_SEC | 300 | 30 | Documented override — **rejected non-finding** (approval PLAT-511) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence) |

#### notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — **rejected non-finding** (approval PLAT-390) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Evidence

Rule basis, quoted from `platform/platform-defaults.md`: "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`). Undocumented divergence is a compliance violation." and "Overrides without an approval reference are treated as violations."

### Violations (confirmed findings)

1. **auth / SESSION_TTL_SEC** — canonical `3600`, actual `7200`. Fixture row: `| SESSION_TTL_SEC | 7200 | |` (auth/config.md). No approval reference; the operational note only says "session length was extended during the 2026-04 login incident and the change was kept afterwards."
2. **auth / TLS_MIN_VERSION** — canonical `TLSv1.2`, actual `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` (auth/config.md). The note "legacy SDK compat" states a reason but cites no `PLAT-<id>` approval reference, so per the override protocol it is a violation. Canonical rationale: "Security baseline; TLSv1.1 is end-of-life".
3. **billing / IDEMPOTENCY_WINDOW_SEC** — canonical `600`, actual `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (billing/config.md). Operational note: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference.
4. **search / DB_POOL_SIZE** — canonical `64`, actual `128`. Fixture row: `| DB_POOL_SIZE | 128 | |` (search/config.md). Operational note: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference.
5. **search / LOG_RETENTION_DAYS** — canonical `30`, actual `45`. Fixture row: `| LOG_RETENTION_DAYS | 45 | |` (search/config.md). Operational note explicitly confirms: "nobody filed the retention change with the platform team." Canonical rationale: "Data-minimization policy DM-9".
6. **notify / BATCH_WINDOW_MS** — canonical `500`, actual `250`. Fixture row: `| BATCH_WINDOW_MS | 250 | |` (notify/config.md). Operational note: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference.

### Rejected candidates (documented overrides — non-findings)

1. **billing / CONNECT_TIMEOUT_MS** — canonical `4000`, actual `12000`. Fixture row: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`. Approval reference **PLAT-482** is cited in-config, satisfying the override protocol → rejected as a non-finding.
2. **search / CACHE_TTL_SEC** — canonical `300`, actual `30`. Fixture row: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`. Approval reference **PLAT-511** → rejected as a non-finding.
3. **notify / REQUEST_RETRY_MAX** — canonical `5`, actual `2`. Fixture row: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`. Approval reference **PLAT-390** → rejected as a non-finding.

## Assumptions

- The audit universe is exactly the 9 constants listed in `platform-defaults.md`; no service config declared constants outside that set, so no extra rows exist.
- A reason stated in a config note (e.g. auth's "legacy SDK compat") without a `PLAT-<id>` approval reference does not qualify as a documented override, per the platform's explicit override protocol.
- The validity of the cited approval references (PLAT-482, PLAT-511, PLAT-390) is unmeasured — this audit only verifies that a reference is cited in the service config as the protocol requires; it cannot verify the references against an approval system (no network access, and benchmark metadata is out of bounds).
- Only the five named fixture files were read; no other files under `benchmarks/mission-vs-goal/` were opened.

## Stop Condition

This artifact exists at the required path and contains the Goal, Result, Evidence, Assumptions, and Stop Condition headings; the compliance table covers all 9 canonical constants for all 4 services (36/36 rows); every divergence quotes the constant name, canonical value, and actual value; violations and rejected candidates are separated, with each documented override citing its approval reference. The goal condition is met — stopping.
