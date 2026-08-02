# Portfolio CX Config Audit — claude_code_goal_command arm

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-cx-config-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit configuration compliance for four services (auth, billing, search,
notify) against the platform canonical defaults (`PLAT-CONFIG v4`), producing:
a full per-service compliance table for every canonical constant, a violations
section with quoted evidence for undocumented divergences, and a
rejected-candidates section for documented overrides (citing approval
references).

## Result

Read exactly the five named fixtures:
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped,
or listed.

### Per-service compliance table

Canonical values (from `platform-defaults.md`): `CONNECT_TIMEOUT_MS`=4000,
`REQUEST_RETRY_MAX`=5, `SESSION_TTL_SEC`=3600, `DB_POOL_SIZE`=64,
`BATCH_WINDOW_MS`=500, `TLS_MIN_VERSION`=TLSv1.2, `CACHE_TTL_SEC`=300,
`IDEMPOTENCY_WINDOW_SEC`=600, `LOG_RETENTION_DAYS`=30.

| Constant | Canonical | auth | billing | search | notify |
|---|---|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 — compliant | 12000 — **documented override** (PLAT-482) | 4000 — compliant | 4000 — compliant |
| REQUEST_RETRY_MAX | 5 | 5 — compliant | 5 — compliant | 5 — compliant | 2 — **documented override** (PLAT-390) |
| SESSION_TTL_SEC | 3600 | 7200 — **VIOLATION** | 3600 — compliant | 3600 — compliant | 3600 — compliant |
| DB_POOL_SIZE | 64 | 64 — compliant | 64 — compliant | 128 — **VIOLATION** | 64 — compliant |
| BATCH_WINDOW_MS | 500 | 500 — compliant | 500 — compliant | 500 — compliant | 250 — **VIOLATION** |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 — **VIOLATION** | TLSv1.2 — compliant | TLSv1.2 — compliant | TLSv1.2 — compliant |
| CACHE_TTL_SEC | 300 | 300 — compliant | 300 — compliant | 30 — **documented override** (PLAT-511) | 300 — compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 — compliant | 86400 — **VIOLATION** | 600 — compliant | 600 — compliant |
| LOG_RETENTION_DAYS | 30 | 30 — compliant | 30 — compliant | 45 — **VIOLATION** | 30 — compliant |

All 9 canonical constants × 4 services = 36 rows are covered above; no row is
missing.

### Violations (undocumented divergences — confirmed findings)

1. **auth-service / `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`.
   Evidence (`auth/config.md`): `| SESSION_TTL_SEC | 7200 | |` with the note
   "session length was extended during the 2026-04 login incident and the
   change was kept afterwards." No approval reference (`PLAT-<id>`) is cited
   anywhere in the file. Per the override protocol in
   `platform-defaults.md` ("Overrides without an approval reference are
   treated as violations"), this is a violation.

2. **auth-service / `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual
   `TLSv1.1`. Evidence (`auth/config.md`): `| TLS_MIN_VERSION | TLSv1.1 |
   legacy SDK compat |` with note "The TLS floor is pinned for an older
   mobile SDK; the SDK deprecation ticket is still open." No approval
   reference is cited. Violation. This also directly contradicts the
   canonical rationale in `platform-defaults.md`: "TLSv1.1 is end-of-life."

3. **billing-service / `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual
   `86400`. Evidence (`billing/config.md`): `| IDEMPOTENCY_WINDOW_SEC | 86400
   | |` with note "the idempotency window was widened while debugging
   duplicate settlement webhooks in 2026-03." No approval reference is
   cited for this specific row (only `CONNECT_TIMEOUT_MS` in this file cites
   `PLAT-482`). Violation.

4. **search-service / `DB_POOL_SIZE`** — canonical `64`, actual `128`.
   Evidence (`search/config.md`): `| DB_POOL_SIZE | 128 | |` with note "the
   pool was doubled during a 2026-05 load test and never reverted." No
   approval reference cited. Violation.

5. **search-service / `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`.
   Evidence (`search/config.md`): `| LOG_RETENTION_DAYS | 45 | |` with note
   "Query logs are kept 45 days to debug relevance regressions; nobody filed
   the retention change with the platform team." The file itself states no
   approval reference exists. Violation.

6. **notify-service / `BATCH_WINDOW_MS`** — canonical `500`, actual `250`.
   Evidence (`notify/config.md`): `| BATCH_WINDOW_MS | 250 | |` with note
   "the batch window was halved to reduce push latency during the 2026-06
   campaign." No approval reference is cited for this row (only
   `REQUEST_RETRY_MAX` in this file cites `PLAT-390`). Violation.

### Rejected candidates (documented overrides — not findings)

1. **billing-service / `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual
   `12000`. Evidence (`billing/config.md`): `| CONNECT_TIMEOUT_MS | 12000 |
   Override: PSP provider p99 latency is 9s; approved PLAT-482 |`, and
   operational notes confirm "The connect timeout override follows the
   platform override protocol with approval reference PLAT-482." **Rejected**
   — documented override, approval reference `PLAT-482` cited.

2. **search-service / `CACHE_TTL_SEC`** — canonical `300`, actual `30`.
   Evidence (`search/config.md`): `| CACHE_TTL_SEC | 30 | Override: suggestion
   freshness SLA requires 30s; approved PLAT-511 |`, and operational notes
   confirm "The cache TTL override follows the override protocol with
   approval reference PLAT-511." **Rejected** — documented override, approval
   reference `PLAT-511` cited.

3. **notify-service / `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`.
   Evidence (`notify/config.md`): `| REQUEST_RETRY_MAX | 2 | Override: at-most
   -once delivery guarantee; approved PLAT-390 |`, and operational notes
   confirm "The retry override follows the override protocol with approval
   reference PLAT-390." **Rejected** — documented override, approval
   reference `PLAT-390` cited.

## Evidence

All quotes above are copied verbatim from the five named fixture files (table
rows and operational-notes prose), each cited inline next to the finding it
supports. No other fixture or metadata file under `benchmarks/mission-vs-goal/`
was accessed to produce this audit.

Summary counts (derived directly from the table, not independently measured
elsewhere): 4 services × 9 canonical constants = 36 rows audited; 27
compliant, 6 undocumented violations, 3 documented/rejected overrides
(27 + 6 + 3 = 36).

## Assumptions

- "Documented override" requires both an explicit override note in the
  service config AND a `PLAT-<id>` approval reference, per the protocol
  stated in `platform-defaults.md` ("Overrides without an approval reference
  are treated as violations"). An operational note explaining *why* a value
  diverges (e.g. auth's TLS/session notes, billing's idempotency note,
  search's pool/retention notes, notify's batch-window note) is not treated
  as equivalent to a documented override when no `PLAT-<id>` reference is
  present — those are scored as violations, not rejected candidates.
- No information beyond the five listed fixtures was available or used; any
  fact not present in those five files (e.g. whether an approval reference
  exists elsewhere, undisclosed to this task) is unmeasured and not assumed.
- File contents are taken as authoritative and current as read at execution
  time (2026-08-02); no version-control history was consulted per the task's
  access restriction.

## Stop Condition

This artifact satisfies the goal: it exists at the required path, contains
the headings Goal, Result, Evidence, Assumptions, and Stop Condition, includes
a full per-service compliance table covering all 9 canonical constants × 4
services, a violations section with quoted evidence for all 6 undocumented
divergences, and a rejected-candidates section citing the approval reference
for each of the 3 documented overrides. No commit, push, package install, or
network access was performed. Only this output file was written.
