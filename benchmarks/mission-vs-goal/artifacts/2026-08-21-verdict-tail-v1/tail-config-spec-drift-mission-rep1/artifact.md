# tail-config-spec-drift — mission arm (rep1)

- Task id: `tail-config-spec-drift`
- Category: configuration
- Arm: mission (profile: full, complexity: Complex, `--max-iter 3`)
- Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`

## Mission

Audit configuration drift of three subordinate documents (`impl-alpha.md`, `impl-beta.md`, `runbook.md`) against the canonical spec, adjudicate exactly the ten mandated items, quote the exact fixture identifier and value for every confirmed finding, and explicitly clear the candidates that only *look* contradictory by showing the unit or aggregate conversion.

Authority basis (quoted from `spec.md`, line 3):

> This table is the canonical contract. Implementations and runbooks must match it.

Scope boundary honoured: the only files opened were the four named fixtures plus this output file. No other path under `benchmarks/mission-vs-goal/` was opened, listed, or searched — task definitions, scoring configuration, and answer keys were not consulted.

## Plan

Adopted canonical plan: `.mission-state/plans/750c2eac3ea5ec5f.json` (digest `sha256:750c2eac3ea5ec5f20691fe9dd519117437622088a968114fecfa2b8e53c82c8`, source `core`, generation 1).

| Step | Action | Acceptance |
|---|---|---|
| S1 | Read the four fixtures and build a per-file key/value inventory | Every one of the ten items has a quoted literal line from its fixture |
| S2 | Adjudicate each item: drift vs. cleared-by-conversion | Every `no-finding` shows the conversion/reasoning; every `drift` quotes both values |
| S3 | Write this artifact | Eight mandated headings; exactly one findings table with the mandated header and ten mandated rows |
| S4 | Verify, then run one scored review iteration to the gate | Verification recorded; two independent reviewers; `review-finalize` and `closeout` exit 0 |

## Execution

Canonical spec values (verbatim from `spec.md`):

| Key | Spec value | Spec note |
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

Declared conversion factors found in the subordinate documents (these are the only conversion bases any fixture supplies):

- `impl-beta.md`: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second."
- `impl-beta.md`: "Backoff strategy names follow the retry library's enum (`constant-interval`, `exponential`, `decorrelated-jitter`)." — i.e. `constant-interval` and `exponential` are *distinct* enum members, not synonyms.
- `runbook.md`: "the two replicas hold 64 pooled connections in total" combined with the spec note "Two replicas run in production."

### Confirmed drift

| # | File | Key | Spec value | Actual value | Quoted evidence (verbatim) |
|---|---|---|---|---|---|
| D1 | `impl-alpha.md` | `request_timeout_ms` | 3000 (ms) | 27000 (ms) | spec: `` | `request_timeout_ms` | 3000 | `` / alpha: `requestTimeoutMs   = 27000` |
| D2 | `impl-alpha.md` | `queue_max_depth` | 10000 | 1250 | spec: `` | `queue_max_depth` | 10000 | `` / alpha: `MAX_QUEUE_DEPTH    = 1250` |
| D3 | `impl-alpha.md` | `enable_legacy_auth` | false | true | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | `` / alpha: `enableLegacyAuth   = true` |
| D4 | `impl-beta.md` | `health_check_interval_s` | 15 (s) | 75 (s) | spec: `` | `health_check_interval_s` | 15 | `` / beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| D5 | `impl-beta.md` | `retry_backoff` | exponential, base 250ms | constant-interval (base 250ms) | spec: `` | `retry_backoff` | exponential, base 250ms | `` / beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| D6 | `runbook.md` | `max_retries` | 3 | 6 | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | `` / runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| D7 | `runbook.md` | `tls_min_version` | 1.3 | 1.2 | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | `` / runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

Per-finding notes:

- **D1** — 27000 ms is 9× the contract value; no unit mismatch is available to explain it, since both the spec key and the Alpha key are expressed in milliseconds (`request_timeout_ms` / `requestTimeoutMs`). 3000 ms = 3 s, 27000 ms = 27 s.
- **D2** — Alpha's key `MAX_QUEUE_DEPTH` carries no unit suffix and Alpha declares no conversion basis anywhere ("values above are read at boot; there is no runtime override layer in Alpha"). Depth is a count of requests in both documents, so 1250 ≠ 10000 stands as drift. (The arithmetic coincidence 1250 × 8 = 10000 is addressed in *Rejected candidates* below and does **not** clear this row.)
- **D3** — The spec note is an explicit prohibition ("Must stay false"). Alpha's own text confirms the deviation is unreviewed: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since."
- **D4** — Beta's tick conversion does not apply here: the key is named `..._SECONDS`, not `..._TICKS`. Even if the tick basis were forced, 75 ÷ 60 = 1.25 s, which is also not 15 s. Drift either way.
- **D5** — Beta's base delay matches (`RETRY_BACKOFF_BASE_MS=250` vs. spec "base 250ms"), but the *strategy* does not. Beta's own note establishes that `constant-interval` and `exponential` are separate members of the same enum, so this is a substantive contradiction, not a naming variant. Jitter ("Jitter enabled" in the spec) is unmeasured in Beta — no jitter key appears in the excerpt — and is therefore not asserted either way.
- **D6** — The runbook states a ceiling of 6 retries for exactly the population the spec constrains ("idempotent requests" in both). It also forbids raising further, which shows 6 is the intended operating value, not a typo bound.
- **D7** — The spec's floor is absolute ("Hard floor for all listeners"), so a temporary rotation-window exception at 1.2 still contradicts it. The contradiction is in the runbook procedure itself; `tlsMinVersion = 1.3` in Alpha and `TLS_MIN_VERSION=1.3` in Beta both comply, so the drift is attributed to `runbook.md` only.

### Rejected candidates (looked contradictory, cleared)

| # | File | Key | Apparent contradiction | Clearing conversion / reasoning |
|---|---|---|---|---|
| R1 | `impl-beta.md` | `idle_timeout_s` | `IDLE_TIMEOUT_TICKS=5400` vs. spec `90` — a 60× numeric gap | Beta declares "the scheduler runs at 60 ticks per second". 5400 ticks ÷ 60 ticks/s = **90 s**, exactly the spec value. Compliant. |
| R2 | `runbook.md` | `db_pool_size_per_replica` | "the two replicas hold 64 pooled connections in total" vs. spec `32` — a 2× numeric gap | The runbook figure is an **aggregate**, the spec figure is **per replica**. Spec note: "Two replicas run in production." 64 total ÷ 2 replicas = **32 per replica**, exactly the spec value. Compliant. |
| R3 | `runbook.md` | `log_level` | "Run all services at INFO verbosity in production" vs. spec `info` — case mismatch; plus the sentence "DEBUG is allowed only on a single canary replica for up to one hour" appears to authorise a non-`info` level | Case folding only: `INFO` and `info` denote the same severity level, and no third value is introduced for the production fleet. The DEBUG clause does not contradict the spec either, because the spec value is qualified as the "Production default" — a bounded, single-replica, one-hour exception is consistent with a default rather than an absolute floor. Compliant. |

Additional candidates considered and **not** reported as drift rows (they are not among the ten adjudication items, and the evidence does not support a defect claim):

- **`impl-alpha.md` bit/byte reading of `MAX_QUEUE_DEPTH`** — 1250 × 8 = 10000 is arithmetically exact, which makes it superficially look like the same class of unit conversion as R1/R2. It is rejected as a clearing conversion because no fixture declares any bit/byte basis for queue depth, and queue depth is a count of shed-able requests ("Requests beyond depth are shed"), not a size in bits. An undeclared factor invented by the auditor cannot clear a contradiction, so D2 remains drift.
- **Keys absent from a fixture** — `impl-alpha.md` contains no `health_check_interval_s` and no `idle_timeout_s`; `impl-beta.md` contains no `db_pool_size_per_replica` deviation. Absence in an excerpt ("excerpt from deployed config") is not evidence of a contradiction; these are unmeasured, not compliant-by-proof, and are not claimed as findings.
- **Jitter for `retry_backoff`** — the spec requires "Jitter enabled". Neither implementation excerpt exposes a jitter key, so jitter conformance is **unmeasured** in both Alpha and Beta.

### Violated spec constraints (explicit)

The following canonical constraints are violated by at least one subordinate document:

1. `request_timeout_ms = 3000` — violated by `impl-alpha.md` (27000).
2. `queue_max_depth = 10000` — violated by `impl-alpha.md` (1250).
3. `enable_legacy_auth = false` ("Must stay false; scheduled for removal") — violated by `impl-alpha.md` (true). This is the only constraint the spec states as a hard prohibition on the value itself.
4. `health_check_interval_s = 15` — violated by `impl-beta.md` (75).
5. `retry_backoff = exponential, base 250ms` — violated by `impl-beta.md` (strategy `constant-interval`; base is compliant).
6. `max_retries = 3` ("Applies to idempotent requests only") — violated by `runbook.md` (up to 6, same request population).
7. `tls_min_version = 1.3` ("Hard floor for all listeners") — violated by `runbook.md` (floor lowered to 1.2 during rotation).

Constraints found compliant across all evaluated items: `idle_timeout_s` (Beta, after tick conversion), `log_level` (runbook), `db_pool_size_per_replica` (runbook, after aggregate conversion). Also observed compliant but outside the ten adjudication items: `max_retries=3`, `tls_min_version=1.3`, `log_level=info`, `db_pool_size_per_replica=32` in `impl-alpha.md`; `request_timeout_ms=3000`, `max_retries=3`, `queue_max_depth=10000`, `tls_min_version=1.3`, `enable_legacy_auth=false`, `log_level=info`, `db_pool_size_per_replica=32` in `impl-beta.md`.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks = 90 s | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval, base 250ms | drift |
| runbook.md | db_pool_size_per_replica | 32 | 64 total / 2 replicas = 32 | no-finding |
| runbook.md | log_level | info | INFO | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |

## Review

Mission loop, iteration 1. Two independent reviewers (`correctness/evidence` and `contract-compliance`) reviewed this artifact against the task validator. Their `mission-review/1` payloads are stored verbatim under `.mission-state/archive/` and aggregated by `mission-state.py review-finalize`; they are not transcribed here (output-compression discipline — the archive is the record).

Pre-review verification (facts obtained by execution, not opinion) was recorded via `mission-state.py verification record --iteration 1`:

| Check | Result |
|---|---|
| Findings table header matches the mandated string exactly | ok |
| Exactly one table with the mandated header exists in the artifact | ok |
| Exactly ten findings rows, matching the ten mandated `location`/`key` strings | ok |
| Every `verdict` cell is exactly `drift` or `no-finding` | ok |
| All eight mandated headings present | ok |
| Every quoted fixture value re-matched against the fixture file text | ok |
| Conversion arithmetic re-computed (5400/60 = 90; 64/2 = 32; 75/60 = 1.25 ≠ 15) | ok |

Reviewer findings at severity Medium or higher that were applied to this artifact are reflected in the text above; residual Low observations are recorded in the archive.

## Score

Gate values are tool-computed by `mission-state.py review-finalize` / `closeout` and are recorded in the session state at `.mission-state/sessions/cc-e963af39-a173-4cb5-905d-65ce03329515.json`; the scoring JSON and per-reviewer evidence are under `.mission-state/archive/`. Threshold: 4.0 (default). This artifact does not restate the numbers from memory — the state file is the authoritative record, and any figure quoted outside it would be a transcription rather than evidence.

## Stop Decision

Stop when the mission gate is satisfied: findings evidence path present, `open_high == 0`, `max_agreement_delta <= 1.5`, composite `>= 4.0`, and `min(scored_items) >= 3.5`, confirmed by `mission-state.py closeout` exiting 0. If the gate had failed, the loop would have continued to iteration 2 (limit 3). No superiority claim of any kind is made about this arm; this artifact completes one task only.

## Evidence

Fixture lines quoted in this artifact (verbatim, with the fixture they come from):

- `spec.md`: `| request_timeout_ms | 3000 | Per-request upstream timeout. |`, `| max_retries | 3 | Applies to idempotent requests only. |`, `| retry_backoff | exponential, base 250ms | Jitter enabled. |`, `| queue_max_depth | 10000 | Requests beyond depth are shed. |`, `| tls_min_version | 1.3 | Hard floor for all listeners. |`, `| health_check_interval_s | 15 | Liveness probe cadence. |`, `| enable_legacy_auth | false | Must stay false; scheduled for removal. |`, `| idle_timeout_s | 90 | Connection idle close. |`, `| log_level | info | Production default. |`, `| db_pool_size_per_replica | 32 | Two replicas run in production. |`
- `impl-alpha.md`: `requestTimeoutMs   = 27000`, `MAX_QUEUE_DEPTH    = 1250`, `enableLegacyAuth   = true`, `maxRetries         = 3`, `retryBackoff       = exponential`, `retryBackoffBaseMs = 250`, `tlsMinVersion      = 1.3`, `logLevel           = info`, `dbPoolSizePerReplica = 32`
- `impl-beta.md`: `HEALTH_CHECK_INTERVAL_SECONDS=75`, `IDLE_TIMEOUT_TICKS=5400`, `RETRY_BACKOFF_STRATEGY=constant-interval`, `RETRY_BACKOFF_BASE_MS=250`, `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `ENABLE_LEGACY_AUTH=false`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`, plus the note "the scheduler runs at 60 ticks per second"
- `runbook.md`: "retry idempotent requests up to 6 times before shedding", "set the load balancer TLS floor to 1.2 first", "Run all services at INFO verbosity in production. DEBUG is allowed only on a single canary replica for up to one hour.", "the two replicas hold 64 pooled connections in total"

Mission-state evidence:

- Session state: `.mission-state/sessions/cc-e963af39-a173-4cb5-905d-65ce03329515.json`
- Canonical plan: `.mission-state/plans/750c2eac3ea5ec5f.json` (`sha256:750c2eac3ea5ec5f20691fe9dd519117437622088a968114fecfa2b8e53c82c8`)
- Review payloads, aggregation and scoring JSON: `.mission-state/archive/`
- Routing: the CLI did **not** route this task to the goal contract (complexity `Complex`); the mission loop was run as the implementer role, so the mission headings apply.

Not measured (stated explicitly rather than inferred):

- Jitter conformance for `retry_backoff` in either implementation — no jitter key appears in either excerpt.
- Keys absent from a given fixture excerpt (e.g. `health_check_interval_s` and `idle_timeout_s` in `impl-alpha.md`) — absence in an excerpt is not evidence of either compliance or drift.
- Whether any deviation is deliberately waived outside these four documents — no waiver register was in scope.
- Runtime/deployed behaviour — only document text was audited; nothing was executed against a running gateway.

## Assumptions

| ID | Assumption | Basis / validation |
|---|---|---|
| A1 | `spec.md` is the sole source of truth; the two implementations and the runbook are subordinate | `spec.md`: "This table is the canonical contract. Implementations and runbooks must match it." |
| A2 | A conversion declared *inside a fixture* (ticks-per-second, replica count) legitimately clears an apparent contradiction; a conversion the auditor invents does not | Beta declares 60 ticks/s; runbook declares 64 connections across two replicas; spec declares two replicas. No fixture declares any bit/byte basis for queue depth, which is why D2 stays drift. |
| A3 | Case-only differences in enum-like values (`INFO` vs `info`) are not drift | Same severity level, no third value introduced for the production fleet. |
| A4 | A spec value labelled "Production default" tolerates a bounded, explicitly scoped exception; a value labelled "Hard floor" or "Must stay false" does not | Spec notes distinguish "Production default" (`log_level`) from "Hard floor for all listeners" (`tls_min_version`) and "Must stay false" (`enable_legacy_auth`). This is why R3 clears while D7 does not. |
| A5 | Benchmark metadata under `benchmarks/mission-vs-goal/` beyond the four named fixtures and this output file is out of bounds | Task-prompt scope rule; all file access in this run was restricted to those paths. |
