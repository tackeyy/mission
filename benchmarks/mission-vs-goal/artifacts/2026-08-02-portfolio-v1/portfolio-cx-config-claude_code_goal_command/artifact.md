# Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-cx-config-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit configuration compliance for four services (auth, billing, search, notify)
against the platform canonical defaults (`platform-defaults.md`). For every constant in
every service, classify it as compliant, an undocumented divergence (violation), or a
documented override (must cite the approval reference and be rejected as a non-finding).

Fixtures read (exactly these five, no others):
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

# Result

Full per-service compliance table against the 9 canonical constants defined in
`platform-defaults.md` (`CONNECT_TIMEOUT_MS`, `REQUEST_RETRY_MAX`, `SESSION_TTL_SEC`,
`DB_POOL_SIZE`, `BATCH_WINDOW_MS`, `TLS_MIN_VERSION`, `CACHE_TTL_SEC`,
`IDEMPOTENCY_WINDOW_SEC`, `LOG_RETENTION_DAYS`).

## Compliance table — auth-service

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

## Compliance table — billing-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override — rejected (see below) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented) |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Compliance table — search-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented) |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override — rejected (see below) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented) |

## Compliance table — notify-service

| Constant | Canonical | Actual | Status |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override — rejected (see below) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented) |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

## Violations (undocumented divergences — confirmed findings)

Per `platform-defaults.md`: "Overrides without an approval reference are treated as
violations." Each item below has a divergent value with no `PLAT-<id>` approval
reference cited anywhere in the service config's Note column or operational notes.

1. **auth-service — `SESSION_TTL_SEC`**: canonical `3600`, actual `7200`. Quote from
   `auth/config.md`: `| SESSION_TTL_SEC | 7200 | |` and "session length was extended
   during the 2026-04 login incident and the change was kept afterwards." No approval
   reference is cited — this is an undocumented divergence, not a documented override.
2. **auth-service — `TLS_MIN_VERSION`**: canonical `TLSv1.2`, actual `TLSv1.1`. Quote:
   `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` and "The TLS floor is pinned for
   an older mobile SDK; the SDK deprecation ticket is still open." A reason is given
   ("legacy SDK compat") but no `PLAT-<id>` approval reference is cited, so per the
   override protocol this does not qualify as a documented override — it is a
   violation. This also directly contradicts the platform rationale: "TLSv1.1 is
   end-of-life."
3. **billing-service — `IDEMPOTENCY_WINDOW_SEC`**: canonical `600`, actual `86400`.
   Quote: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` and "the idempotency window was
   widened while debugging duplicate settlement webhooks in 2026-03." No approval
   reference is cited for this value — undocumented divergence.
4. **search-service — `DB_POOL_SIZE`**: canonical `64`, actual `128`. Quote:
   `| DB_POOL_SIZE | 128 | |` and "the pool was doubled during a 2026-05 load test and
   never reverted." No approval reference — undocumented divergence.
5. **search-service — `LOG_RETENTION_DAYS`**: canonical `30`, actual `45`. Quote:
   `| LOG_RETENTION_DAYS | 45 | |` and "Query logs are kept 45 days to debug relevance
   regressions; nobody filed the retention change with the platform team." The note
   explicitly confirms no filing occurred — undocumented divergence.
6. **notify-service — `BATCH_WINDOW_MS`**: canonical `500`, actual `250`. Quote:
   `| BATCH_WINDOW_MS | 250 | |` and "the batch window was halved to reduce push
   latency during the 2026-06 campaign." No approval reference cited — undocumented
   divergence.

## Rejected candidates (documented overrides — not findings)

Each of these has a divergent value but cites a `PLAT-<id>` approval reference per the
platform's override protocol, so it is rejected as a non-finding (compliant-as-overridden).

1. **billing-service — `CONNECT_TIMEOUT_MS`**: canonical `4000`, actual `12000`. Quote:
   `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved
   PLAT-482 |` and "The connect timeout override follows the platform override
   protocol with approval reference PLAT-482." Approval reference: **PLAT-482**.
   Rejected — documented override, not a violation.
2. **search-service — `CACHE_TTL_SEC`**: canonical `300`, actual `30`. Quote:
   `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved
   PLAT-511 |` and "The cache TTL override follows the override protocol with approval
   reference PLAT-511." Approval reference: **PLAT-511**. Rejected — documented
   override, not a violation.
3. **notify-service — `REQUEST_RETRY_MAX`**: canonical `5`, actual `2`. Quote:
   `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved
   PLAT-390 |` and "The retry override follows the override protocol with approval
   reference PLAT-390." Approval reference: **PLAT-390**. Rejected — documented
   override, not a violation.

## Summary counts

- Constants audited: 9 canonical constants × 4 services = 36 rows (all 36 present
  above; no missing rows).
- Compliant: 27
- Violations (undocumented divergence): 6
- Documented overrides (rejected candidates): 3

# Evidence

All quotes below are verbatim from the five fixture files read for this task.

- Canonical defaults source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  - "Every service MUST use these values unless an override is documented in the
    service config with an approval reference (`PLAT-<id>`). Undocumented divergence
    is a compliance violation."
  - "Override protocol: the service config must state the constant, the overridden
    value, the reason, and the approval reference. Overrides without an approval
    reference are treated as violations."
  - Table rows: `CONNECT_TIMEOUT_MS | 4000`, `REQUEST_RETRY_MAX | 5`,
    `SESSION_TTL_SEC | 3600`, `DB_POOL_SIZE | 64`, `BATCH_WINDOW_MS | 500`,
    `TLS_MIN_VERSION | TLSv1.2`, `CACHE_TTL_SEC | 300`,
    `IDEMPOTENCY_WINDOW_SEC | 600`, `LOG_RETENTION_DAYS | 30`.
- auth-service source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  (full 9-row table quoted/tabulated above; operational notes quoted above).
- billing-service source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  (full 9-row table quoted/tabulated above; operational notes quoted above).
- search-service source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  (full 9-row table quoted/tabulated above; operational notes quoted above).
- notify-service source: `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
  (full 9-row table quoted/tabulated above; operational notes quoted above).

Nothing in this audit is unmeasured — all four service configs each list exactly the
same 9 constants as the platform defaults table, so no constant is missing from any
service's config, and no row above required inference beyond what is stated in the
fixture text.

# Assumptions

- "Documented override" requires an explicit `PLAT-<id>` approval reference string
  present in the service config file itself (either in the Note column or the
  operational notes), per the platform's own override protocol text. A stated
  operational reason without a `PLAT-<id>` reference (e.g., auth-service's
  "legacy SDK compat" for `TLS_MIN_VERSION`) is treated as an undocumented divergence,
  not a documented override, because the protocol explicitly requires "the approval
  reference" as one of four mandatory elements.
- The five fixture files are treated as the complete and final source of truth for
  this audit; no other files under `benchmarks/mission-vs-goal/` were opened, read, or
  listed, per the task's scope restriction.
- No fixture defines a tolerance band (e.g., "±10% is acceptable") for any constant, so
  any numeric or string difference from the canonical value is treated as a divergence
  requiring override documentation, however small.

# Stop Condition

This artifact is complete: it contains all five required headings (Goal, Result,
Evidence, Assumptions, Stop Condition), a full per-service compliance table covering
all 9 canonical constants for all 4 services (36/36 rows present, no missing rows), a
violations section with quoted evidence for each of the 6 confirmed undocumented
divergences, and a rejected-candidates section citing the approval reference
(PLAT-482, PLAT-511, PLAT-390) for each of the 3 documented overrides. No further
fixture reads or edits are needed; the goal condition is satisfied and this file is the
only artifact written for this task.
