# Config Sprawl Audit — auth / billing / search / notify vs Platform Canonical Defaults (PLAT-CONFIG v4)

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-smoke/disc-config-sprawl-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit configuration compliance for four services (auth, billing, search,
notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every
constant in every service, classify as compliant, undocumented divergence
(violation), or documented override (rejected as a non-finding, citing the
approval reference). Quote exact constant name, canonical value, and actual
value for every divergence.

## Result

Full per-service compliance table (9 canonical constants x 4 services = 36 rows, all present, no missing rows):

### auth-service (Owner: identity team, last reviewed 2026-05-02)

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

### billing-service (Owner: payments team, last reviewed 2026-06-11)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (rejected — see below) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

### search-service (Owner: discovery team, last reviewed 2026-06-27)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (rejected — see below) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence) |

### notify-service (Owner: messaging team, last reviewed 2026-06-20)

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (rejected — see below) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

**Tally**: 36 rows total — 27 compliant, 6 confirmed violations, 3 rejected candidates (documented overrides).

## Evidence

### Confirmed violations (undocumented divergence — no approval reference cited)

1. **auth-service / `SESSION_TTL_SEC`** — canonical value `3600` (`platform-defaults.md`: "| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |"). Actual value in `auth/config.md`: "| SESSION_TTL_SEC | 7200 | |". The service's own operational notes state: "session length was extended during the 2026-04 login incident and the change was kept afterwards" — no `PLAT-<id>` approval reference is given anywhere in the file, so per the override protocol ("Overrides without an approval reference are treated as violations") this is a violation.

2. **auth-service / `TLS_MIN_VERSION`** — canonical value `TLSv1.2` (`platform-defaults.md`: "| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |"). Actual value in `auth/config.md`: "| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |". The note explains the reason ("legacy SDK compat") and the operational notes add "The TLS floor is pinned for an older mobile SDK; the SDK deprecation ticket is still open" — but no `PLAT-<id>` approval reference is cited. A reason without an approval reference does not meet the override protocol, so this is a violation.

3. **billing-service / `IDEMPOTENCY_WINDOW_SEC`** — canonical value `600` (`platform-defaults.md`: "| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |"). Actual value in `billing/config.md`: "| IDEMPOTENCY_WINDOW_SEC | 86400 | |". Operational notes state: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no `PLAT-<id>` reference given (the file's only approval reference, `PLAT-482`, is explicitly tied to `CONNECT_TIMEOUT_MS`, not this constant). Violation.

4. **search-service / `DB_POOL_SIZE`** — canonical value `64` (`platform-defaults.md`: "| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |"). Actual value in `search/config.md`: "| DB_POOL_SIZE | 128 | |". Operational notes: "the pool was doubled during a 2026-05 load test and never reverted" — no approval reference. Violation.

5. **search-service / `LOG_RETENTION_DAYS`** — canonical value `30` (`platform-defaults.md`: "| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |"). Actual value in `search/config.md`: "| LOG_RETENTION_DAYS | 45 | |". Operational notes explicitly confirm this is undocumented: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Violation.

6. **notify-service / `BATCH_WINDOW_MS`** — canonical value `500` (`platform-defaults.md`: "| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |"). Actual value in `notify/config.md`: "| BATCH_WINDOW_MS | 250 | |". Operational notes: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference (the file's only approval reference, `PLAT-390`, is explicitly tied to `REQUEST_RETRY_MAX`, not this constant). Violation.

### Rejected candidates (documented overrides — look like divergences but cite a valid approval reference)

1. **billing-service / `CONNECT_TIMEOUT_MS`** — actual `12000` vs canonical `4000`. Looks like a violation at first glance, but `billing/config.md` states in the table note: "Override: PSP provider p99 latency is 9s; approved PLAT-482" and the operational notes confirm: "The connect timeout override follows the platform override protocol with approval reference PLAT-482." Rejected — documented override with approval reference `PLAT-482`.

2. **search-service / `CACHE_TTL_SEC`** — actual `30` vs canonical `300`. Table note: "Override: suggestion freshness SLA requires 30s; approved PLAT-511" and operational notes: "The cache TTL override follows the override protocol with approval reference PLAT-511." Rejected — documented override with approval reference `PLAT-511`.

3. **notify-service / `REQUEST_RETRY_MAX`** — actual `2` vs canonical `5`. Table note: "Override: at-most-once delivery guarantee; approved PLAT-390" and operational notes: "The retry override follows the override protocol with approval reference PLAT-390." Rejected — documented override with approval reference `PLAT-390`.

### Not measured / out of scope

- No data was available on runtime enforcement (i.e., whether these config files reflect what is actually deployed) — compliance here is assessed strictly against the four `config.md` files as written, which is unmeasured beyond the document contents themselves.
- No additional services beyond auth, billing, search, and notify were in scope, per the task prompt's explicit fixture list.

## Assumptions

- The five fixture files listed in the task prompt are treated as complete and authoritative for this audit; no other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed, per the task's scope restriction.
- An override is only accepted as "documented" when a `PLAT-<id>` approval reference is explicitly present and tied (by row or by cross-reference in the operational notes) to that specific constant — a stated business reason without a `PLAT-<id>` reference is treated as insufficient per the platform's own override protocol ("Overrides without an approval reference are treated as violations").
- Where a service config's per-row note is blank but the operational-notes prose explains a divergence without citing a `PLAT-<id>` reference (auth `SESSION_TTL_SEC`, auth `TLS_MIN_VERSION`, billing `IDEMPOTENCY_WINDOW_SEC`, search `DB_POOL_SIZE`, search `LOG_RETENTION_DAYS`, notify `BATCH_WINDOW_MS`), this is treated as an undocumented divergence (violation), not a documented override.
- "Last reviewed" dates and team ownership are descriptive context only and do not affect compliance classification.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five
required headings (Goal, Result, Evidence, Assumptions, Stop Condition),
provides a full per-service compliance table covering all 9 canonical
constants x 4 services (36/36 rows, no missing rows), a violations section
quoting exact constant/canonical/actual values with fixture evidence for all
6 confirmed violations, and a rejected-candidates section citing the approval
reference (`PLAT-482`, `PLAT-511`, `PLAT-390`) for each of the 3 documented
overrides. No commit, push, package install, or network access was performed.
Only the five named fixture files and this output file were accessed.
