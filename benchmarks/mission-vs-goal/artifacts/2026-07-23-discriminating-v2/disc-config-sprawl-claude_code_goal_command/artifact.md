# disc-config-sprawl — Configuration Compliance Audit (claude_code_goal_command arm)

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v2/disc-config-sprawl-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit configuration compliance for four services (auth, billing, search,
notify) against the platform canonical defaults (PLAT-CONFIG v4), covering
every constant in every service. For every divergence, classify it as an
undocumented violation or a documented override (citing the approval
reference and rejecting it as a non-finding). Coverage must be exhaustive —
every constant in every service must appear in the compliance table.

## Result

Audited all 4 services × 9 canonical constants = 36 rows. Found **6
undocumented violations** and **3 documented overrides** (rejected as
non-findings because each cites a valid `PLAT-<id>` approval reference per
the override protocol). The remaining 27 rows are fully compliant.

### Per-service compliance table

Canonical values (source: `platform-defaults.md`): CONNECT_TIMEOUT_MS=4000,
REQUEST_RETRY_MAX=5, SESSION_TTL_SEC=3600, DB_POOL_SIZE=64,
BATCH_WINDOW_MS=500, TLS_MIN_VERSION=TLSv1.2, CACHE_TTL_SEC=300,
IDEMPOTENCY_WINDOW_SEC=600, LOG_RETENTION_DAYS=30.

#### auth-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation (undocumented)** |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation (undocumented)** |
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
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation (undocumented)** |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation (undocumented)** |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (rejected — see below) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation (undocumented)** |

#### notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (rejected — see below) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation (undocumented)** |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (undocumented divergences — confirmed findings)

1. **auth-service / SESSION_TTL_SEC**: canonical `3600`, actual `7200`.
   Fixture row: `| SESSION_TTL_SEC | 7200 | |` — the Note column is empty, no
   approval reference. The operational notes state: "session length was
   extended during the 2026-04 login incident and the change was kept
   afterwards" — this describes a reason but cites no `PLAT-<id>` approval
   reference, so per the override protocol ("Overrides without an approval
   reference are treated as violations") this is a violation.

2. **auth-service / TLS_MIN_VERSION**: canonical `TLSv1.2`, actual
   `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK
   compat |`. The note gives a reason ("legacy SDK compat") but no
   `PLAT-<id>` reference. Operational notes confirm: "The TLS floor is
   pinned for an older mobile SDK; the SDK deprecation ticket is still
   open" — again a reason without an approval reference. Violation.

3. **billing-service / IDEMPOTENCY_WINDOW_SEC**: canonical `600`, actual
   `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` — Note
   column empty. Operational notes: "the idempotency window was widened
   while debugging duplicate settlement webhooks in 2026-03" — no
   `PLAT-<id>` reference cited anywhere for this constant. Violation.

4. **search-service / DB_POOL_SIZE**: canonical `64`, actual `128`.
   Fixture row: `| DB_POOL_SIZE | 128 | |` — Note column empty. Operational
   notes: "the pool was doubled during a 2026-05 load test and never
   reverted" — explicitly describes an unreverted, undocumented change with
   no approval reference. Violation.

5. **search-service / LOG_RETENTION_DAYS**: canonical `30`, actual `45`.
   Fixture row: `| LOG_RETENTION_DAYS | 45 | |` — Note column empty.
   Operational notes state directly: "nobody filed the retention change
   with the platform team" — explicit confirmation of no approval
   reference. Violation.

6. **notify-service / BATCH_WINDOW_MS**: canonical `500`, actual `250`.
   Fixture row: `| BATCH_WINDOW_MS | 250 | |` — Note column empty.
   Operational notes: "the batch window was halved to reduce push latency
   during the 2026-06 campaign" — no `PLAT-<id>` reference cited for this
   constant. Violation.

### Rejected candidates (documented overrides — not findings)

1. **billing-service / CONNECT_TIMEOUT_MS**: canonical `4000`, actual
   `12000`. Looked like a violation (4000 → 12000 is a 3x divergence), but
   the fixture row states: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP
   provider p99 latency is 9s; approved PLAT-482 |`. Operational notes
   confirm: "The connect timeout override follows the platform override
   protocol with approval reference PLAT-482." This meets the override
   protocol's requirement (constant, overridden value, reason, and approval
   reference all present) — rejected as a non-finding.

2. **search-service / CACHE_TTL_SEC**: canonical `300`, actual `30`. Looked
   like a violation, but the fixture row states: `| CACHE_TTL_SEC | 30 |
   Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`.
   Operational notes confirm: "The cache TTL override follows the override
   protocol with approval reference PLAT-511." Rejected as a non-finding.

3. **notify-service / REQUEST_RETRY_MAX**: canonical `5`, actual `2`.
   Looked like a violation, but the fixture row states: `|
   REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee;
   approved PLAT-390 |`. Operational notes confirm: "The retry override
   follows the override protocol with approval reference PLAT-390."
   Rejected as a non-finding.

## Evidence

All quotes below are verbatim from the five fixture files read for this
audit.

- Canonical defaults source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  - `| CONNECT_TIMEOUT_MS | 4000 | Upstream LB kills idle connects at 5s |`
  - `| REQUEST_RETRY_MAX | 5 | Backoff budget fits the 30s request SLA |`
  - `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`
  - `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`
  - `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`
  - `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`
  - `| CACHE_TTL_SEC | 300 | Balance of freshness and origin load |`
  - `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`
  - `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`
  - Override protocol: "the service config must state the constant, the
    overridden value, the reason, and the approval reference. Overrides
    without an approval reference are treated as violations."

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  - `| SESSION_TTL_SEC | 7200 | |`
  - `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`
  - "session length was extended during the 2026-04 login incident and the
    change was kept afterwards. The TLS floor is pinned for an older mobile
    SDK; the SDK deprecation ticket is still open."

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  - `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is
    9s; approved PLAT-482 |`
  - `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`
  - "the idempotency window was widened while debugging duplicate
    settlement webhooks in 2026-03. The connect timeout override follows
    the platform override protocol with approval reference PLAT-482."

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  - `| DB_POOL_SIZE | 128 | |`
  - `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires
    30s; approved PLAT-511 |`
  - `| LOG_RETENTION_DAYS | 45 | |`
  - "the pool was doubled during a 2026-05 load test and never reverted.
    Query logs are kept 45 days to debug relevance regressions; nobody
    filed the retention change with the platform team. The cache TTL
    override follows the override protocol with approval reference
    PLAT-511."

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
  - `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee;
    approved PLAT-390 |`
  - `| BATCH_WINDOW_MS | 250 | |`
  - "the batch window was halved to reduce push latency during the 2026-06
    campaign. The retry override follows the override protocol with
    approval reference PLAT-390."

All 5 named fixture files were read in full; no other files under
`benchmarks/mission-vs-goal/` were opened, read, grepped, or listed, per the
task's out-of-bounds rule for benchmark metadata.

## Assumptions

- "Documented override" requires, per the platform's own override protocol
  text, all four elements: constant name, overridden value, reason, and an
  approval reference in the form `PLAT-<id>`. Where a row's Note column
  gives a reason but no `PLAT-<id>` reference (auth SESSION_TTL_SEC, auth
  TLS_MIN_VERSION), it is treated as an undocumented violation, not a
  partially-documented override — the protocol text does not describe a
  partial-credit category.
- Where a divergence's Note column is empty but the service's free-text
  "Operational notes" narrate a reason with no `PLAT-<id>` reference
  (billing IDEMPOTENCY_WINDOW_SEC, search DB_POOL_SIZE, search
  LOG_RETENTION_DAYS, notify BATCH_WINDOW_MS), this is treated identically
  to an undocumented divergence — a narrative reason without an approval
  reference does not satisfy the override protocol.
- No cross-service or cross-file validation of the `PLAT-<id>` approval
  references themselves was performed (e.g., confirming PLAT-482, PLAT-511,
  PLAT-390 exist in an approvals ledger) — no such ledger was among the
  five fixtures in scope, and the task rules forbid reading anything under
  `benchmarks/mission-vs-goal/` beyond the named fixtures and this output
  file. This is unmeasured; the audit takes the approval-reference citation
  in the config file at face value, which is what the task prompt's
  override-classification rule calls for.
- "Last reviewed" dates and "Owner" fields in each service config were read
  but are not compliance-relevant to the canonical-constants comparison, so
  they are not scored as rows in the compliance table.

## Stop Condition

This artifact is complete: it exists at the required path, contains all
five required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), includes a full per-service compliance table covering all 4
services × 9 canonical constants (36/36 rows, all with explicit
Compliant / Violation / Documented-override status), a violations section
with quoted fixture evidence for each of the 6 confirmed violations, and a
rejected-candidates section citing the approval reference for each of the 3
documented overrides. No further fixture reads or edits are needed; the
`/goal` condition is satisfied by this file's existence and content as of
this write.
