# Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-cx-config-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit configuration compliance for four services (`auth`, `billing`,
`search`, `notify`) against the platform canonical defaults (`PLAT-CONFIG v4`).
For every constant in every service, classify as compliant, undocumented
divergence (violation), or documented override (rejected as a non-finding,
with approval reference cited).

## Result

Audit complete. Fixtures read (exactly the five named in the task prompt):

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

Canonical defaults (from `platform-defaults.md`, PLAT-CONFIG v4):

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

#### auth-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation (undocumented)** |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation (undocumented — no approval reference)** |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (rejected — see below) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation (undocumented — no approval reference)** |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation (undocumented — no approval reference)** |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (rejected — see below) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation (undocumented — no approval reference)** |

#### notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (rejected — see below) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation (undocumented — no approval reference)** |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (undocumented divergences — confirmed findings)

1. **auth-service / SESSION_TTL_SEC**: canonical value `3600`, actual value
   `7200` (`| SESSION_TTL_SEC | 7200 | |`, `auth/config.md` line 9). The
   operational note ("session length was extended during the 2026-04 login
   incident and the change was kept afterwards") states a reason but cites no
   approval reference (`PLAT-<id>`) as the override protocol requires — this
   is a violation, not a documented override.
2. **auth-service / TLS_MIN_VERSION**: canonical value `TLSv1.2`, actual value
   `TLSv1.1` (`| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`,
   `auth/config.md` line 12). "legacy SDK compat" is a reason, not an approval
   reference — no `PLAT-<id>` is cited. Violation.
3. **billing-service / IDEMPOTENCY_WINDOW_SEC**: canonical value `600`, actual
   value `86400` (`| IDEMPOTENCY_WINDOW_SEC | 86400 | |`, `billing/config.md`
   line 14). Operational notes explain it was "widened while debugging
   duplicate settlement webhooks in 2026-03" but cite no approval reference.
   Violation.
4. **search-service / DB_POOL_SIZE**: canonical value `64`, actual value
   `128` (`| DB_POOL_SIZE | 128 | |`, `search/config.md` line 10).
   Operational notes state "the pool was doubled during a 2026-05 load test
   and never reverted" — no approval reference. Violation.
5. **search-service / LOG_RETENTION_DAYS**: canonical value `30`, actual
   value `45` (`| LOG_RETENTION_DAYS | 45 | |`, `search/config.md` line 15).
   Operational notes explicitly state "nobody filed the retention change with
   the platform team" — confirmed undocumented. Violation.
6. **notify-service / BATCH_WINDOW_MS**: canonical value `500`, actual value
   `250` (`| BATCH_WINDOW_MS | 250 | |`, `notify/config.md` line 11).
   Operational notes state "the batch window was halved to reduce push
   latency during the 2026-06 campaign" — no approval reference. Violation.

### Rejected candidates (documented overrides — non-findings)

1. **billing-service / CONNECT_TIMEOUT_MS**: canonical `4000`, actual `12000`
   (`| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s;
   approved PLAT-482 |`, `billing/config.md` line 7). Approval reference:
   **PLAT-482**. Confirmed as a documented override in operational notes
   ("The connect timeout override follows the platform override protocol
   with approval reference PLAT-482"). Rejected as a non-finding.
2. **search-service / CACHE_TTL_SEC**: canonical `300`, actual `30`
   (`| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s;
   approved PLAT-511 |`, `search/config.md` line 13). Approval reference:
   **PLAT-511**. Confirmed in operational notes ("The cache TTL override
   follows the override protocol with approval reference PLAT-511").
   Rejected as a non-finding.
3. **notify-service / REQUEST_RETRY_MAX**: canonical `5`, actual `2`
   (`| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee;
   approved PLAT-390 |`, `notify/config.md` line 8). Approval reference:
   **PLAT-390**. Confirmed in operational notes ("The retry override follows
   the override protocol with approval reference PLAT-390"). Rejected as a
   non-finding.

### Summary counts

- Constants audited: 9 per service × 4 services = 36 rows, all present (no
  missing rows).
- Compliant: 27
- Violations (undocumented divergence): 6
- Documented overrides (rejected non-findings): 3

## Evidence

All quotes above are taken verbatim from the table rows and operational-notes
prose of the five read fixtures, with file and line references inline. No
external or unread fixture content was used. Canonical values are quoted
directly from `platform-defaults.md` lines 9–17.

## Assumptions

- The override protocol defined in `platform-defaults.md` ("the service
  config must state the constant, the overridden value, the reason, and the
  approval reference... Overrides without an approval reference are treated
  as violations") is applied literally: any divergence whose config row or
  operational note lacks a `PLAT-<id>` citation is classified as a violation,
  even when a business reason is given (e.g. auth-service's "legacy SDK
  compat" and "kept after the incident" explanations, billing-service's and
  search-service's/notify-service's non-cited divergences).
- No fixture outside the five named files was consulted; scoring
  configuration, task metadata, and answer keys under `benchmarks/` were not
  opened, per the task's out-of-bounds rule. Whether this audit's
  classifications match an external answer key is unmeasured.
- "Last reviewed" dates in each service config are descriptive metadata, not
  constants to audit, and were excluded from the compliance table.

## Stop Condition

This artifact is written at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-cx-config-claude_code_goal_command.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), a full per-service compliance table for all 9 canonical constants
across all 4 services (36/36 rows present, no missing rows), a violations
section with quoted fixture evidence for all 6 confirmed undocumented
divergences, and a rejected-candidates section citing the approval reference
(PLAT-482, PLAT-511, PLAT-390) for each of the 3 documented overrides. No
commits, pushes, package installs, or network access were performed. The task
is complete.
