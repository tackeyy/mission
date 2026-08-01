# disc-config-sprawl-mission

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against platform canonical defaults (PLAT-CONFIG v4). Classify every constant per service as **compliant**, **undocumented divergence** (violation), or **documented override** (approved). Produce a single artifact with exhaustive per-service coverage.

---

## Plan

| # | Action | Input | Output | Completion condition | Deps | Parallel |
|---|---|---|---|---|---|---|
| 1 | Read platform defaults | platform-defaults.md | Canonical constant table (9 rows) | All 9 constants extracted | — | with 2–5 |
| 2 | Read auth/config.md | auth/config.md | auth constant table | All 9 constants extracted | — | with 1,3–5 |
| 3 | Read billing/config.md | billing/config.md | billing constant table | All 9 constants extracted | — | with 1,2,4,5 |
| 4 | Read search/config.md | search/config.md | search constant table | All 9 constants extracted | — | with 1–3,5 |
| 5 | Read notify/config.md | notify/config.md | notify constant table | All 9 constants extracted | — | with 1–4 |
| 6 | Build per-service compliance tables | Steps 1–5 outputs | Classification per constant×service | Every cell filled; override references captured | 1–5 | — |
| 7 | Identify violations | Step 6 | Violations list with canonical vs actual values | All undocumented divergences enumerated | 6 | with 8 |
| 8 | Identify documented overrides | Step 6 | Rejected-candidates list with approval refs | All PLAT-IDs recorded | 6 | with 7 |
| 9 | Write artifact | Steps 6–8 | disc-config-sprawl-mission.md | File written, all required sections present | 7,8 | — |
| 10 | Self-review | Step 9 artifact | Review notes | All 9 constants × 4 services covered; violations & overrides sections complete | 9 | — |

**Risks:**
- Missing approval reference in service config → treat as violation (per platform override protocol).
- Operational-notes-only justification without PLAT-ID → still a violation.

---

## Execution

### Platform Canonical Defaults (PLAT-CONFIG v4)

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

Override protocol (from platform-defaults.md): a service config must state the constant, the overridden value, the reason, **and an approval reference (`PLAT-<id>`)**. Overrides without an approval reference are treated as violations.

---

### Per-Service Compliance Tables

Legend: **C** = compliant | **V** = undocumented divergence (violation) | **O** = documented override

#### auth-service

Owner: identity team. Last reviewed 2026-05-02.

| Constant | Canonical | Actual | Status | Note |
|---|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | C | |
| REQUEST_RETRY_MAX | 5 | 5 | C | |
| SESSION_TTL_SEC | 3600 | 7200 | **V** | Extended during 2026-04 login incident; no approval reference |
| DB_POOL_SIZE | 64 | 64 | C | |
| BATCH_WINDOW_MS | 500 | 500 | C | |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **V** | "legacy SDK compat" stated as reason; no approval reference (PLAT-ID absent) |
| CACHE_TTL_SEC | 300 | 300 | C | |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | C | |
| LOG_RETENTION_DAYS | 30 | 30 | C | |

auth violations: **2** | documented overrides: 0

---

#### billing-service

Owner: payments team. Last reviewed 2026-06-11.

| Constant | Canonical | Actual | Status | Note |
|---|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | **O** | PSP provider p99 latency 9s; approved PLAT-482 |
| REQUEST_RETRY_MAX | 5 | 5 | C | |
| SESSION_TTL_SEC | 3600 | 3600 | C | |
| DB_POOL_SIZE | 64 | 64 | C | |
| BATCH_WINDOW_MS | 500 | 500 | C | |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | C | |
| CACHE_TTL_SEC | 300 | 300 | C | |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **V** | "Widened while debugging duplicate settlement webhooks in 2026-03"; no approval reference |
| LOG_RETENTION_DAYS | 30 | 30 | C | |

billing violations: **1** | documented overrides: 1

---

#### search-service

Owner: discovery team. Last reviewed 2026-06-27.

| Constant | Canonical | Actual | Status | Note |
|---|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | C | |
| REQUEST_RETRY_MAX | 5 | 5 | C | |
| SESSION_TTL_SEC | 3600 | 3600 | C | |
| DB_POOL_SIZE | 64 | 128 | **V** | "Doubled during 2026-05 load test and never reverted"; no approval reference |
| BATCH_WINDOW_MS | 500 | 500 | C | |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | C | |
| CACHE_TTL_SEC | 300 | 30 | **O** | Suggestion freshness SLA requires 30s; approved PLAT-511 |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | C | |
| LOG_RETENTION_DAYS | 30 | 45 | **V** | "Nobody filed the retention change with the platform team" |

search violations: **2** | documented overrides: 1

---

#### notify-service

Owner: messaging team. Last reviewed 2026-06-20.

| Constant | Canonical | Actual | Status | Note |
|---|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | C | |
| REQUEST_RETRY_MAX | 5 | 2 | **O** | At-most-once delivery guarantee; approved PLAT-390 |
| SESSION_TTL_SEC | 3600 | 3600 | C | |
| DB_POOL_SIZE | 64 | 64 | C | |
| BATCH_WINDOW_MS | 500 | 250 | **V** | "Halved to reduce push latency during 2026-06 campaign"; no approval reference |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | C | |
| CACHE_TTL_SEC | 300 | 300 | C | |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | C | |
| LOG_RETENTION_DAYS | 30 | 30 | C | |

notify violations: **1** | documented overrides: 1

---

### Violations (Undocumented Divergences)

All 6 violations below have no approval reference (`PLAT-<id>`) in the service config. Per the platform override protocol, they are compliance violations.

| # | Service | Constant | Canonical value | Actual value | Evidence from config |
|---|---|---|---|---|---|
| V-1 | auth | SESSION_TTL_SEC | 3600 | 7200 | No override note, no PLAT-ID; operational note says "extended during 2026-04 login incident" |
| V-2 | auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | Note says "legacy SDK compat"; no PLAT-ID present |
| V-3 | billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | Operational note references "duplicate settlement webhooks" debug in 2026-03; no PLAT-ID in config table |
| V-4 | search | DB_POOL_SIZE | 64 | 128 | Operational note acknowledges "never reverted" after 2026-05 load test; no PLAT-ID |
| V-5 | search | LOG_RETENTION_DAYS | 30 | 45 | Operational note explicitly states "nobody filed the retention change with the platform team" |
| V-6 | notify | BATCH_WINDOW_MS | 500 | 250 | Operational note references "2026-06 campaign" latency reduction; no PLAT-ID |

---

### Rejected Candidates (Documented Overrides — Not Violations)

These constants diverge from canonical defaults but satisfy the override protocol: constant, overridden value, reason, and approval reference are all present in the service config.

| Service | Constant | Canonical value | Override value | Approval ref | Stated reason |
|---|---|---|---|---|---|
| billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | PLAT-482 | PSP provider p99 latency is 9s |
| search | CACHE_TTL_SEC | 300 | 30 | PLAT-511 | Suggestion freshness SLA requires 30s |
| notify | REQUEST_RETRY_MAX | 5 | 2 | PLAT-390 | At-most-once delivery guarantee |

---

## Review

Self-review checklist:

- [x] All 9 canonical constants enumerated for each of the 4 services (36 cells total)
- [x] Every cell classified as C / V / O — no gaps
- [x] Violations section quotes exact constant name, canonical value, actual value, and evidence
- [x] Rejected-candidates section cites each documented override's approval reference
- [x] Coverage exhaustive: compliant constants included in per-service tables, not just violations
- [x] Only the 5 specified fixture files were read; no other benchmarks/mission-vs-goal/ files accessed
- [x] No commit/push/install/network operations performed

Classification rationale for edge cases:
- **auth SESSION_TTL_SEC**: operational note describes history but the config table has no `Note` entry and no PLAT-ID → violation.
- **auth TLS_MIN_VERSION**: "legacy SDK compat" in the Note column is a reason but not an approval reference → violation per protocol.
- **billing IDEMPOTENCY_WINDOW_SEC**: operational note explains the change but the config table Note cell is blank and no PLAT-ID is cited → violation.
- **search DB_POOL_SIZE / LOG_RETENTION_DAYS**: both operational notes acknowledge the absence of formal approval → violations.
- **notify BATCH_WINDOW_MS**: operational note mentions a campaign but no PLAT-ID appears in the config table → violation.

---

## Score

| Criterion | Assessment |
|---|---|
| Exhaustive constant enumeration | All 36 cells (9 constants × 4 services) covered |
| Correct violation identification | 6 violations identified; all supported by absence of PLAT-ID |
| Correct override identification | 3 documented overrides; each with PLAT-ID |
| No false positives | Compliant cells correctly classified as C |
| Artifact completeness | All 8 required sections present |
| Scope compliance | Only 5 specified files read; no out-of-bounds access |

Estimated score: **4.5 / 5** (pending external validation)

---

## Stop Decision

Mission complete. All required sections populated, all 36 constant×service cells classified, violations and documented overrides enumerated with evidence. No blockers. No further iterations required.

---

## Evidence

Source files read (exactly the 5 specified fixtures):

1. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` — 9 canonical constants, override protocol definition
2. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md` — auth-service, 9 constants, 2 violations (SESSION_TTL_SEC, TLS_MIN_VERSION)
3. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md` — billing-service, 9 constants, 1 violation (IDEMPOTENCY_WINDOW_SEC), 1 override (CONNECT_TIMEOUT_MS / PLAT-482)
4. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md` — search-service, 9 constants, 2 violations (DB_POOL_SIZE, LOG_RETENTION_DAYS), 1 override (CACHE_TTL_SEC / PLAT-511)
5. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md` — notify-service, 9 constants, 1 violation (BATCH_WINDOW_MS), 1 override (REQUEST_RETRY_MAX / PLAT-390)

No other files were accessed.

---

## Assumptions

1. **Override protocol is binary**: a divergence is either a documented override (constant + value + reason + PLAT-ID all present in the config table or its inline Note) or a violation. Operational notes in the prose section do not substitute for a PLAT-ID in the config table.
2. **Approval reference format**: `PLAT-<numeric-id>` is the recognized format per the platform-defaults.md override protocol. Textual justifications without this token are not approved overrides.
3. **All 9 canonical constants apply to all 4 services**: the platform defaults document says "Every service MUST use these values unless…" with no service-type exemptions.
4. **Service configs are exhaustive**: any canonical constant absent from a service config table would be a violation by omission. In practice all 4 services enumerate all 9 constants, so this did not arise.
5. **Operational notes are not override records**: prose in the "Operational notes" section describes history and intent but does not constitute the override protocol entry; the inline Note column in the config table is the canonical location for override declarations.
