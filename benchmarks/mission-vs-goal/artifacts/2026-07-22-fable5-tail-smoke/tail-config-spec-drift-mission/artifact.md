# tail-config-spec-drift — mission arm artifact

## Mission

Audit configuration drift against the canonical spec (`spec.md`) for the gateway
service, comparing exactly three documents: `impl-alpha.md`, `impl-beta.md`, and
`runbook.md`. Report every contradiction with quoted key and both values, and
explicitly reject look-alike contradictions that are consistent after unit or
aggregate conversion. Task id: `tail-config-spec-drift`, arm: `mission`,
profile: `full`, complexity: Complex, `--max-iter 3`, threshold 4.0.

Mission state: `.mission-state/sessions/cc-807eff78-f5d1-4356-9322-4677136bda00.json`
(mission_id `c7abeb64f42951d0`, `permission_preflight: passed`, `review_tier: full`).

## Plan

1. Initialize auditable mission state (`mission-state.py init`, Complex) and record
   assumptions in the state's `assumptions_path`. — done before analysis.
2. Read exactly the four named fixtures; nothing else under `benchmarks/mission-vs-goal/`.
3. Build a key-by-key comparison matrix (spec ↔ alpha ↔ beta ↔ runbook), normalizing
   units (ms/s, ticks/s, per-replica/aggregate) before judging drift.
4. Classify each mismatch as confirmed drift (no valid conversion) or rejected
   candidate (conversion shown), and write this artifact.
5. Review gate: 3 independent reviewers (Complex ⇒ 3) produce `mission-review/1`
   JSON; aggregate via `mission-state.py aggregate-reviews` → `push-score` →
   `mark-passes` only if composite ≥ 4.0, `open_high == 0`, agreement ≤ 1.5.
6. Stop when `next` returns `report-complete`, or halt with reason.

## Execution

All four fixtures were read in full. Comparison method: map each spec key to its
counterpart(s) by name normalization (`request_timeout_ms` ≡ `requestTimeoutMs` ≡
`REQUEST_TIMEOUT_MS`, `queue_max_depth` ≡ `MAX_QUEUE_DEPTH`, etc.), convert units
where the fixture itself documents a conversion basis, then compare values.

### Confirmed drift table

| # | File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|---|
| 1 | `impl-alpha.md` | `request_timeout_ms` | `3000` | `27000` | spec: "`request_timeout_ms` \| 3000"; alpha: `requestTimeoutMs   = 27000`. Both are milliseconds — no unit conversion maps 27000 ms to 3000 ms (27 s vs 3 s). |
| 2 | `impl-alpha.md` | `queue_max_depth` | `10000` | `1250` | spec: "`queue_max_depth` \| 10000 \| Requests beyond depth are shed."; alpha: `MAX_QUEUE_DEPTH    = 1250`. The alpha fixture documents no per-worker/shard factor, so no aggregate conversion is available (see rejected-candidate analysis R5). |
| 3 | `impl-alpha.md` | `enable_legacy_auth` | `false` | `true` | spec: "`enable_legacy_auth` \| false \| Must stay false; scheduled for removal."; alpha: `enableLegacyAuth   = true`, with the note "The legacy auth flag was toggled during the March incident bridge and has not been revisited since." Boolean contradiction; no conversion possible. |
| 4 | `impl-beta.md` | `retry_backoff` (strategy) | `exponential, base 250ms` | `constant-interval` | spec: "`retry_backoff` \| exponential, base 250ms"; beta: `RETRY_BACKOFF_STRATEGY=constant-interval`. The fixture confirms these are distinct enum members of the retry library ("`constant-interval`, `exponential`, `decorrelated-jitter`"), so this is a strategy mismatch, not a naming variant. |
| 5 | `impl-beta.md` | `health_check_interval_s` | `15` | `75` | spec: "`health_check_interval_s` \| 15 \| Liveness probe cadence."; beta: `HEALTH_CHECK_INTERVAL_SECONDS=75`. Both keys are denominated in seconds by name; 75 s ≠ 15 s and no conversion basis exists (the tick basis applies only to `IDLE_TIMEOUT_TICKS`). |
| 6 | `runbook.md` | `max_retries` | `3` | `6` | spec: "`max_retries` \| 3 \| Applies to idempotent requests only."; runbook: "the gateway will retry idempotent requests up to 6 times before shedding." Same semantics (retries of idempotent requests), same unit (count); 6 ≠ 3. Even a retries-vs-attempts reading gives 3 retries = 4 attempts, not 6. |
| 7 | `runbook.md` | `tls_min_version` | `1.3` (hard floor) | `1.2` (during cert rotation) | spec: "`tls_min_version` \| 1.3 \| Hard floor for all listeners."; runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window." A "hard floor" admits no temporary lowering; the runbook instructs a procedure that violates it. |

### Rejected candidates (look contradictory, but consistent)

| # | File / key | Why it looked suspicious | Conversion / reasoning that clears it |
|---|---|---|---|
| R1 | `impl-beta.md` `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s = 90` | 5400 vs 90 differ by 60× and the key names use different units. | Beta documents its own basis: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." 5400 ticks ÷ 60 ticks/s = **90 s**, exactly the spec value. Consistent after unit conversion. |
| R2 | `runbook.md` "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica = 32` | 64 ≠ 32 at face value. | The runbook figure is an aggregate; the spec notes "Two replicas run in production." 32 per replica × 2 replicas = **64 total**. Consistent after aggregate conversion. |
| R3 | `impl-alpha.md` splits backoff across two keys: `retryBackoff = exponential` + `retryBackoffBaseMs = 250` vs spec `retry_backoff = exponential, base 250ms` | Spec expresses one compound value; alpha expresses two keys, so a naive key-by-key diff flags a mismatch. | Recomposing alpha's two keys yields "exponential, base 250 ms", identical to the spec (strategy `exponential`, base 250 ms). Consistent after representation normalization; no value differs. |
| R4 | `runbook.md` "Run all services at INFO verbosity … DEBUG is allowed only on a single canary replica for up to one hour" vs spec `log_level = info` | "INFO" vs "info" case differs, and the DEBUG allowance reads like a contradiction of the production value. | Case is a formatting variant of the same level (INFO ≡ info). The spec note defines `info` as the "Production default"; a bounded single-canary DEBUG exception does not change the default and contradicts no spec value. Both beta (`LOG_LEVEL=info`) and alpha (`logLevel = info`) also match. |
| R5 | `impl-alpha.md` `MAX_QUEUE_DEPTH = 1250` — could it be per-worker with 8 workers (1250 × 8 = 10000)? | The arithmetic coincidence 1250 × 8 = 10000 invites an aggregate-conversion rescue like R2. | Rejected as a rescue and kept as **confirmed drift #2**: unlike R1/R2, no fixture documents any worker/shard count or per-worker semantics for alpha's queue depth (alpha's notes mention only boot-time reads and the legacy-auth toggle). A conversion basis must come from the fixtures, not from numerology; absent one, 1250 contradicts 10000. |
| R6 | `impl-alpha.md` has no `health_check_interval_s` / `idle_timeout_s` lines | Missing keys can look like drift-by-omission. | Alpha is labeled an "excerpt from deployed config"; absence from an excerpt is not evidence of a contradicting value. Runbook's health section defers to the spec ("Liveness probes are configured centrally; see the spec for cadence."), which agrees with, not contradicts, the spec. Status: unmeasured, not drift. |

### Spec constraints violated (explicit statement)

The following canonical spec constraints are violated:

1. `request_timeout_ms = 3000` — violated by impl-alpha (`27000`).
2. `queue_max_depth = 10000` — violated by impl-alpha (`1250`).
3. `enable_legacy_auth = false` ("Must stay false") — violated by impl-alpha (`true`).
4. `retry_backoff = exponential, base 250ms` — violated by impl-beta (strategy `constant-interval`; the 250 ms base itself matches).
5. `health_check_interval_s = 15` — violated by impl-beta (`75`).
6. `max_retries = 3` — violated by the runbook's retry guidance (`up to 6 times`).
7. `tls_min_version = 1.3` ("Hard floor for all listeners") — violated by the runbook's rotation procedure (temporary floor `1.2`).

Not violated by any audited document: `queue_max_depth` (beta: `10000` ✓),
`tls_min_version` in both impls (`1.3` ✓), `log_level = info` (all three ✓),
`db_pool_size_per_replica = 32` (alpha ✓, beta ✓, runbook consistent as aggregate),
`idle_timeout_s = 90` (beta ✓ after tick conversion; alpha unmeasured),
`max_retries = 3` in both impls (`3` ✓), `request_timeout_ms` (beta ✓).

## Review

Review gate per mission full profile (Complex ⇒ 3 reviewers). Three independent
reviewer subagents were spawned in parallel with distinct perspectives:
A = accuracy/evidence fidelity (quotes match fixtures verbatim), B = completeness
against the task validator (all spec keys swept, all traps classified), C =
usability/auditability of the artifact. Each returned `mission-review/1` JSON;
the JSONs were saved verbatim and aggregated with
`mission-state.py aggregate-reviews --iteration 1` (strict schema validation),
then pushed via `push-score --scoring-json`. Reviewer findings and dispositions
are listed in Evidence below.

Result: composite 4.55 (A/B/C all 4.4–4.7), max agreement delta 0.30,
High findings: 0, Medium: 0, Low: 3 (wording/format suggestions; non-blocking).
No inline fixes were required after review, so no re-verification pass (M6) was
triggered.

## Score

- Iteration: 1 / max 3. Threshold: 4.0 (composite), min item gate 3.5.
- Composite score: **4.55** (from `aggregate-reviews` → `push-score`; see
  `score_history` in the session state).
- Axis minima across reviewers: mission_achievement 4.4, accuracy 4.5,
  completeness 4.4, usability 4.3 — all ≥ 3.5.
- `open_high = 0`, `max_agreement_delta = 0.30 ≤ 1.5`, findings evidence recorded
  at the state's `findings_evidence_path`.

## Stop Decision

Early-stop at iteration 1: composite 4.55 ≥ threshold 4.0 with `open_high == 0`
and all per-item scores ≥ 3.5, so the pass gate is satisfied on the first
iteration (`mark-passes` exit 0, `next` → `report-complete`). No further
iterations were run; `--max-iter 3` was not exhausted. No PR exists, so Phase 7
(merge) is not applicable. No commit/push/network operations were performed, per
benchmark rules.

## Evidence

- Fixtures read (the only files opened under `benchmarks/mission-vs-goal/`):
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`
  - `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`
- Every confirmed finding quotes the exact key and both values inline in the
  confirmed-drift table above; every rejected candidate shows its clearing
  conversion (R1: 5400 ÷ 60 = 90; R2: 32 × 2 = 64; R3 recomposition; R4 case/
  default semantics; R5 no documented conversion basis ⇒ stays confirmed; R6
  excerpt-omission ⇒ unmeasured).
- Mission state artifacts: session file
  `.mission-state/sessions/cc-807eff78-f5d1-4356-9322-4677136bda00.json`
  (`init` exit 0, `permission_preflight: passed`), assumptions at the state's
  `assumptions_path`, reviewer JSONs under `.mission-state/` evidence paths,
  aggregation output `/…/mission-scorer-1.json` passed to `push-score`.
- Unmeasured items (stated as such): alpha's `health_check_interval_s` and
  `idle_timeout_s` (absent from the excerpt); runtime behavior of any service
  (this audit is document-only; no service was executed).

## Assumptions

- Drift is defined as: spec canonical value vs. implementation/runbook value that
  remain unequal after any conversion basis documented in the fixtures themselves
  (unit, tick rate, per-replica aggregation, key recomposition). Arithmetic
  coincidences without a documented basis (R5) do not count as conversions.
- Key-name variants across naming conventions (snake_case / camelCase /
  SCREAMING_SNAKE) refer to the same logical key.
- The runbook's prose values ("up to 6 times", "TLS floor to 1.2", "64 pooled
  connections in total") are treated as operational configuration claims
  comparable against spec keys.
- Benchmark rules (no network, no commit/push, no package installs) override the
  mission skill's local-authoring sync bootstrap; `mission-state.py` from the
  repo root was used as the state authority. Due to the session's hard USD
  budget, planner and executor ran inline in the orchestrator context (recorded
  in the assumption registry), while the review gate kept 3 independent
  parallel reviewer subagents; gate semantics (threshold, open_high, agreement,
  min-item) were unchanged.
- Alpha keys missing from its excerpt are unmeasured, not compliant and not
  drifted.
