# portfolio-cx-config — mission arm — rep1

Task id: `portfolio-cx-config` / Category: configuration / Arm: mission / Profile: full / Complexity: Complex

## Mission

Audit configuration compliance for four services (auth, billing, search, notify) against the platform canonical defaults (`PLAT-CONFIG v4`). Classify every constant in every service as compliant, an undocumented divergence (violation), or a documented override (rejected as a non-finding with its approval reference). Produce a complete per-service compliance table (9 canonical constants × 4 services = 36 rows), a violations section with quoted evidence, and a rejected-candidates section.

Mission state: `.mission-state/sessions/cc-5a61847e-8508-4834-bf77-48ed10eb7e22.json` (mission_id `cef6bea9dabb1c37`, complexity Complex, implementer role, no goal routing).

## Plan

Planner output (mission-planner, iteration 1) — steps executed in order:

1. Read exactly the 5 named fixtures verbatim (parallel read): `platform/platform-defaults.md`, `auth/config.md`, `billing/config.md`, `search/config.md`, `notify/config.md`. Nothing else under `benchmarks/mission-vs-goal/` was opened.
2. Build the full 36-row compliance table (9 constants × 4 services), each row with Service / Constant / Canonical / Actual / Status.
3. Separate documented overrides: rows whose Note cites an approval reference `PLAT-<id>` per the override protocol.
4. Write the violations section — every undocumented divergence with verbatim fixture quotes.
5. Write the rejected-candidates section — each documented override with its approval reference, rejected as a non-finding.
6. Self-check coverage: 36/36 rows, violations vs. rejected counts reconciled (36 = compliant 27 + violations 6 + documented overrides 3).
7. Run the gated review loop: 2 independent reviewers in parallel (Complex, no irreversible/security signals), `review-finalize`, `closeout`.

## Execution

- All 5 fixtures were read in a single parallel message. The canonical baseline is the 9-constant table in `platform-defaults.md` (PLAT-CONFIG v4), which states: "Every service MUST use these values unless an override is documented in the service config with an approval reference (`PLAT-<id>`)" and "Overrides without an approval reference are treated as violations."
- The full compliance table, violations, and rejected candidates are below (Evidence section). Classification rule applied: divergence + Note citing `PLAT-<id>` → documented override (rejected); divergence without an approval reference → violation, even when a reason is given in prose (e.g. auth `TLS_MIN_VERSION` note "legacy SDK compat" has no PLAT id).

### Per-service compliance table (36/36 rows)

| # | Service | Constant | Canonical | Actual | Status |
|---|---|---|---|---|---|
| 1 | auth | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 2 | auth | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 3 | auth | SESSION_TTL_SEC | 3600 | 7200 | **Violation** (undocumented divergence) |
| 4 | auth | DB_POOL_SIZE | 64 | 64 | Compliant |
| 5 | auth | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 6 | auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **Violation** (reason given, no approval reference) |
| 7 | auth | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 8 | auth | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 9 | auth | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| 10 | billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | Documented override (PLAT-482) — rejected as non-finding |
| 11 | billing | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 12 | billing | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 13 | billing | DB_POOL_SIZE | 64 | 64 | Compliant |
| 14 | billing | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 15 | billing | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 16 | billing | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 17 | billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **Violation** (undocumented divergence) |
| 18 | billing | LOG_RETENTION_DAYS | 30 | 30 | Compliant |
| 19 | search | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 20 | search | REQUEST_RETRY_MAX | 5 | 5 | Compliant |
| 21 | search | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 22 | search | DB_POOL_SIZE | 64 | 128 | **Violation** (undocumented divergence) |
| 23 | search | BATCH_WINDOW_MS | 500 | 500 | Compliant |
| 24 | search | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 25 | search | CACHE_TTL_SEC | 300 | 30 | Documented override (PLAT-511) — rejected as non-finding |
| 26 | search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 27 | search | LOG_RETENTION_DAYS | 30 | 45 | **Violation** (undocumented divergence) |
| 28 | notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | Compliant |
| 29 | notify | REQUEST_RETRY_MAX | 5 | 2 | Documented override (PLAT-390) — rejected as non-finding |
| 30 | notify | SESSION_TTL_SEC | 3600 | 3600 | Compliant |
| 31 | notify | DB_POOL_SIZE | 64 | 64 | Compliant |
| 32 | notify | BATCH_WINDOW_MS | 500 | 250 | **Violation** (undocumented divergence) |
| 33 | notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | Compliant |
| 34 | notify | CACHE_TTL_SEC | 300 | 300 | Compliant |
| 35 | notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | Compliant |
| 36 | notify | LOG_RETENTION_DAYS | 30 | 30 | Compliant |

Coverage check: 36 rows = 27 compliant + 6 violations + 3 documented overrides (rejected).

### Violations (confirmed findings, 6)

Each violation quotes the exact fixture rows. Canonical rows are from `platform/platform-defaults.md`; actual rows are from the named service config.

1. **auth `SESSION_TTL_SEC` — canonical `3600`, actual `7200`.**
   Canonical: `| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |`
   Actual (auth): `| SESSION_TTL_SEC | 7200 | |`
   No approval reference. Operational note admits: "session length was extended during the 2026-04 login incident and the change was kept afterwards."
2. **auth `TLS_MIN_VERSION` — canonical `TLSv1.2`, actual `TLSv1.1`.**
   Canonical: `| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |`
   Actual (auth): `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`
   A reason is given ("legacy SDK compat") but there is no `PLAT-<id>` approval reference, so per the override protocol this is a violation.
3. **billing `IDEMPOTENCY_WINDOW_SEC` — canonical `600`, actual `86400`.**
   Canonical: `| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |`
   Actual (billing): `| IDEMPOTENCY_WINDOW_SEC | 86400 | |`
   No approval reference. Note admits: "the idempotency window was widened while debugging duplicate settlement webhooks in 2026-03."
4. **search `DB_POOL_SIZE` — canonical `64`, actual `128`.**
   Canonical: `| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |`
   Actual (search): `| DB_POOL_SIZE | 128 | |`
   No approval reference. Note admits: "the pool was doubled during a 2026-05 load test and never reverted."
5. **search `LOG_RETENTION_DAYS` — canonical `30`, actual `45`.**
   Canonical: `| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |`
   Actual (search): `| LOG_RETENTION_DAYS | 45 | |`
   No approval reference. Note admits: "Query logs are kept 45 days to debug relevance regressions; nobody filed the retention change with the platform team."
6. **notify `BATCH_WINDOW_MS` — canonical `500`, actual `250`.**
   Canonical: `| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |`
   Actual (notify): `| BATCH_WINDOW_MS | 250 | |`
   No approval reference on this row (PLAT-390 in the same file belongs to `REQUEST_RETRY_MAX` only). Note admits: "the batch window was halved to reduce push latency during the 2026-06 campaign."

### Rejected candidates (documented overrides, 3 — non-findings)

1. **billing `CONNECT_TIMEOUT_MS` = 12000 (canonical 4000) — rejected, approval reference PLAT-482.**
   Quoted row: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |`
2. **search `CACHE_TTL_SEC` = 30 (canonical 300) — rejected, approval reference PLAT-511.**
   Quoted row: `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |`
3. **notify `REQUEST_RETRY_MAX` = 2 (canonical 5) — rejected, approval reference PLAT-390.**
   Quoted row: `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |`

## Review

Gated review loop, iteration 1: 2 independent reviewers (Complex, no irreversibility/security escalation signals) spawned in parallel in a single message, each returning `mission-review/1` JSON.

- Reviewer A (perspective `correctness-and-coverage`): verified all 36 rows against the fixtures; 0 findings; axis scores 5.0/5.0/5.0/5.0.
- Reviewer B (perspective `evidence-and-protocol`): verified all quoted strings verbatim against the fixtures; 1 Low finding (the Evidence section's override-protocol citation omits the fixture's leading `Override protocol:` label — substance exact); axis scores 5.0/4.8/5.0/4.9 (accuracy capped to 4.7 by the 1-Low finding cap during aggregation).
- Aggregation and scoring via `mission-state.py review-finalize --min-reviewers 2` (aggregate-reviews → push-score, with `--reviewer-window` for both reviewers), then `closeout` (mark-passes → next).

## Score

- Iteration: 1 (threshold 4.0, `--max-iter 3`)
- Composite score: **4.95** / min item 4.85 (`computed_composite` / `computed_min_item` from `.mission-state/archive/iter-1-cef6bea9-scoring.json`, timestamp 2026-08-07T07:24:23Z)
- Aggregated items: mission_achievement 5.0 / accuracy 4.85 / completeness 5.0 / usability 4.95
- Agreement: max per-axis delta 0.3 (accuracy min 4.7 / max 5.0) ≤ 1.5; review_agreement 5.0
- open_high: 0; findings evidence at `.mission-state/archive/iter-1-cef6bea9-reviews.json`
- Gates: composite 4.95 ≥ 4.0 ✔ / min item 4.85 ≥ 3.5 ✔ / agreement delta 0.3 ≤ 1.5 ✔ / open_high == 0 ✔ / findings_evidence_path exists ✔
- `closeout` returned `ok: true`, `mark_passes.passes: true` (not forced), `next_action: report-complete`, `loop_active: false`

## Stop Decision

Stopped after iteration 1 (early-stop rule: threshold reached and `open_high == 0` at iteration 1 → pass; composite 4.95 is above the 4.0–4.3 continue band, so no extra iteration is warranted). `closeout` returned `passes=true` / `loop_active=false` / `next_action=report-complete`, terminating normally within `--max-iter 3`. No halt reason. The single Low finding (missing `Override protocol:` label in one citation) does not affect the audit classifications and is left as-is.

## Evidence

- Fixtures read (exactly the 5 named; nothing else under `benchmarks/mission-vs-goal/` was opened):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
- Override protocol quoted from `platform-defaults.md`: "the service config must state the constant, the overridden value, the reason, and the approval reference. Overrides without an approval reference are treated as violations."
- All 36 classification rows quote canonical and actual values verbatim from the fixture tables (see Execution).
- Mission state evidence: session `cc-5a61847e-8508-4834-bf77-48ed10eb7e22`, mission_id `cef6bea9dabb1c37`; reviewer `mission-review/1` JSON at `.mission-state/review-iter1-a.json` / `.mission-state/review-iter1-b.json`; aggregated reviews at `.mission-state/archive/iter-1-cef6bea9-reviews.json`; scoring JSON at `.mission-state/archive/iter-1-cef6bea9-scoring.json`; assumptions at `.mission-state/sessions/cc-5a61847e-8508-4834-bf77-48ed10eb7e22-assumptions.md`.
- Specialists: selected: none / used: none / degraded: none / unselected-manual: none (`specialists_decision.policy: fallback`, reason: top preset specialist `documentation-provider` not installed → continue-core).
- Efficiency metrics (wall-clock time, tokens) for this run are unmeasured.

## Assumptions

- `PLAT-390` attaches only to notify `REQUEST_RETRY_MAX`; `BATCH_WINDOW_MS=250` in the same file has no approval reference and is therefore a violation.
- A prose reason without a `PLAT-<id>` approval reference does not constitute a documented override (per the quoted override protocol) — this is why auth `TLS_MIN_VERSION` ("legacy SDK compat") is a violation.
- `MISSION_PLUGIN_ROOT` points at a local authoring checkout, but the benchmark forbids network access, so `mission-local-authoring-sync.sh` was skipped and the repo-root `scripts/mission-state.py` was used as the state CLI.
- Benchmark metadata (task definitions, scoring configuration, answer keys) was treated as out of bounds and never opened.
