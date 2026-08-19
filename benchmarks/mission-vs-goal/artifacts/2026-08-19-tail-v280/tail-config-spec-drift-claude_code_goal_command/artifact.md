# tail-config-spec-drift — claude_code_goal_command

Task id: `tail-config-spec-drift` · Category: configuration · Arm: `claude_code_goal_command`

## Goal

Audit configuration drift against the canonical spec (`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`) across `impl-alpha.md`, `impl-beta.md`, and `runbook.md`; report every place an implementation or the runbook contradicts the spec (quoting key and both values), and explicitly reject the entries that only *look* contradictory but are consistent after unit or aggregate conversion, showing the conversion.

Scope limits observed: read only the four named fixture files; wrote only this artifact; no commits, pushes, installs, or network access; no benchmark metadata (task definitions, scoring config, answer keys) opened.

## Result

**7 confirmed drifts** (3 in Alpha, 2 in Beta, 2 in the runbook) and **4 rejected candidates** (2 cleared by conversion, 1 cleared by spec wording, 1 cleared as absence-not-contradiction).

### Confirmed drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `request_timeout_ms` (impl name `requestTimeoutMs`) | `3000` | `27000` | spec: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. | `` · alpha: `requestTimeoutMs   = 27000` |
| `impl-alpha.md` | `queue_max_depth` (impl name `MAX_QUEUE_DEPTH`) | `10000` | `1250` | spec: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. | `` · alpha: `MAX_QUEUE_DEPTH    = 1250` |
| `impl-alpha.md` | `enable_legacy_auth` (impl name `enableLegacyAuth`) | `false` | `true` | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | `` · alpha: `enableLegacyAuth   = true` |
| `impl-beta.md` | `retry_backoff` (impl name `RETRY_BACKOFF_STRATEGY`) | `exponential, base 250ms` | `constant-interval` | spec: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. | `` · beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| `impl-beta.md` | `health_check_interval_s` (impl name `HEALTH_CHECK_INTERVAL_SECONDS`) | `15` | `75` | spec: `` | `health_check_interval_s` | 15 | Liveness probe cadence. | `` · beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| `runbook.md` | `max_retries` | `3` | `6` | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | `` · runbook: `the gateway will retry idempotent requests up to 6 times before shedding` |
| `runbook.md` | `tls_min_version` | `1.3` | `1.2` | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | `` · runbook: `set the load balancer TLS floor to 1.2 first` |

### Violated spec constraints (explicit)

1. `request_timeout_ms = 3000` — violated by Alpha (`27000`, i.e. 9× the contracted per-request upstream timeout).
2. `queue_max_depth = 10000` — violated by Alpha (`1250`; load shedding begins 8× earlier than contracted).
3. `enable_legacy_auth = false` with note **"Must stay false; scheduled for removal."** — violated by Alpha (`true`). This is the only spec row with an explicit imperative, so it is the strongest violation in the set.
4. `retry_backoff = exponential, base 250ms` with note **"Jitter enabled."** — violated by Beta, which selects `constant-interval`. Beta's base value is compliant (`RETRY_BACKOFF_BASE_MS=250`); the *strategy* is not, and a constant-interval strategy also forfeits the jitter property the spec note requires.
5. `health_check_interval_s = 15` — violated by Beta (`75`).
6. `max_retries = 3` ("Applies to idempotent requests only.") — violated by the runbook, which instructs operators to allow `up to 6 times` for exactly the idempotent-request case the spec bounds at 3.
7. `tls_min_version = 1.3` ("Hard floor for all listeners.") — violated by the runbook, which instructs lowering the load-balancer floor to `1.2` during rotation. The spec states no rotation-window exemption, and "hard floor for all listeners" covers the load-balancer listener.

Not violated by any of the three files: `db_pool_size_per_replica` (32), `log_level` (info), `idle_timeout_s` (90) — see rejected candidates for the two of these that superficially appeared to drift.

## Evidence

Exact fixture lines used for every claim above and below.

### `spec.md` (canonical rows quoted verbatim)

```
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
```

Header context: `This table is the canonical contract. Implementations and runbooks must match it.`

### `impl-alpha.md`

```
requestTimeoutMs   = 27000
maxRetries         = 3
retryBackoff       = exponential
retryBackoffBaseMs = 250
MAX_QUEUE_DEPTH    = 1250
tlsMinVersion      = 1.3
enableLegacyAuth   = true
logLevel           = info
dbPoolSizePerReplica = 32
```

Narrative used in reasoning: `values above are read at boot; there is no runtime override layer in Alpha. The legacy auth flag was toggled during the March incident bridge and has not been revisited since.` — this rules out an override layer that could restore the spec values at runtime, so the deployed values above are the effective values.

Compliant Alpha keys (no drift): `maxRetries = 3`; `retryBackoff = exponential` + `retryBackoffBaseMs = 250` (matches `exponential, base 250ms`); `tlsMinVersion = 1.3`; `logLevel = info`; `dbPoolSizePerReplica = 32`.

### `impl-beta.md`

```
REQUEST_TIMEOUT_MS=3000
MAX_RETRIES=3
RETRY_BACKOFF_STRATEGY=constant-interval
RETRY_BACKOFF_BASE_MS=250
QUEUE_MAX_DEPTH=10000
TLS_MIN_VERSION=1.3
HEALTH_CHECK_INTERVAL_SECONDS=75
ENABLE_LEGACY_AUTH=false
IDLE_TIMEOUT_TICKS=5400
LOG_LEVEL=info
DB_POOL_SIZE_PER_REPLICA=32
```

Narrative used in reasoning: `Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second. Backoff strategy names follow the retry library's enum (constant-interval, exponential, decorrelated-jitter).`

Compliant Beta keys (no drift): `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`, `RETRY_BACKOFF_BASE_MS=250`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `ENABLE_LEGACY_AUTH=false`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`.

### `runbook.md`

```
requests up to 6 times before shedding. Do not raise this further during
```
```
When rotating listener certificates, set the load balancer TLS floor to 1.2
first so older internal probes keep passing during the rotation window, then
```
```
Run all services at INFO verbosity in production. DEBUG is allowed only on a
single canary replica for up to one hour.
```
```
Capacity planning note: the two replicas hold 64 pooled connections in total.
```
```
Liveness probes are configured centrally; see the spec for cadence.
```

## Rejected candidates

Each of these looked like drift on a first pass; each is cleared, with the conversion or reasoning shown.

| # | File | Key | Why it looked like drift | Why it is not drift |
|---|---|---|---|---|
| R1 | `impl-beta.md` | `idle_timeout_s` vs `IDLE_TIMEOUT_TICKS` | Spec says `90`; Beta says `IDLE_TIMEOUT_TICKS=5400` — a 60× numeric mismatch. | **Unit conversion.** Beta's own note: `Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second.` Conversion: 5400 ticks ÷ 60 ticks/s = **90 s** = spec `90`. Exact match; the key name itself (`_TICKS` vs `_S`) signals the different unit. |
| R2 | `runbook.md` | `db_pool_size_per_replica` | Spec says `32`; runbook says `the two replicas hold 64 pooled connections in total` — 64 ≠ 32. | **Aggregate conversion.** Spec note: `Two replicas run in production.` and the key is explicitly *per replica*. Conversion: 32 per replica × 2 replicas = **64 total** = the runbook's aggregate. The runbook states a fleet total, not a per-replica value; it also labels it `Capacity planning note ... Alert thresholds are derived from that aggregate figure.` |
| R3 | `runbook.md` | `log_level` | Runbook permits `DEBUG` (`DEBUG is allowed only on a single canary replica for up to one hour`), while the spec value is `info` — looks like a permitted deviation from the contract. | **Spec wording clears it.** The spec note is `Production default.`, not a hard floor or a "must stay" imperative (contrast `enable_legacy_auth`: `Must stay false`). The runbook's primary instruction restates the default — `Run all services at INFO verbosity in production` — and scopes DEBUG to a bounded, single-replica, one-hour exception. A scoped temporary exception to a stated *default* is not a contradiction of that default. Flagged as a judgement call rather than an arithmetic one; a reader who treats `Production default` as binding on every replica at all times would classify this as drift. |
| R4 | `impl-alpha.md` | `health_check_interval_s`, `idle_timeout_s` | Both spec keys are absent from the Alpha excerpt, which could read as Alpha violating them. | **Absence, not contradiction.** Alpha's config block contains no line for either key, so there is no conflicting value to quote. The file is labelled `excerpt from deployed config`, so absence here does not establish either compliance or drift. Alpha's effective values for these two keys are **unmeasured** by this audit. |

### Near-miss numeric relations examined and *not* accepted as clearing conversions

- Alpha `MAX_QUEUE_DEPTH    = 1250` vs spec `10000`: 1250 × 8 = 10000 is arithmetically tidy, but no unit or aggregate basis exists in the fixtures — the spec defines the key in requests (`Requests beyond depth are shed.`), Alpha supplies no sharding, per-worker, or byte/bit note, and Alpha explicitly states `there is no runtime override layer in Alpha`. Rejected as coincidence; the drift stands as confirmed.
- Alpha `requestTimeoutMs   = 27000` vs spec `3000`: both are already in the same unit (milliseconds, per the key name in both files), so no conversion is available. 27000 ÷ 3000 = 9 with no factor-of-9 unit in the fixtures. Drift stands as confirmed.
- Beta `HEALTH_CHECK_INTERVAL_SECONDS=75` vs spec `15`: the tick note (`60 ticks per second`) was considered and does not apply — Beta scopes ticks to idle time only (`Beta counts idle time in scheduler ticks`), and the key is explicitly `_SECONDS`, matching the spec's `_s` unit. 75 ÷ 60 = 1.25 s, which matches nothing in the spec. Drift stands as confirmed.
- Beta `RETRY_BACKOFF_STRATEGY=constant-interval`: checked whether `constant-interval` could be a library alias for the spec's `exponential`. It cannot — Beta's own note lists them as distinct enum members: `(constant-interval, exponential, decorrelated-jitter)`, and `exponential` was available and not chosen. Drift stands as confirmed.

## Assumptions

1. The spec table is the sole authority; where an implementation's key name differs in casing or style (`requestTimeoutMs`, `REQUEST_TIMEOUT_MS`, `MAX_QUEUE_DEPTH`), it is mapped to the spec key of the same semantics. Name-style differences are treated as naming conventions, not drift.
2. Runbook prose that prescribes an operational value (retry count, TLS floor) is treated as configuration guidance in scope for drift, since the spec states `Implementations and runbooks must match it.`
3. Both implementation files are labelled `excerpt`, so keys absent from an excerpt are reported as unmeasured rather than as compliant or drifting (see R4). Alpha additionally omits `idle_timeout_s` and `health_check_interval_s`; Beta omits none of the ten spec keys.
4. Unit semantics are taken only from what the fixtures state (Beta's `60 ticks per second`; the spec's `Two replicas run in production.`). No external or assumed conversion factors were introduced.
5. R3 (`log_level` / canary DEBUG) rests on reading `Production default.` as a default rather than an invariant. This is a wording interpretation, not a measurement; the alternative reading is stated in the table so the call is auditable.
6. Not measured / out of scope: whether these files reflect the current live deployments; the runtime behaviour of either service; any file under `benchmarks/mission-vs-goal/` other than the four named fixtures and this artifact. No commands were run against any service. No claim about relative benchmark performance is made here.

## Stop Condition

Stopping is warranted because all of the following hold:

- This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280/tail-config-spec-drift-claude_code_goal_command.md` and contains the headings **Goal**, **Result**, **Evidence**, **Assumptions**, and **Stop Condition**.
- All four named fixtures were read in full, and no other file under `benchmarks/mission-vs-goal/` was opened, listed, or searched.
- All ten spec keys were checked against each of the three non-spec files; the confirmed-drift table carries file, key, spec value, actual value, and a verbatim quote for both sides of every row.
- A rejected-candidates section is present with the explicit conversion (R1: 5400 ÷ 60 = 90; R2: 32 × 2 = 64) or stated reasoning (R3, R4) that clears each one, plus the near-miss relations examined and declined.
- The violated spec constraints are stated explicitly and enumerated.
- Exactly one file was written; nothing was committed, pushed, installed, or fetched over the network.
