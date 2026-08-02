# Portfolio CX Config — Mission Benchmark Artifact

## Mission

Audit configuration compliance for four services (`auth`, `billing`, `search`,
`notify`) against the platform canonical defaults (`PLAT-CONFIG v4`). For
every constant in every service, determine whether it is:

- **compliant** — actual value matches the canonical value,
- **undocumented divergence (violation)** — actual value differs from
  canonical and no approval reference is cited, or
- **documented override (non-finding)** — actual value differs from canonical
  but the service config cites an approval reference (`PLAT-<id>`) per the
  override protocol; these are rejected as non-findings.

Source fixtures (read exactly these, no others under
`benchmarks/mission-vs-goal/`):

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`

## Plan

Mission state: `.mission-state/sessions/cc-ede38845-49cc-4e37-a89e-eb384b8c6004.json`,
mission_id `5308c7aa9eeba4d8`, complexity `Complex`.

1. `mission-planner` (Skill, forked execution) reads all five fixtures and
   produces the full 4×9 compliance matrix plus a 3-step execution plan
   (create output dir → write artifact → update mission state).
2. Orchestrator independently re-reads all five fixtures directly (not
   delegated) to verify every planner-reported value and status before
   trusting it (Maker-Checker discipline for a config-audit task where
   transcription errors would silently corrupt the finding set).
3. Orchestrator (acting as executor, since verification already produced the
   authoritative table) writes this artifact.
4. Two independent reviewers (`mission-reviewer`, parallel) score the
   artifact against the task validator.
5. `review-finalize` (aggregate-reviews → push-score) records the scored
   iteration; `closeout` (mark-passes → next) gates completion.

## Execution

- Planner spawn: read `platform-defaults.md`, `auth/config.md`,
  `billing/config.md`, `search/config.md`, `notify/config.md`; returned a
  36-cell (4 services × 9 constants) compliance table with 6 undocumented
  divergences and 3 documented overrides.
- Orchestrator verification: re-read all five fixture files directly, line by
  line, and cross-checked every cell against the planner's output. Result:
  **0 discrepancies** — every value, status, and approval reference in the
  planner's table matched the fixture text exactly.
- Artifact written to
  `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-cx-config-mission.md`.
- No commits, pushes, package installs, or network calls were made.

## Review

### Platform canonical defaults (PLAT-CONFIG v4)

Quoted from `platform-defaults.md`:

| Constant | Canonical value |
|---|---|
| `CONNECT_TIMEOUT_MS` | `4000` |
| `REQUEST_RETRY_MAX` | `5` |
| `SESSION_TTL_SEC` | `3600` |
| `DB_POOL_SIZE` | `64` |
| `BATCH_WINDOW_MS` | `500` |
| `TLS_MIN_VERSION` | `TLSv1.2` |
| `CACHE_TTL_SEC` | `300` |
| `IDEMPOTENCY_WINDOW_SEC` | `600` |
| `LOG_RETENTION_DAYS` | `30` |

Override protocol (quoted): "the service config must state the constant, the
overridden value, the reason, and the approval reference. Overrides without an
approval reference are treated as violations."

### Full per-service compliance table (36 cells = 4 services × 9 constants)

| Service | Constant | Canonical | Actual | Status | Approval ref |
|---|---|---|---|---|---|
| auth | CONNECT_TIMEOUT_MS | 4000 | 4000 | compliant | — |
| auth | REQUEST_RETRY_MAX | 5 | 5 | compliant | — |
| auth | SESSION_TTL_SEC | 3600 | 7200 | **undocumented divergence** | none |
| auth | DB_POOL_SIZE | 64 | 64 | compliant | — |
| auth | BATCH_WINDOW_MS | 500 | 500 | compliant | — |
| auth | TLS_MIN_VERSION | TLSv1.2 | TLSv1.1 | **undocumented divergence** | none |
| auth | CACHE_TTL_SEC | 300 | 300 | compliant | — |
| auth | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | compliant | — |
| auth | LOG_RETENTION_DAYS | 30 | 30 | compliant | — |
| billing | CONNECT_TIMEOUT_MS | 4000 | 12000 | documented override | PLAT-482 |
| billing | REQUEST_RETRY_MAX | 5 | 5 | compliant | — |
| billing | SESSION_TTL_SEC | 3600 | 3600 | compliant | — |
| billing | DB_POOL_SIZE | 64 | 64 | compliant | — |
| billing | BATCH_WINDOW_MS | 500 | 500 | compliant | — |
| billing | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | compliant | — |
| billing | CACHE_TTL_SEC | 300 | 300 | compliant | — |
| billing | IDEMPOTENCY_WINDOW_SEC | 600 | 86400 | **undocumented divergence** | none |
| billing | LOG_RETENTION_DAYS | 30 | 30 | compliant | — |
| search | CONNECT_TIMEOUT_MS | 4000 | 4000 | compliant | — |
| search | REQUEST_RETRY_MAX | 5 | 5 | compliant | — |
| search | SESSION_TTL_SEC | 3600 | 3600 | compliant | — |
| search | DB_POOL_SIZE | 64 | 128 | **undocumented divergence** | none |
| search | BATCH_WINDOW_MS | 500 | 500 | compliant | — |
| search | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | compliant | — |
| search | CACHE_TTL_SEC | 300 | 30 | documented override | PLAT-511 |
| search | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | compliant | — |
| search | LOG_RETENTION_DAYS | 30 | 45 | **undocumented divergence** | none |
| notify | CONNECT_TIMEOUT_MS | 4000 | 4000 | compliant | — |
| notify | REQUEST_RETRY_MAX | 5 | 2 | documented override | PLAT-390 |
| notify | SESSION_TTL_SEC | 3600 | 3600 | compliant | — |
| notify | DB_POOL_SIZE | 64 | 64 | compliant | — |
| notify | BATCH_WINDOW_MS | 500 | 250 | **undocumented divergence** | none |
| notify | TLS_MIN_VERSION | TLSv1.2 | TLSv1.2 | compliant | — |
| notify | CACHE_TTL_SEC | 300 | 300 | compliant | — |
| notify | IDEMPOTENCY_WINDOW_SEC | 600 | 600 | compliant | — |
| notify | LOG_RETENTION_DAYS | 30 | 30 | compliant | — |

Tally: 27 compliant, 6 undocumented divergences, 3 documented overrides = 36 cells (matches 4×9).

### Violations (undocumented divergences) — 6 confirmed findings

Each row quotes the exact constant name, canonical value, and actual value.

1. **auth / `SESSION_TTL_SEC`** — canonical `3600`, actual `7200`. Fixture row:
   `| SESSION_TTL_SEC | 7200 | |` (empty Note column, no approval reference).
   Operational notes state the value "was extended during the 2026-04 login
   incident and the change was kept afterwards" — no `PLAT-<id>` reference is
   given anywhere in the file, so per the override protocol this is treated
   as a violation.
2. **auth / `TLS_MIN_VERSION`** — canonical `TLSv1.2`, actual `TLSv1.1`.
   Fixture row: `| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |`. "legacy
   SDK compat" is a reason, not an approval reference (no `PLAT-<id>` cited
   anywhere in the file) — undocumented divergence.
3. **billing / `IDEMPOTENCY_WINDOW_SEC`** — canonical `600`, actual `86400`.
   Fixture row: `| IDEMPOTENCY_WINDOW_SEC | 86400 | |` (empty Note column).
   Operational notes: "the idempotency window was widened while debugging
   duplicate settlement webhooks in 2026-03" — no approval reference —
   undocumented divergence.
4. **search / `DB_POOL_SIZE`** — canonical `64`, actual `128`. Fixture row:
   `| DB_POOL_SIZE | 128 | |` (empty Note column). Operational notes: "the
   pool was doubled during a 2026-05 load test and never reverted" — no
   approval reference — undocumented divergence.
5. **search / `LOG_RETENTION_DAYS`** — canonical `30`, actual `45`. Fixture
   row: `| LOG_RETENTION_DAYS | 45 | |`. Operational notes explicitly state
   "nobody filed the retention change with the platform team" — undocumented
   divergence.
6. **notify / `BATCH_WINDOW_MS`** — canonical `500`, actual `250`. Fixture
   row: `| BATCH_WINDOW_MS | 250 | |` (empty Note column). Operational notes:
   "the batch window was halved to reduce push latency during the 2026-06
   campaign" — no approval reference — undocumented divergence.

### Rejected candidates (documented overrides) — 3, non-findings

Each row cites the exact approval reference from the fixture, so these are
**rejected** as compliance findings.

1. **billing / `CONNECT_TIMEOUT_MS`** — canonical `4000`, actual `12000`.
   Fixture row: `| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99
   latency is 9s; approved PLAT-482 |`. Operational notes confirm: "The
   connect timeout override follows the platform override protocol with
   approval reference PLAT-482." Rejected — documented override, approval
   ref `PLAT-482`.
2. **search / `CACHE_TTL_SEC`** — canonical `300`, actual `30`. Fixture row:
   `| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s;
   approved PLAT-511 |`. Operational notes confirm: "The cache TTL override
   follows the override protocol with approval reference PLAT-511." Rejected
   — documented override, approval ref `PLAT-511`.
3. **notify / `REQUEST_RETRY_MAX`** — canonical `5`, actual `2`. Fixture row:
   `| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee;
   approved PLAT-390 |`. Operational notes confirm: "The retry override
   follows the override protocol with approval reference PLAT-390." Rejected
   — documented override, approval ref `PLAT-390`.

### Reviewer scoring (peer review, iteration 1)

Two independent `mission-reviewer` passes were run in parallel against this
artifact and the task validator. Raw review outputs and the aggregated
`mission-review/1` JSON are archived under `.mission-state/` (see Evidence).
Aggregated result: **composite score 5.0/5.0**, 0 open High-severity
findings, reviewer agreement delta 0.0 (both reviewers scored 5/5 on
completeness, evidence-quoting, and non-finding separation).

## Score

| Metric | Value |
|---|---|
| Constants audited per service | 9 |
| Services audited | 4 (auth, billing, search, notify) |
| Total cells covered | 36 / 36 |
| Compliant | 27 |
| Undocumented divergences (violations) | 6 |
| Documented overrides (rejected non-findings) | 3 |
| Missing rows | 0 |
| Orchestrator independent re-verification | 5/5 fixtures re-read, 0 discrepancies vs. planner output |
| Composite review score (iteration 1) | 5.0 / 5.0 (threshold 4.0) |
| Open High-severity findings | 0 |
| Max reviewer agreement delta | 0.0 (gate: ≤ 1.5) |

## Stop Decision

`passes: true`. All gate conditions met:

- `findings_evidence_path` recorded (aggregated review JSON archived under
  `.mission-state/`).
- `open_high == 0`.
- `max_agreement_delta` 0.0 ≤ 1.5.
- `composite_score` 5.0 ≥ threshold 4.0.
- `min(scored_items)` ≥ 3.5 (all items scored 5.0).
- Full per-service compliance table (36/36 cells), violations section (6,
  quoted), and rejected-candidates section (3, quoted with approval refs) are
  all present, satisfying the task validator.

Iteration 1 of `--max-iter 3` was sufficient; no further iterations required.
`mark-passes` → `closeout` completed the mission loop.

## Evidence

- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/platform/platform-defaults.md`
  — canonical constants and override protocol (quoted above).
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/auth/config.md`
  — auth service actuals (quoted above).
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/billing/config.md`
  — billing service actuals (quoted above).
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/search/config.md`
  — search service actuals (quoted above).
- `benchmarks/mission-vs-goal/fixtures/discriminating/config-sprawl/notify/config.md`
  — notify service actuals (quoted above).
- Mission state: `.mission-state/sessions/cc-ede38845-49cc-4e37-a89e-eb384b8c6004.json`
  (mission_id `5308c7aa9eeba4d8`), archived reviewer JSON and scoring JSON
  under `.mission-state/` per the standard review-finalize flow.
- No other files under `benchmarks/mission-vs-goal/` were opened, read,
  grepped, or listed, per task rules.

## Assumptions

1. **Local authoring sync skipped.** `bash "$MISSION_PLUGIN_ROOT/scripts/mission-local-authoring-sync.sh"`
   returned `error: local Mission source must be clean before syncing
   origin/main` (exit 1). Fixing that would require modifying
   `~/dev/mission`, which is outside this benchmark's allowed edit scope
   (benchmark output files and `.mission-state/` only) and network access is
   disallowed for this run regardless. Proceeded using the already-loaded
   `SKILL.md` instructions and the repo-local `scripts/mission-state.py`
   without resyncing. This is a deviation from the normal bootstrap step, not
   from the mission loop itself (state gating, planner/reviewer/score/closeout
   all ran normally).
2. **Override-validity standard.** A divergence counts as a "documented
   override" only if the service config's own table row or operational notes
   cite an explicit `PLAT-<id>` approval reference, per the override protocol
   quoted in `platform-defaults.md` ("Overrides without an approval reference
   are treated as violations"). Explanatory notes without a `PLAT-<id>` (e.g.
   auth's "legacy SDK compat", billing's/search's/notify's plain operational
   commentary on the 6 violation rows) do not qualify as documented overrides,
   even where a business reason is stated.
3. **No un-measured claims.** Every value, status, and reference in this
   artifact was directly read from the five named fixtures (both by the
   planner sub-agent and independently by the orchestrator) — nothing here is
   inferred or estimated.
4. **Complexity classification.** The task prompt specified `Complex`, so a
   full 2-reviewer parallel pass was used (not the Standard inline-planner
   path or Simple single-reviewer path).
