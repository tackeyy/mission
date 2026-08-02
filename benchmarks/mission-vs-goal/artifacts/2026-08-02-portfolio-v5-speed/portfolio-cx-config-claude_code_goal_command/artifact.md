# Portfolio CX Config Audit — claude_code_goal_command

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-cx-config-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit configuration compliance for four services (`auth`, `billing`,
`search`, `notify`) against the platform canonical defaults
(`PLAT-CONFIG v4`). For every constant in every service, classify it as
compliant, an undocumented divergence (violation), or a documented override
(rejected as a non-finding, with approval reference cited).

## Result

Full per-service compliance table, violations section, and rejected-candidates
section below. Summary counts:

- **auth-service**: 7 compliant, 2 undocumented violations, 0 documented overrides
- **billing-service**: 7 compliant, 1 undocumented violation, 1 documented override (rejected)
- **search-service**: 6 compliant, 2 undocumented violations, 1 documented override (rejected)
- **notify-service**: 7 compliant, 1 undocumented violation, 1 documented override (rejected)

### Per-service compliance table

| Service | Constant | Canonical value | Actual value | Status |
|---|---|---|---|---|
| auth | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| auth | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| auth | SESSION_TTL_SEC | 3600 | 7200 | **Violation (undocumented)** |
| auth | DB_POOL_SIZE | 64 | 64 | Compliant |
| auth | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation (undocumented)** |
| auth | CACHE_TTL_SEC | 300 | 300 | Compliant |
| auth | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| auth | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (rejected — see below) |
| billing | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| billing | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| billing | DB_POOL_SIZE | 64 | 64 | Compliant |
| billing | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| billing | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| billing | CACHE_TTL_SEC | 300 | 300 | Compliant |
| billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation (undocumented)** |
| billing | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| search | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| search | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| search | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| search | DB_POOL_SIZE | 64 | 128 | **Violation (undocumented)** |
| search | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| search | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| search | CACHE_TTL_SEC | 300 | 30 | Documented override (rejected — see below) |
| search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| search | LOG_RETENTION_DAYS | 30 | 45 | **Violation (undocumented)** |
| notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| notify | REQUEST_RETRY_MAX | 5 | 2 | Documented override (rejected — see below) |
| notify | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| notify | DB_POOL_SIZE | 64 | 64 | Compliant |
| notify | BATCH_WINDOW_MS | 500 | 250 | **Violation (undocumented)** |
| notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| notify | CACHE_TTL_SEC | 300 | 300 | Compliant |
| notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| notify | LOG_RETENTION_DAYS | 30 | 30 | Compliant |

All 9 canonical constants × 4 services = 36 rows are covered above; no rows
are missing.

## Evidence

### Violations (undocumented divergences)

Each entry quotes the canonical row from `platform-defaults.md` and the
actual row from the service config. None of these carry a `PLAT-<id>`
approval reference in the service config, so per the override protocol
("Overrides without an approval reference are treated as violations") they
are confirmed findings.

1. **auth-service / SESSION_TTL_SEC**
   - Canonical: `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`
   - Actual: `| SESSION_TTL_SEC | 7200 | |`
   - Note in fixture: "session length was extended during the 2026-04 login incident and the change was kept afterwards" — no `PLAT-` reference given.

2. **auth-service / TLS_MIN_VERSION**
   - Canonical: `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`
   - Actual: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`
   - Note in fixture: "The TLS floor is pinned for an older mobile SDK; the SDK deprecation ticket is still open." — no `PLAT-` reference given.

3. **billing-service / IDEMPOTENCY_WINDOW_SEC**
   - Canonical: `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`
   - Actual: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`
   - Note in fixture: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no `PLAT-` reference given (contrast with the same service's CONNECT_TIMEOUT_MS override, which does cite `PLAT-482`).

4. **search-service / DB_POOL_SIZE**
   - Canonical: `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`
   - Actual: `| DB_POOL_SIZE | 128 | |`
   - Note in fixture: "the pool was doubled during a 2026-05 load test and never reverted." — no `PLAT-` reference given.

5. **search-service / LOG_RETENTION_DAYS**
   - Canonical: `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`
   - Actual: `| LOG_RETENTION_DAYS | 45 | |`
   - Note in fixture: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." — explicitly confirmed as unfiled, no `PLAT-` reference.

6. **notify-service / BATCH_WINDOW_MS**
   - Canonical: `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`
   - Actual: `| BATCH_WINDOW_MS | 250 | |`
   - Note in fixture: "the batch window was halved to reduce push latency during the 2026-06 campaign." — no `PLAT-` reference given.

### Rejected candidates (documented overrides — not findings)

These constants also diverge from canonical values, but the service config
cites an explicit approval reference per the override protocol, so they are
**rejected as non-findings**.

1. **billing-service / CONNECT_TIMEOUT_MS** — Canonical `4000`, actual `12000`.
   Quoted note: "Override: PSP provider p99 latency is 9s; approved PLAT-482".
   Approval reference: **PLAT-482**.

2. **search-service / CACHE_TTL_SEC** — Canonical `300`, actual `30`.
   Quoted note: "Override: suggestion freshness SLA requires 30s; approved PLAT-511".
   Approval reference: **PLAT-511**.

3. **notify-service / REQUEST_RETRY_MAX** — Canonical `5`, actual `2`.
   Quoted note: "Override: at-most-once delivery guarantee; approved PLAT-390".
   Approval reference: **PLAT-390**.

## Assumptions

- The override protocol quoted in `platform-defaults.md` ("the service config
  must state the constant, the overridden value, the reason, and the
  approval reference... Overrides without an approval reference are treated
  as violations") is treated as the sole test for whether a divergence counts
  as a documented override vs. a violation. Any divergence whose fixture row
  or operational note lacks a literal `PLAT-<id>` string is classified as an
  undocumented violation, even where the operational notes describe a
  plausible operational reason (e.g. auth-service's TLS pin for SDK
  compatibility, or billing-service's idempotency-window widening for
  debugging).
- No fixtures beyond the five named files were read (per task rules), so
  compliance is judged only against what those five files state. Whether an
  approval reference like `PLAT-482` is itself valid/authorized in some
  external system is unmeasured — this audit only checks whether the
  citation is present in the service config, per the protocol's literal
  requirement.
- All 9 canonical constants apply uniformly to all 4 services; the fixture
  gives no per-service exemption list, so any service missing a constant row
  would count as an audit failure. All 4 services in fact list all 9
  constants, so there are no missing-row failures to report.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-cx-config-claude_code_goal_command.md`
and includes the headings Goal, Result, Evidence, Assumptions, and Stop
Condition, satisfying the configured goal. The audit is complete: all 36
constant/service pairs (9 constants × 4 services) are classified, all 6
undocumented violations are quoted with canonical vs. actual values, and all
3 documented overrides are cited with their `PLAT-<id>` approval reference
and rejected as non-findings.
