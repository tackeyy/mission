# portfolio-cx-config — mission arm, rep1 (2026-08-07)

Task id: `portfolio-cx-config` / Arm: mission / Profile: full / Complexity: Complex

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). For every constant in every service, classify as compliant, undocumented divergence (violation), or documented override (rejected as non-finding with approval reference). Sources read (verbatim, complete):

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

## Plan

Planner ran at iteration 1 (mission-planner, forked). Steps: (1) create run-output directory; (2) re-read all 5 fixtures verbatim; (3) build the full 9 constants × 4 services = 36-cell compliance table; (4) violations section with verbatim quotes; (5) rejected-candidates section with approval references; (6) 2 independent reviewers re-derive all 36 cells; (7) score via `review-finalize`; (8) `closeout` gate before completion. Risks tracked: transcription errors (mitigated by verbatim re-read), PLAT-390 scope misattribution (pinned to REQUEST_RETRY_MAX only), directory-missing write failure (mkdir -p first).

## Execution

Mission state: `.mission-state/sessions/cc-f8d683a4-51ef-4e9b-b01c-9f64dff607db.json` (mission_id `1674a4eda8e4a78a`, permission preflight passed). All 5 fixtures were read in full; no other file under `benchmarks/mission-vs-goal/` was opened. Executor ran inline in the orchestrator context (recorded in assumptions; codex-inline equivalent) due to session budget constraints.

### Full per-service compliance table (9 canonical constants × 4 services = 36 rows)

Canonical values from `platform-defaults.md`: CONNECT_TIMEOUT_MS=4000, REQUEST_RETRY_MAX=5, SESSION_TTL_SEC=3600, DB_POOL_SIZE=64, BATCH_WINDOW_MS=500, TLS_MIN_VERSION=TLSv1.2, CACHE_TTL_SEC=300, IDEMPOTENCY_WINDOW_SEC=600, LOG_RETENTION_DAYS=30.

| # | Service | Constant | Canonical | Actual | Status |
|---|---|---|---|---|---|
| 1 | auth | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 2 | auth | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 3 | auth | SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented) |
| 4 | auth | DB_POOL_SIZE | 64 | 64 | Compliant |
| 5 | auth | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 6 | auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (undocumented) |
| 7 | auth | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 8 | auth | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 9 | auth | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| 10 | billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected |
| 11 | billing | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 12 | billing | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 13 | billing | DB_POOL_SIZE | 64 | 64 | Compliant |
| 14 | billing | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 15 | billing | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 16 | billing | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 17 | billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented) |
| 18 | billing | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| 19 | search | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 20 | search | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 21 | search | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 22 | search | DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented) |
| 23 | search | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 24 | search | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 25 | search | CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected |
| 26 | search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 27 | search | LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented) |
| 28 | notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 29 | notify | REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected |
| 30 | notify | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 31 | notify | DB_POOL_SIZE | 64 | 64 | Compliant |
| 32 | notify | BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented) |
| 33 | notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 34 | notify | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 35 | notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 36 | notify | LOG_RETENTION_DAYS | 30 | 30 | Compliant |

Self-check: 36/36 rows present (9 constants × 4 services). Totals: 27 compliant, 6 violations, 3 documented overrides (rejected).

### Violations (confirmed findings — undocumented divergences, no approval reference)

1. **auth / SESSION_TTL_SEC** — canonical `3600`, actual `7200`. Fixture row: `| SESSION_TTL_SEC | 7200 | |` (empty note, no `PLAT-<id>`). Operational note confirms it is unapproved drift: "session length was extended during the 2026-04 login incident and the change was kept afterwards."
2. **auth / TLS_MIN_VERSION** — canonical `TLSv1.2`, actual `TLSv1.1`. Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |` — a reason but no approval reference, which the platform doc treats as a violation ("Overrides without an approval reference are treated as violations"). Canonical rationale: "Security baseline; TLSv1.1 is end-of-life".
3. **billing / IDEMPOTENCY_WINDOW_SEC** — canonical `600`, actual `86400`. Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (no approval reference). Note: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03" — no PLAT id cited for this change.
4. **search / DB_POOL_SIZE** — canonical `64`, actual `128`. Fixture row: `| DB_POOL_SIZE | 128 | |`. Note: "the pool was doubled during a 2026-05 load test and never reverted."
5. **search / LOG_RETENTION_DAYS** — canonical `30`, actual `45`. Fixture row: `| LOG_RETENTION_DAYS | 45 | |`. Note: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team." Canonical rationale: "Data-minimization policy DM-9".
6. **notify / BATCH_WINDOW_MS** — canonical `500`, actual `250`. Fixture row: `| BATCH_WINDOW_MS | 250 | |`. Note: "the batch window was halved to reduce push latency during the 2026-06 campaign" — no approval reference (PLAT-390 attaches only to REQUEST_RETRY_MAX).

### Rejected candidates (documented overrides — non-findings, approval reference cited)

1. **billing / CONNECT_TIMEOUT_MS = 12000** (canonical 4000). Fixture note: "Override: PSP provider p99 latency is 9s; approved PLAT-482". Follows the override protocol → rejected as non-finding.
2. **search / CACHE_TTL_SEC = 30** (canonical 300). Fixture note: "Override: suggestion freshness SLA requires 30s; approved PLAT-511". Follows the override protocol → rejected as non-finding.
3. **notify / REQUEST_RETRY_MAX = 2** (canonical 5). Fixture note: "Override: at-most-once delivery guarantee; approved PLAT-390". Follows the override protocol → rejected as non-finding.

## Review

Two independent reviewers were spawned in parallel in a single message (perspectives: correctness/completeness and evidence-fidelity/false-positive hunting). Each re-derived the 36-cell classification from the same 5 fixtures and returned `mission-review/1` JSON. Reviewer JSONs are stored under `.mission-state/` (`review-iter1-a.json`, `review-iter1-b.json`) and were aggregated by `mission-state.py review-finalize --iteration 1 --min-reviewers 2` with a recorded `--reviewer-window` per perspective. Scores and any findings are recorded in the Score section below from the tool-computed aggregation output (not hand-computed).

## Score

Filled from `review-finalize` output and post-write state re-read (see Evidence): composite score, per-item minimum, `max_agreement_delta`, and `open_high` are tool-computed by `mission-state.py`, not manually derived. Gate requirements: composite ≥ 4.0 (threshold), min item ≥ 3.5, `open_high == 0`, `max_agreement_delta ≤ 1.5`, findings evidence path present. Result recorded in Stop Decision.

- Iteration 1 composite: 4.62 (reviewer A 4.65, reviewer B 4.6; agreement delta ≤ 1.5 satisfied)
- Minimum scored item: 4.4 (≥ 3.5)
- open_high: 0
- Findings evidence: `.mission-state/findings-iter1.md`

## Stop Decision

`closeout` (mark-passes → next) returned exit 0 with `passes=true` and `next_action=report-complete` at iteration 1. Early-stop criteria met: threshold reached at iteration 1 with `open_high == 0`. `--max-iter 3` not exhausted; no stagnation. Loop stopped because all machine-verified gates passed, not by self-assessment.

## Evidence

- Mission state session: `.mission-state/sessions/cc-f8d683a4-51ef-4e9b-b01c-9f64dff607db.json` (mission_id `1674a4eda8e4a78a`), `init` output: `{"ok": true, "permission_preflight": "passed"}`.
- Fixture reads: 5/5 named fixtures read in full; every canonical constant and every service value quoted in the table above is a verbatim transcription (e.g. canonical `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |` vs auth `| SESSION_TTL_SEC | 7200 | |`).
- Approval references verified verbatim: `PLAT-482` (billing), `PLAT-511` (search), `PLAT-390` (notify). No other `PLAT-<id>` appears in any service fixture, so no other divergence can be a documented override.
- Coverage: 36/36 constant×service cells classified; missing-row audit failure condition avoided by explicit row numbering (1–36).
- Scored review iteration 1 completed via `review-finalize` (aggregate-reviews + push-score) and `closeout` (mark-passes + next); gate values in Score section are from tool output.
- Unmeasured: wall-clock duration and token counts for this run are unmeasured here; benchmark-level comparisons are out of scope for this artifact (no superiority claim).

## Assumptions

1. PLAT-390 (notify) applies only to the `REQUEST_RETRY_MAX` row where it is cited; `BATCH_WINDOW_MS=250` has no approval reference and is therefore a violation.
2. A stated reason without an approval reference (auth `TLS_MIN_VERSION` "legacy SDK compat") does not satisfy the override protocol — per platform-defaults.md: "Overrides without an approval reference are treated as violations."
3. Fixture scope was limited to the 5 named files; benchmark metadata and answer keys were not opened (out of bounds per run rules).
4. Executor ran inline in the orchestrator context (budget constraint), recorded in `.mission-state` assumptions; reviewers were independent parallel subagents.
5. No commit/push/network was performed; edits were limited to this artifact and `.mission-state/`.
