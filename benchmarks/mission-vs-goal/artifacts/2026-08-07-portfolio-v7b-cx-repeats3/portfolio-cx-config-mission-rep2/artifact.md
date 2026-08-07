# portfolio-cx-config Audit Artifact

**Task ID:** portfolio-cx-config  
**Run:** 2026-08-07-portfolio-v7b-cx-repeats3 / rep2  
**Date:** 2026-08-07  
**Agent arm:** mission  

---

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults defined in `platform-defaults.md` (PLAT-CONFIG v4). For every constant in every service, determine whether it is:

- **Compliant** — matches the canonical value exactly
- **Violation** — diverges from the canonical value with no documented approval reference
- **Documented override** — diverges from the canonical value with a valid `PLAT-<id>` approval reference (rejected as a finding)

Scope: 9 canonical constants × 4 services = 36 cells.

---

## Plan

1. Read `platform-defaults.md` to extract all 9 canonical constants and their values.
2. Read each service config (auth, billing, search, notify) and record the value for every canonical constant.
3. For each divergence, check whether a `PLAT-<id>` approval reference is present in the Note column.
4. Classify: compliant / violation / documented override.
5. Produce a per-service table (all 9 rows per service), a violations section with quoted evidence, and a rejected-candidates section for documented overrides.
6. Verify total cell count = 36 before finalising.

---

## Execution

### Platform canonical constants (PLAT-CONFIG v4)

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

### Per-service compliance tables

#### auth-service (9 cells)

| Constant | Canonical | Actual | Verdict |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 7200 | **Violation** |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### billing-service (9 cells)

| Constant | Canonical | Actual | Verdict |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

#### search-service (9 cells)

| Constant | Canonical | Actual | Verdict |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 128 | **Violation** |
| BATCH_WINDOW_MS | 500 | 500 | Compliant |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 45 | **Violation** |

#### notify-service (9 cells)

| Constant | Canonical | Actual | Verdict |
|---|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) |
| SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| DB_POOL_SIZE | 64 | 64 | Compliant |
| BATCH_WINDOW_MS | 500 | 250 | **Violation** |
| TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| CACHE_TTL_SEC | 300 | 300 | Compliant |
| IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| LOG_RETENTION_DAYS | 30 | 30 | Compliant |

**Cell count check:** 9 × 4 = 36 cells audited. No missing rows.

---

## Review

### Scored review iteration (mission loop, iteration 1)

Two independent reviewers were spawned in parallel (single message) and produced `mission-review/1` JSON:

| Reviewer | Perspective | Composite | High | Medium | JSON |
|---|---|---|---|---|---|
| A | 正確性・証拠品質 (accuracy / evidence quality) | 5.0 | 0 | 0 | `.mission-state/review-iter1-accuracy.json` |
| B | 網羅性・validator 適合 (completeness / validator compliance) | 5.0 | 0 | 0 | `.mission-state/review-iter1-completeness.json` |

Aggregation via `mission-state.py review-finalize --iteration 1 --min-reviewers 2` (aggregate-reviews → push-score, tool-computed): composite 5.0, min item 5.0, open_high 0, max agreement delta 0.0. Evidence archives: `.mission-state/archive/iter-1-b8fc16bc-reviews.json`, `.mission-state/archive/iter-1-b8fc16bc-scoring.json`.

### Violations (6 confirmed)

| # | Service | Constant | Canonical | Actual | Evidence |
|---|---|---|---|---|---|
| V1 | auth | SESSION_TTL_SEC | 3600 | 7200 | auth/config.md row: `SESSION_TTL_SEC \| 7200 \|` — no PLAT-\<id\> in Note column |
| V2 | auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | auth/config.md row: `TLS_MIN_VERSION \| TLSv1.1 \| legacy SDK compat` — no PLAT-\<id\> |
| V3 | billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | billing/config.md row: `IDEMPOTENCY_WINDOW_SEC \| 86400 \|` — Note column is empty; ops notes acknowledge the change was made informally ("while debugging duplicate settlement webhooks") |
| V4 | search | DB_POOL_SIZE | 64 | 128 | search/config.md row: `DB_POOL_SIZE \| 128 \|` — no PLAT-\<id\> in Note column |
| V5 | search | LOG_RETENTION_DAYS | 30 | 45 | search/config.md row: `LOG_RETENTION_DAYS \| 45 \|` — ops notes explicitly state "nobody filed the retention change with the platform team" |
| V6 | notify | BATCH_WINDOW_MS | 500 | 250 | notify/config.md row: `BATCH_WINDOW_MS \| 250 \|` — no PLAT-\<id\> in Note column; ops notes acknowledge the halving but cite no approval |

### Rejected candidates: documented overrides (3)

These divergences carry a valid `PLAT-<id>` approval reference and are therefore **not violations**.

| Service | Constant | Actual value | Approval reference | Reason stated in fixture |
|---|---|---|---|---|
| billing | CONNECT_TIMEOUT_MS | 12000 | PLAT-482 | "PSP provider p99 latency is 9s; approved PLAT-482" |
| search | CACHE_TTL_SEC | 30 | PLAT-511 | "suggestion freshness SLA requires 30s; approved PLAT-511" |
| notify | REQUEST_RETRY_MAX | 2 | PLAT-390 | "at-most-once delivery guarantee; approved PLAT-390" |

Each override provides the required fields (constant, overridden value, reason, approval reference) per the platform override protocol. They are rejected as findings.

### Fully compliant services

No service is fully compliant across all 9 constants. Every service has at least one violation or one documented override.

- auth: 2 violations
- billing: 1 violation, 1 documented override
- search: 2 violations, 1 documented override
- notify: 1 violation, 1 documented override

---

## Score

Tool-computed gate values from `mission-state.py` (score_history, iteration 1, timestamp 2026-08-07T07:40:36Z):

| Gate | Value | Threshold | Result |
|---|---|---|---|
| composite_score | 5.0 | >= 4.0 | pass |
| min(scored_items) | 5.0 (mission_achievement 5.0 / accuracy 5.0 / completeness 5.0 / usability 5.0) | >= 3.5 | pass |
| open_high | 0 | == 0 | pass |
| max_agreement_delta | 0.0 | <= 1.5 | pass |
| findings_evidence_path | `.mission-state/archive/iter-1-b8fc16bc-reviews.json` | exists | pass |

`mark-passes` (via `closeout`) returned `passes: true, forced: false`.

---

## Stop Decision

All 36 cells have been audited. All 6 violations are confirmed with quoted evidence. All 3 documented overrides are correctly rejected with approval references.

Mission loop state at stop: iteration 1 / max-iter 3, `passes: true`, `loop_active: false`, `next_action: report-complete`. Early-stop applies: threshold (4.0) reached at iteration 1 with `open_high == 0` and zero Medium findings, so no further iteration is warranted.

**Decision: STOP — audit complete, mission gate passed at iteration 1.**

---

## Evidence

Fixture files read (no files outside the listed paths were consulted):

1. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md` — 9 canonical constants
2. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md` — 9 rows
3. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md` — 9 rows
4. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md` — 9 rows
5. `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md` — 9 rows

Key quoted values:

- auth SESSION_TTL_SEC: `7200`
- auth TLS_MIN_VERSION: `TLSv1.1`
- billing IDEMPOTENCY_WINDOW_SEC: `86400`
- billing CONNECT_TIMEOUT_MS override: `12000` / `PLAT-482`
- search DB_POOL_SIZE: `128`
- search LOG_RETENTION_DAYS: `45`
- search CACHE_TTL_SEC override: `30` / `PLAT-511`
- notify BATCH_WINDOW_MS: `250`
- notify REQUEST_RETRY_MAX override: `2` / `PLAT-390`

Mission-state evidence (no goal routing occurred; the CLI kept the mission loop for this Complex task):

- Session state: `.mission-state/sessions/cc-02167a21-722c-4d50-80fe-cc53d6c829f5.json` (mission_id `b8fc16bc2f278ba3`)
- Reviewer JSONs: `.mission-state/review-iter1-accuracy.json`, `.mission-state/review-iter1-completeness.json`
- Aggregated review archive: `.mission-state/archive/iter-1-b8fc16bc-reviews.json`
- Scoring archive: `.mission-state/archive/iter-1-b8fc16bc-scoring.json`
- Reviewer wall-clock windows (self-reported, observational): A=07:32:30Z..07:38:20Z, B=07:32:30Z..07:36:11Z on 2026-08-07 (spawned in one parallel message)
- Unmeasured: token/latency comparison against any other arm — this run makes no benchmark-superiority claim.

---

## Assumptions

- The fixture files are the sole source of truth for this audit. No external system, deployment config, or runtime state was consulted.
- An override is valid if and only if the Note column of the service config contains a `PLAT-<id>` reference. Operational notes in the prose section below the table are informational and do not satisfy the override protocol.
- auth SESSION_TTL_SEC and TLS_MIN_VERSION both have prose justifications (login incident history, legacy SDK) but neither entry in the config table carries a `PLAT-<id>` reference. They are classified as violations per the override protocol definition in `platform-defaults.md`.
- billing IDEMPOTENCY_WINDOW_SEC has an empty Note column and no `PLAT-<id>` anywhere in the file; the ops-notes prose is not an approved override.
- search LOG_RETENTION_DAYS has no Note and the ops note self-reports the absence of approval ("nobody filed the retention change with the platform team").
- notify BATCH_WINDOW_MS has no Note; the ops note provides a business rationale but no approval reference.
