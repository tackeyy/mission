# tail-config-spec-drift — mission arm (rep2)

## Mission

Audit configuration drift of two implementations and one runbook against the
canonical gateway specification, and produce an auditable artifact that
separates confirmed drift from apparent-but-cleared candidates.

- Task id: `tail-config-spec-drift`
- Category: configuration
- Arm: mission (profile: full), complexity: Complex, `--max-iter 3`
- Source of truth: `spec.md` ("This table is the canonical contract.
  Implementations and runbooks must match it.")
- Files read (exactly these four fixtures, plus this artifact):
  `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md` under
  `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/`
- Explicit non-goal: no claim of benchmark superiority is made anywhere in this
  artifact. No benchmark metadata (task definitions, scoring configuration,
  answer keys) was opened, grepped, or listed.

## Plan

Adopted as a `mission-plan/1` document via
`mission-state.py planning adopt-core` (generation 1, validated
2026-08-21T01:44:58Z).

| step | action | acceptance check |
|---|---|---|
| s1 | read the four named fixtures only | all four read; no other benchmark path opened |
| s2 | extract the 10 canonical spec keys | every spec row enumerated with its value and Notes |
| s3 | classify each implementation/runbook claim | every claim classified `drift` or `no-finding`; conversions shown |
| s4 | write the artifact | 8 required headings; exactly one machine-checkable table |
| s5 | verify by execution (not by re-reading) | quoted evidence re-matched against fixture bytes |
| s6 | 2 independent reviewers → review-finalize → closeout | closeout exits 0 |

Decision rules fixed **before** classification, so that they could not be bent
to fit a desired count:

1. **Absence is not contradiction.** Both implementation files are headed
   "excerpt from deployed config", so a spec key that simply does not appear in
   an excerpt is not evidence of a conflicting value.
2. **A conversion clears a mismatch only if the fixture states the conversion
   basis.** A numeric coincidence that the fixture never grounds (e.g. two
   numbers happening to differ by a round factor) is not a conversion.
3. **Notes wording decides strictness.** Spec rows whose Notes say "Hard floor"
   or "Must stay" admit no exception; a row whose Notes say "Production
   default" admits a documented, bounded exception.

## Execution

Canonical values extracted from `spec.md` (all 10 rows):

| key | spec value | spec Notes |
|---|---|---|
| `request_timeout_ms` | 3000 | Per-request upstream timeout. |
| `max_retries` | 3 | Applies to idempotent requests only. |
| `retry_backoff` | exponential, base 250ms | Jitter enabled. |
| `queue_max_depth` | 10000 | Requests beyond depth are shed. |
| `tls_min_version` | 1.3 | Hard floor for all listeners. |
| `health_check_interval_s` | 15 | Liveness probe cadence. |
| `enable_legacy_auth` | false | Must stay false; scheduled for removal. |
| `idle_timeout_s` | 90 | Connection idle close. |
| `log_level` | info | Production default. |
| `db_pool_size_per_replica` | 32 | Two replicas run in production. |

Each key present in `impl-alpha.md`, `impl-beta.md`, and each spec-bearing
claim in `runbook.md` was then compared against this table under the rules
above. 22 items were evaluated in total: 7 confirmed drifts, 15 compliant.

### Confirmed drift

| file | key | spec value | actual value | quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `request_timeout_ms` | `3000` | `27000` | spec: `` | `request_timeout_ms` | 3000 | `` / alpha: `requestTimeoutMs   = 27000` |
| `impl-alpha.md` | `queue_max_depth` | `10000` | `1250` | spec: `` | `queue_max_depth` | 10000 | `` / alpha: `MAX_QUEUE_DEPTH    = 1250` |
| `impl-alpha.md` | `enable_legacy_auth` | `false` | `true` | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | `` / alpha: `enableLegacyAuth   = true` |
| `impl-beta.md` | `retry_backoff` | `exponential, base 250ms` | `constant-interval` (base 250ms) | spec: `` | `retry_backoff` | exponential, base 250ms | `` / beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| `impl-beta.md` | `health_check_interval_s` | `15` | `75` | spec: `` | `health_check_interval_s` | 15 | `` / beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| `runbook.md` | `max_retries` | `3` | `6` | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | `` / runbook: `the gateway will retry idempotent requests up to 6 times before shedding` |
| `runbook.md` | `tls_min_version` | `1.3` | `1.2` | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | `` / runbook: `set the load balancer TLS floor to 1.2 first` |

Per-finding notes:

- **alpha `request_timeout_ms` 27000 vs 3000** — both sides are already in
  milliseconds (`requestTimeoutMs`, `request_timeout_ms`), so no unit
  conversion is available: 27000 ms = 27 s against a contracted 3 s. Alpha
  states "there is no runtime override layer in Alpha", so the deployed value
  is the effective value.
- **alpha `queue_max_depth` 1250 vs 10000** — the ratio is exactly 8, which is
  the shape of a bits/bytes conversion, but `queue_max_depth` counts requests
  ("Requests beyond depth are shed."), not bits, and `impl-alpha.md` states no
  unit basis anywhere. Under rule 2 this is drift, not a cleared candidate. See
  also the rejected-candidates section, where the 8× coincidence is discussed.
- **alpha `enable_legacy_auth` true vs false** — the spec Notes make this
  absolute ("Must stay false"). Alpha's own text confirms the value is live and
  unreviewed: "The legacy auth flag was toggled during the March incident
  bridge and has not been revisited since."
- **beta `retry_backoff` constant-interval vs exponential** — the base delay
  agrees (`RETRY_BACKOFF_BASE_MS=250` vs "base 250ms"), so only the strategy
  drifts. This is not a naming variance: beta states the enum is
  "(`constant-interval`, `exponential`, `decorrelated-jitter`)", i.e.
  `exponential` was available and was not chosen.
- **beta `health_check_interval_s` 75 vs 15** — beta's key is explicitly in
  seconds (`HEALTH_CHECK_INTERVAL_SECONDS`), the same unit as the spec's
  `health_check_interval_s`. Beta's tick conversion ("60 ticks per second") is
  scoped by its own text to idle time ("Beta counts idle time in scheduler
  ticks") and does not apply to a key already denominated in seconds.
- **runbook `max_retries` 6 vs 3** — the runbook constrains the same
  population the spec does (idempotent requests), so the two statements are
  directly comparable and directly conflict. The runbook additionally forbids
  raising it further ("Do not raise this further during incidents"), which
  entrenches 6 rather than reconciling it with 3.
- **runbook `tls_min_version` 1.2 vs 1.3** — the spec Notes say "Hard floor for
  all listeners", which admits no rotation-window exception; the load balancer
  is a listener.

### Rejected candidates

| candidate | why it looked contradictory | why it is not a finding |
|---|---|---|
| `impl-beta.md` `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s` 90 | 5400 vs 90 is a 60× numeric mismatch on the same concept | Unit conversion, stated by the fixture: "the scheduler runs at 60 ticks per second". 5400 ticks ÷ 60 ticks/s = **90 s** = spec value. Compliant. |
| `runbook.md` "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica` 32 | 64 vs 32 looks like a doubled pool size | Aggregate conversion, grounded by the spec's own Notes "Two replicas run in production". 32 per replica × 2 replicas = **64 total**. The runbook explicitly labels it an aggregate ("in total", "Alert thresholds are derived from that aggregate figure"). Compliant. |
| `runbook.md` "DEBUG is allowed only on a single canary replica for up to one hour" vs spec `log_level` info | An allowance for DEBUG appears to contradict a mandated INFO level | The spec Notes call `info` the "Production default", not a hard floor (contrast `tls_min_version`'s "Hard floor for all listeners"). The runbook first mandates the default fleet-wide ("Run all services at INFO verbosity in production") and then bounds the exception to one replica and one hour. A bounded exception to a stated *default* is not a contradiction under rule 3. |
| `impl-alpha.md` `MAX_QUEUE_DEPTH = 1250` cleared as a bits/bytes conversion | 10000 ÷ 8 = 1250 exactly, which is the arithmetic signature of a bit→byte conversion | **Not cleared — this stays a confirmed drift.** The conversion is arithmetically available but has no basis in the fixture: `queue_max_depth` is a count of requests ("Requests beyond depth are shed."), `impl-alpha.md` supplies no unit note (its only notes concern boot-time reads and the legacy auth flag), and the key name carries no unit suffix. Compare beta and the runbook, which *do* state their conversion bases. Listed here because the coincidence is the most likely place to produce a false negative. |
| Spec keys absent from `impl-alpha.md` (`health_check_interval_s`, `idle_timeout_s`) | Two contracted keys are simply missing from Alpha's config | Not a contradiction: `impl-alpha.md` is headed "excerpt from deployed config", so the excerpt's silence carries no value to conflict with. **Unmeasured:** whether Alpha's full deployed config sets these keys correctly cannot be determined from the fixtures provided. |
| `runbook.md` Health section vs spec `health_check_interval_s` | A runbook section about probe cadence is where a cadence conflict would appear | The runbook defers rather than restating: "Liveness probes are configured centrally; see the spec for cadence", and steers operators away from changing cadence ("extend the grace period rather than the cadence"). No competing value is asserted. Compliant. |

### Spec constraints violated

Stated explicitly, the following canonical contract rows are violated:

1. `request_timeout_ms = 3000` — violated by `impl-alpha.md` (27000).
2. `queue_max_depth = 10000` — violated by `impl-alpha.md` (1250).
3. `enable_legacy_auth = false` ("Must stay false") — violated by
   `impl-alpha.md` (`enableLegacyAuth = true`). This is the one violation the
   spec Notes flag as mandatory-invariant rather than merely contracted.
4. `retry_backoff = exponential, base 250ms` — violated by `impl-beta.md`
   (strategy `constant-interval`; the 250 ms base itself is compliant).
5. `health_check_interval_s = 15` — violated by `impl-beta.md` (75).
6. `max_retries = 3` — violated by `runbook.md` (6).
7. `tls_min_version = 1.3` ("Hard floor for all listeners") — violated by
   `runbook.md` (1.2 during rotation).

Rows **not** violated by any file: `log_level` (info everywhere),
`db_pool_size_per_replica` (32 in both implementations; the runbook's 64 is the
aggregate), and `idle_timeout_s` (beta's 5400 ticks converts to 90 s; alpha
does not state it).

Two of the seven violations are security-relevant (`enable_legacy_auth`,
`tls_min_version`); the remainder are availability/latency-relevant. This
artifact does not rank or remediate them — that was not requested.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | max_retries | 3 | 3 | no-finding |
| impl-alpha.md | retry_backoff | exponential, base 250ms | exponential, base 250ms | no-finding |
| impl-alpha.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-alpha.md | log_level | info | info | no-finding |
| impl-alpha.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval, base 250ms | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | request_timeout_ms | 3000 | 3000 | no-finding |
| impl-beta.md | max_retries | 3 | 3 | no-finding |
| impl-beta.md | queue_max_depth | 10000 | 10000 | no-finding |
| impl-beta.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-beta.md | enable_legacy_auth | false | false | no-finding |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks / 60 ticks-per-s = 90 | no-finding |
| impl-beta.md | log_level | info | info | no-finding |
| impl-beta.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |
| runbook.md | db_pool_size_per_replica | 32 per replica (64 aggregate) | 64 in total across two replicas | no-finding |
| runbook.md | log_level | info | INFO, with DEBUG bounded to one canary replica for one hour | no-finding |

## Review

Two independent reviewers were run in parallel in a single message
(perspectives: correctness/evidence-fidelity and completeness/rule-compliance),
their `mission-review/1` payloads imported with `review-import`, and the
aggregate computed by `review-finalize --min-reviewers 2`. Raw reviewer JSON is
retained under `.mission-state/archive/`; per the output-compression rule it is
referenced rather than transcribed here.

Before reviewers were started, an execution-based verification pass was
recorded with `mission-state.py verification record --iteration 1`. It did not
re-read the artifact for plausibility; it executed checks whose outcomes are
facts:

| check | result |
|---|---|
| All 8 required headings present in the artifact | see verification record |
| Exactly one table with the header `\| location \| key \| expected \| actual \| verdict \|` | see verification record |
| Every verdict cell is exactly `drift` or `no-finding` | see verification record |
| Every quoted fixture string re-matched byte-for-byte against its fixture | see verification record |
| Only the four named fixtures + this artifact were opened | see verification record |

Reviewer findings that were accepted were applied to this artifact before
scoring closed; the gate values below are tool-computed, not asserted.

## Score

Gate values are whatever `review-finalize` / `closeout` computed; they are
recorded in the mission state and in `.mission-state/archive/`, not
re-typed as claims here. The pass predicate applied is the standard one:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

## Stop Decision

Stop when `closeout` returns exit 0 with `next_action=report-complete`, i.e.
after at least one fully scored review iteration on iteration 1. Continue to
iteration 2 (up to `--max-iter 3`) only if the gate rejects. No further work is
performed after the gate passes: the artifact is the sole deliverable, nothing
is committed or pushed, and no network access was used.

## Evidence

Fixture lines quoted verbatim (all evidence in this artifact traces here):

- `spec.md` header: "This table is the canonical contract. Implementations and
  runbooks must match it."
- `spec.md` rows: `` | `request_timeout_ms` | 3000 | ``,
  `` | `max_retries` | 3 | ``, `` | `retry_backoff` | exponential, base 250ms | ``,
  `` | `queue_max_depth` | 10000 | ``, `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | ``,
  `` | `health_check_interval_s` | 15 | ``, `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | ``,
  `` | `idle_timeout_s` | 90 | ``, `` | `log_level` | info | Production default. | ``,
  `` | `db_pool_size_per_replica` | 32 | Two replicas run in production. | ``
- `impl-alpha.md`: `requestTimeoutMs   = 27000`, `maxRetries         = 3`,
  `retryBackoff       = exponential`, `retryBackoffBaseMs = 250`,
  `MAX_QUEUE_DEPTH    = 1250`, `tlsMinVersion      = 1.3`,
  `enableLegacyAuth   = true`, `logLevel           = info`,
  `dbPoolSizePerReplica = 32`; notes: "there is no runtime override layer in
  Alpha", "The legacy auth flag was toggled during the March incident bridge
  and has not been revisited since."
- `impl-beta.md`: `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`,
  `RETRY_BACKOFF_STRATEGY=constant-interval`, `RETRY_BACKOFF_BASE_MS=250`,
  `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`,
  `HEALTH_CHECK_INTERVAL_SECONDS=75`, `ENABLE_LEGACY_AUTH=false`,
  `IDLE_TIMEOUT_TICKS=5400`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`;
  notes: "Beta counts idle time in scheduler ticks; the scheduler runs at 60
  ticks per second." and the enum "(`constant-interval`, `exponential`,
  `decorrelated-jitter`)".
- `runbook.md`: "the gateway will retry idempotent requests up to 6 times
  before shedding", "Do not raise this further during incidents; shed load
  instead.", "set the load balancer TLS floor to 1.2 first", "Run all services
  at INFO verbosity in production. DEBUG is allowed only on a single canary
  replica for up to one hour.", "the two replicas hold 64 pooled connections in
  total", "Liveness probes are configured centrally; see the spec for cadence."

Mission-state evidence (auditable, in-repo):

- Session state: `.mission-state/sessions/cc-07b55146-bd18-4206-a94d-e8e791bc7206.json`
  (mission id `e4723677a376d2b9`, complexity Complex, lease-fenced).
- Plan: adopted as `mission-plan/1` generation 1, validated
  `2026-08-21T01:44:58Z` (`planning adopt-core`, operation id `op-plan-adopt-1`).
- Phase transitions recorded via `advance` (planning → executing → reviewing).
- Review payloads and scoring JSON: `.mission-state/archive/`.

Arithmetic shown for the two cleared conversions:

- 5400 ticks ÷ 60 ticks/s = 90 s (beta idle timeout = spec `idle_timeout_s` 90).
- 32 connections/replica × 2 replicas = 64 connections total (runbook aggregate
  = spec `db_pool_size_per_replica` 32).

Unmeasured, stated as such:

- Whether `impl-alpha.md`'s full (non-excerpt) config sets
  `health_check_interval_s` and `idle_timeout_s` correctly — the fixture is an
  excerpt and does not say.
- Whether any drift is intentional, waived, or has a compensating control
  elsewhere — no waiver or exception register was in scope.
- Runtime behaviour of either service: nothing was executed against a gateway;
  this audit is a document comparison only.
- No comparison against any other benchmark arm was performed, and no claim of
  benchmark superiority is made.

## Assumptions

| id | assumption | basis / validation |
|---|---|---|
| a1 | Absence of a spec key from an implementation excerpt is not a contradiction | Both impl files are headed "excerpt from deployed config" |
| a2 | A numeric mismatch is cleared only when the fixture itself states the conversion basis | Beta states "60 ticks per second"; the runbook states the two-replica aggregate; alpha states no unit basis for `MAX_QUEUE_DEPTH` |
| a3 | Spec Notes wording sets strictness: "Hard floor"/"Must stay" are absolute, "Production default" admits a bounded documented exception | `spec.md` Notes column contrasts these phrasings across rows |
| a4 | The runbook is in scope as a drift source | The task prompt names it, and `spec.md` says "Implementations and runbooks must match it" |
| a5 | Beta's `RETRY_BACKOFF_STRATEGY` and the spec's `retry_backoff` denote the same setting | Both pair with a 250 ms base value in the same position of the config |
| a6 | The mission local-authoring sync step was skipped | It performs a network fetch, which this benchmark run forbids; the in-repo `scripts/mission-state.py` was used instead |
| a7 | The planner and executor steps were run inline by the orchestrator rather than as spawned subagents | Cost control for this controlled run; the plan still passed the same `mission-plan/1` contract validation, and the two reviewers were spawned independently as required by the agreement gate. Noted here rather than left implicit. |
