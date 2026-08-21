# tail-config-spec-drift — mission arm (rep1)

## Mission

Audit configuration drift of two implementations and one runbook against the canonical
gateway configuration spec, quoting key and both values for every contradiction, and
explicitly rejecting entries that only look contradictory but are consistent after a
unit or aggregate conversion.

Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
("This table is the canonical contract. Implementations and runbooks must match it.").

Audited artifacts: `impl-alpha.md`, `impl-beta.md`, `runbook.md` in the same fixture
directory. No other file under `benchmarks/mission-vs-goal/` was opened, listed, or
searched.

## Plan

Adopted plan document: `.mission-state/plans/` (canonical `mission-plan/1`, adopted via
`planning adopt-core`, source `.mission-state/plan-iter1.json`).

| Step | Action | Acceptance |
|---|---|---|
| s1 | Read exactly the four named fixtures | all four read, nothing else opened |
| s2 | Enumerate the 10 canonical spec keys and values | full spec key table |
| s3 | Classify every observed impl/runbook entry as drift or conversion-consistent | every entry classified, conversions shown arithmetically |
| s4 | Write this artifact | 8 required headings + exactly one machine-checkable table |
| s5 | Re-derive the findings from the fixtures as verification | verification record stored in mission state |
| s6 | Two parallel reviewers → `review-finalize` → `closeout` | both exit 0 |

## Execution

### Canonical spec (source of truth, all 10 keys)

| Key | Spec value |
|---|---|
| `request_timeout_ms` | 3000 |
| `max_retries` | 3 |
| `retry_backoff` | exponential, base 250ms |
| `queue_max_depth` | 10000 |
| `tls_min_version` | 1.3 |
| `health_check_interval_s` | 15 |
| `enable_legacy_auth` | false |
| `idle_timeout_s` | 90 |
| `log_level` | info |
| `db_pool_size_per_replica` | 32 |

### Confirmed drift

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `request_timeout_ms` | 3000 | 27000 | spec row: `request_timeout_ms` = `3000` ("Per-request upstream timeout.") / alpha: `requestTimeoutMs   = 27000` |
| `impl-alpha.md` | `queue_max_depth` | 10000 | 1250 | spec row: `queue_max_depth` = `10000` ("Requests beyond depth are shed.") / alpha: `MAX_QUEUE_DEPTH    = 1250` |
| `impl-alpha.md` | `enable_legacy_auth` | false | true | spec row: `enable_legacy_auth` = `false` ("Must stay false; scheduled for removal.") / alpha: `enableLegacyAuth   = true` |
| `impl-beta.md` | `retry_backoff` (strategy) | exponential | constant-interval | spec row: `retry_backoff` = `exponential, base 250ms` ("Jitter enabled.") / beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| `impl-beta.md` | `health_check_interval_s` | 15 | 75 | spec row: `health_check_interval_s` = `15` ("Liveness probe cadence.") / beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| `runbook.md` | `max_retries` | 3 | 6 | spec row: `max_retries` = `3` ("Applies to idempotent requests only.") / runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| `runbook.md` | `tls_min_version` | 1.3 | 1.2 | spec row: `tls_min_version` = `1.3` ("Hard floor for all listeners.") / runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

Notes on two of the above:

- **Alpha `request_timeout_ms` 27000 vs 3000** — both sides are already in milliseconds
  (`requestTimeoutMs`, `request_timeout_ms`), so no unit conversion applies. 27000 is not
  reachable from 3000 by any conversion stated in either file (it is not 3000 × retries: with
  `max_retries = 3` a full attempt budget would be 4 × 3000 = 12000, not 27000). Alpha
  also states "there is no runtime override layer in Alpha", so the deployed value is the
  effective value.
- **Alpha `queue_max_depth` 1250 vs 10000** — the ratio is exactly 8, which invites a
  bits-vs-bytes reading. Neither fixture states any such basis: the spec defines the key in
  request units ("Requests beyond depth are shed") and Alpha declares no unit for
  `MAX_QUEUE_DEPTH`. Unlike Beta's ticks and the runbook's replica aggregate, there is no
  stated conversion, so this is reported as drift rather than cleared.

### Rejected candidates (look contradictory, are not drift)

| File | Key | Why it looks contradictory | Conversion / reasoning that clears it |
|---|---|---|---|
| `impl-beta.md` | `idle_timeout_s` | Beta shows `IDLE_TIMEOUT_TICKS=5400` against a spec value of 90 — a 60× mismatch on its face | Beta states "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." 5400 ticks ÷ 60 ticks/s = **90 s** = spec value 90. Consistent. |
| `runbook.md` | `db_pool_size_per_replica` | Runbook says "the two replicas hold 64 pooled connections in total", double the spec's 32 | Spec value is **per replica** and notes "Two replicas run in production." 32 per replica × 2 replicas = **64 total**. The runbook figure is the aggregate, not a per-replica override. Consistent. |
| `impl-alpha.md` | `retry_backoff` | Alpha splits the value across two lines (`retryBackoff`, `retryBackoffBaseMs`), so no single line equals the spec string | `retryBackoff = exponential` + `retryBackoffBaseMs = 250` recombine to "exponential, base 250ms" = spec value. Consistent. (Jitter is unstated in Alpha — unobserved, see Assumptions.) |
| `impl-beta.md` | `retry_backoff` (base) | Base sits next to a non-conforming strategy name, so the whole key looks wrong | `RETRY_BACKOFF_BASE_MS=250` matches the spec's "base 250ms" exactly. Only the strategy component drifts (reported separately above); the base value itself is compliant. |
| `runbook.md` | `log_level` | Runbook permits DEBUG ("DEBUG is allowed only on a single canary replica for up to one hour"), while the spec says `info` | The spec qualifies `info` as the "Production default", not a hard floor — contrast the wording it uses for hard constraints ("Hard floor for all listeners", "Must stay false"). The runbook's baseline is "Run all services at INFO verbosity in production", and the DEBUG allowance is scoped to one canary replica and time-boxed. No contradiction of a stated constraint. |
| `runbook.md` | `health_check_interval_s` | Health section discusses probe behaviour without a number, which could read as an undocumented cadence | Runbook defers: "Liveness probes are configured centrally; see the spec for cadence." It changes the grace period, not the cadence. Consistent. |
| `impl-alpha.md` | `health_check_interval_s`, `idle_timeout_s` | Both spec keys are missing from Alpha's excerpt | Absence is not a contradiction: the excerpt asserts no conflicting value. Recorded as **unmeasured** for Alpha, not as drift. |

### Spec constraints violated

1. `enable_legacy_auth` — "Must stay false; scheduled for removal." Violated by Alpha
   (`enableLegacyAuth = true`). This is the only constraint the spec words as an
   imperative, and Alpha explains it as unreverted incident state ("toggled during the
   March incident bridge and has not been revisited since").
2. `tls_min_version` — "Hard floor for all listeners." Violated by the runbook, which
   instructs operators to lower the load-balancer floor to 1.2 during certificate
   rotation.
3. The canonical-contract requirement itself — "Implementations and runbooks must match
   it." Violated by all seven confirmed-drift rows: Alpha on `request_timeout_ms`,
   `queue_max_depth` and `enable_legacy_auth`; Beta on `retry_backoff` strategy and
   `health_check_interval_s`; the runbook on `max_retries` and `tls_min_version`.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-beta.md | retry_backoff strategy | exponential | constant-interval | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |
| impl-alpha.md | max_retries | 3 | 3 | no-finding |
| impl-alpha.md | retry_backoff | exponential, base 250ms | exponential + base 250ms | no-finding |
| impl-alpha.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-alpha.md | log_level | info | info | no-finding |
| impl-alpha.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| impl-beta.md | request_timeout_ms | 3000 | 3000 | no-finding |
| impl-beta.md | max_retries | 3 | 3 | no-finding |
| impl-beta.md | retry_backoff base | 250ms | 250ms | no-finding |
| impl-beta.md | queue_max_depth | 10000 | 10000 | no-finding |
| impl-beta.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-beta.md | enable_legacy_auth | false | false | no-finding |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks / 60 ticks per s = 90 | no-finding |
| impl-beta.md | log_level | info | info | no-finding |
| impl-beta.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| runbook.md | log_level | info (production default) | INFO baseline, DEBUG time-boxed on one canary | no-finding |
| runbook.md | db_pool_size_per_replica | 32 per replica | 64 total across 2 replicas = 32 each | no-finding |
| runbook.md | health_check_interval_s | 15 | deferred to spec, no value asserted | no-finding |

## Review

Two independent reviewers were run in parallel (single message) against this artifact
and the four fixtures, per the mission profile for Complex. Their `mission-review/1`
payloads were validated and stored by `mission-state.py review-import`, and aggregated
by `review-finalize --min-reviewers 2`. Review evidence paths and the aggregate are in
`.mission-state/` (see Evidence); reviewer prose is not re-transcribed here.

Independent pre-review verification (`mission-state.py verification record`) re-derived
each numeric claim directly from the fixture text rather than from this artifact:
5400 ÷ 60 = 90; 32 × 2 = 64; spec key count = 10; every quoted string matched verbatim.

## Score

Composite score, per-axis scores, `open_high`, and `max_agreement_delta` were computed
by `review-finalize` / `push-score` and recorded in mission state. The pass gate is the
tool-computed one:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

Values are in `.mission-state/sessions/cc-654da05e-9ee8-4922-8f2e-869fa9a3a1a2.json`
(`score_history`) and the archived scoring JSON; they are not restated by hand here, to
avoid transcription drift.

## Stop Decision

Stopped after iteration 1 on `closeout` exit 0 (`mark-passes` → `next` →
`report-complete`). No further iteration was run because the gate passed with
`open_high == 0` and `iteration < max_iter (3)`.

No benchmark-superiority claim is made here. This artifact reports only this task.

## Evidence

| Item | Reference |
|---|---|
| Mission session state | `.mission-state/sessions/cc-654da05e-9ee8-4922-8f2e-869fa9a3a1a2.json` |
| Adopted plan (`mission-plan/1`) | `.mission-state/plans/` (adopted via `planning adopt-core`, input `.mission-state/plan-iter1.json`) |
| Verification record | `mission-state.py verification record --iteration 1` (stored in session state) |
| Reviewer payloads + aggregate | `.mission-state/archive/` (paths returned by `review-import` / `review-finalize`) |
| Fixture: source of truth | `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md` |
| Fixture: audited | `impl-alpha.md`, `impl-beta.md`, `runbook.md` (same directory) |

Every quoted value above is a verbatim string from the named fixture. Files read during
this run under `benchmarks/mission-vs-goal/`: the four named fixtures and this output
file only.

## Assumptions

1. `spec.md` is the sole source of truth; the two implementations and the runbook are
   the audited artifacts. Basis: "This table is the canonical contract."
2. Keys absent from a fixture are **unmeasured**, not drift. Concretely: Alpha states no
   `health_check_interval_s` and no `idle_timeout_s`; the runbook states no
   `request_timeout_ms`, `retry_backoff`, `queue_max_depth`, `enable_legacy_auth` or
   `idle_timeout_s`. Whether the real deployments comply on those keys is unmeasured
   here.
3. A conversion stated inside a fixture (Beta's 60 ticks/s, the spec's two replicas)
   legitimately clears an apparent contradiction; an unstated conversion does not. This
   is why Beta's `IDLE_TIMEOUT_TICKS` is cleared while Alpha's `MAX_QUEUE_DEPTH` is not.
4. `retry_backoff` is treated as two comparable components (strategy, base). Beta's base
   is compliant and only its strategy drifts; splitting it this way is a judgment call,
   not a fixture-stated rule.
5. Jitter ("Jitter enabled." in the spec) is asserted by neither implementation.
   Unmeasured, therefore not reported either way.
6. Planning for this iteration was performed inline by the orchestrator and adopted
   through `planning adopt-core` rather than by spawning `mission-planner`; the plan
   still satisfies the same `mission-plan/1` contract. `mission-local-authoring-sync.sh`
   was not run because this run forbids network access; the repository-root
   `scripts/mission-state.py` was used directly.
