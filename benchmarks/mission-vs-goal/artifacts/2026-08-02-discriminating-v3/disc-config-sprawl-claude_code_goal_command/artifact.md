# Configuration Compliance Audit — auth / billing / search / notify vs PLAT-CONFIG v4

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-discriminating-v3/disc-config-sprawl-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`platform-defaults.md`), producing a full per-service compliance table, a violations section with quoted evidence for every undocumented divergence, and a rejected-candidates section citing each documented override's approval reference.

## Result

Read the five specified fixtures only:
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

The platform canonical defaults define 9 constants (`CONNECT_TIMEOUT_MS`, `REQUEST_RETRY_MAX`, `SESSION_TTL_SEC`, `DB_POOL_SIZE`, `BATCH_WINDOW_MS`, `TLS_MIN_VERSION`, `CACHE_TTL_SEC`, `IDEMPOTENCY_WINDOW_SEC`, `LOG_RETENTION_DAYS`). Each of the 4 services defines all 9 constants (36 rows total, no missing rows in any service config as read).

Summary counts:
- Compliant rows: 27 / 36
- Undocumented violations: 6
- Documented overrides (rejected candidates, non-findings): 3

### Full per-service compliance table

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
| billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (rejected candidate) |
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
| search | CACHE_TTL_SEC | 300 | 30 | Documented override (rejected candidate) |
| search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| search | LOG_RETENTION_DAYS | 30 | 45 | **Violation (undocumented)** |
| notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| notify | REQUEST_RETRY_MAX | 5 | 2 | Documented override (rejected candidate) |
| notify | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| notify | DB_POOL_SIZE | 64 | 64 | Compliant |
| notify | BATCH_WINDOW_MS | 500 | 250 | **Violation (undocumented)** |
| notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| notify | CACHE_TTL_SEC | 300 | 300 | Compliant |
| notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| notify | LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### Violations (undocumented divergences)

1. **auth / `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`. Fixture quote (auth `config.md` row): `| SESSION_TTL_SEC | 7200 | |` and canonical (`platform-defaults.md`): `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`. Operational note: "session length was extended during the 2026-04 login incident and the change was kept afterwards" — no `PLAT-<id>` approval reference is cited anywhere in the auth config, so per the override protocol ("Overrides without an approval reference are treated as violations") this is a violation, not a documented override.

2. **auth / `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`. Fixture quote: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` vs canonical `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`. The note gives a reason ("legacy SDK compat") but cites no `PLAT-<id>` approval reference — the operational notes explicitly describe it as pinned for an old SDK with an open deprecation ticket, not an approved override. Violation.

3. **billing / `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`. Fixture quote: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` vs canonical `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`. Operational note: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no approval reference cited. Violation.

4. **search / `DB_POOL_SIZE`** — canonical `64`, actual `128`. Fixture quote: `| DB_POOL_SIZE | 128 | |` vs canonical `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`. Operational note: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference cited. Violation.

5. **search / `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`. Fixture quote: `| LOG_RETENTION_DAYS | 45 | |` vs canonical `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`. Operational note explicitly states: "nobody filed the retention change with the platform team." Violation.

6. **notify / `BATCH_WINDOW_MS`** — canonical `500`, actual `250`. Fixture quote: `| BATCH_WINDOW_MS | 250 | |` vs canonical `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`. Operational note: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference cited (unlike the adjacent `REQUEST_RETRY_MAX` override in the same file, which does cite `PLAT-390`). Violation.

### Rejected candidates (documented overrides — non-findings)

These looked like divergences from the canonical value but are documented overrides with a cited approval reference, so per the override protocol they are **not** compliance findings:

1. **billing / `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`. Fixture quote: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`, and operational notes confirm: "The connect timeout override follows the platform override protocol with approval reference PLAT-482." **Approval reference: PLAT-482.** Rejected — this is a documented, approved override, not a violation.

2. **search / `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Fixture quote: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`, and operational notes confirm: "The cache TTL override follows the override protocol with approval reference PLAT-511." **Approval reference: PLAT-511.** Rejected — documented, approved override.

3. **notify / `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Fixture quote: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`, and operational notes confirm: "The retry override follows the override protocol with approval reference PLAT-390." **Approval reference: PLAT-390.** Rejected — documented, approved override.

### Candidates considered and explicitly not raised as either a violation or a documented override

None. All 9 divergences found across the 4 services (6 undocumented, 3 documented) are accounted for above; every other row (27 of 36) matches the canonical value exactly and required no further judgment.

## Evidence

All values quoted above are copied verbatim from the table rows and operational-notes prose in the five fixtures read for this task:
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` (canonical values and rationale column, override protocol paragraph)
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md` (`SESSION_TTL_SEC`, `TLS_MIN_VERSION` rows and operational notes)
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md` (`CONNECT_TIMEOUT_MS`, `IDEMPOTENCY_WINDOW_SEC` rows and operational notes)
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md` (`DB_POOL_SIZE`, `CACHE_TTL_SEC`, `LOG_RETENTION_DAYS` rows and operational notes)
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md` (`REQUEST_RETRY_MAX`, `BATCH_WINDOW_MS` rows and operational notes)

No fixtures outside this list, and no other files under `benchmarks/mission-vs-goal/`, were opened, read, grepped, or listed while producing this artifact.

Row-count and completeness check (unmeasured beyond direct inspection — no automated diff tool was run; this is a manual line-by-line comparison of the two Markdown tables per service against the canonical table): each of the 4 service configs lists exactly the same 9 constants as `platform-defaults.md`, in the same order, with no rows omitted — so there are no "missing row" audit failures to report for any of the 4 services.

## Assumptions

- "Documented override" is interpreted per the platform's own override protocol (`platform-defaults.md`, lines 19–21): the service config must state the constant, the overridden value, the reason, **and the approval reference**; overrides without an approval reference are violations. This is stated in the fixture itself, not inferred.
- Where a service's note gives a reason for a divergence (e.g., auth's "legacy SDK compat" for `TLS_MIN_VERSION`, or search's load-test explanation for `DB_POOL_SIZE`) but cites no `PLAT-<id>` reference, this was treated as an undocumented violation rather than a documented override, per the explicit protocol text. This is a direct application of the stated rule, not a judgment call beyond it.
- All 9 canonical constants were assumed to be the complete and exhaustive set of constants in scope, since `platform-defaults.md` lists exactly 9 and each service config lists exactly the same 9 — no additional service-only constants outside the canonical list were observed in any of the 4 configs as read.
- No fixture outside the 5 named files was consulted, so anything the task might additionally validate against (e.g., an out-of-scope answer key or scoring config) is explicitly unmeasured and not represented in this audit.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-discriminating-v3/disc-config-sprawl-claude_code_goal_command.md` and includes the required Goal, Result, Evidence, Assumptions, and Stop Condition headings. It contains a full per-service compliance table covering all 9 canonical constants for all 4 services (36 rows, none missing), a violations section with quoted fixture evidence for each of the 6 undocumented divergences, and a rejected-candidates section citing the approval reference (`PLAT-482`, `PLAT-511`, `PLAT-390`) for each of the 3 documented overrides. No commit, push, package install, or network access was performed; only this output file was written, and only the 5 named fixtures plus this file were accessed under `benchmarks/mission-vs-goal/`. The task is complete.
