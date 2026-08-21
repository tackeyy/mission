# tail-config-spec-drift — configuration drift audit

Task id: `tail-config-spec-drift` / Arm: `claude_code_goal_command`

## Goal

Audit the two implementation excerpts and the operations runbook against the
canonical spec (`spec.md`), report every place where they contradict the spec
with the key and both values quoted, and explicitly reject candidates that only
look contradictory but reconcile after a unit or aggregate conversion.

Sources of truth and inputs read (exactly these four files, nothing else under
`benchmarks/mission-vs-goal/` was opened):

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md` (canonical)
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`

## Result

7 confirmed drifts, 3 rejected candidates. All 10 adjudicated items are listed
in the findings table below.

Confirmed drift: `impl-alpha.md` (`request_timeout_ms`, `queue_max_depth`,
`enable_legacy_auth`), `impl-beta.md` (`retry_backoff`,
`health_check_interval_s`), `runbook.md` (`max_retries`, `tls_min_version`).

Rejected (compliant after conversion / normalization): `impl-beta.md`
(`idle_timeout_s`), `runbook.md` (`db_pool_size_per_replica`, `log_level`).

### Confirmed drift table

Pipes inside quoted spec rows are escaped as `\|` so the table renders; the
unescaped spec rows appear verbatim in the Evidence section.

| file | key | spec value | actual value | quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | 3000 | 27000 | spec: `` \| `request_timeout_ms` \| 3000 \| Per-request upstream timeout. \| `` — alpha: `requestTimeoutMs   = 27000` |
| impl-alpha.md | `queue_max_depth` | 10000 | 1250 | spec: `` \| `queue_max_depth` \| 10000 \| Requests beyond depth are shed. \| `` — alpha: `MAX_QUEUE_DEPTH    = 1250` |
| impl-alpha.md | `enable_legacy_auth` | false | true | spec: `` \| `enable_legacy_auth` \| false \| Must stay false; scheduled for removal. \| `` — alpha: `enableLegacyAuth   = true` |
| impl-beta.md | `retry_backoff` | exponential, base 250ms | constant-interval, base 250ms | spec: `` \| `retry_backoff` \| exponential, base 250ms \| Jitter enabled. \| `` — beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| impl-beta.md | `health_check_interval_s` | 15 | 75 | spec: `` \| `health_check_interval_s` \| 15 \| Liveness probe cadence. \| `` — beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| runbook.md | `max_retries` | 3 | 6 | spec: `` \| `max_retries` \| 3 \| Applies to idempotent requests only. \| `` — runbook: `requests up to 6 times before shedding` (full sentence: "the gateway will retry idempotent requests up to 6 times before shedding") |
| runbook.md | `tls_min_version` | 1.3 | 1.2 | spec: `` \| `tls_min_version` \| 1.3 \| Hard floor for all listeners. \| `` — runbook: `set the load balancer TLS floor to 1.2 first` |

### Violated spec constraints (explicit)

1. `request_timeout_ms = 3000` — violated by Alpha (27000, 9× the contract).
2. `queue_max_depth = 10000` — violated by Alpha (1250; load is shed 8× earlier
   than the contract allows).
3. `enable_legacy_auth = false` with the note "Must stay false; scheduled for
   removal" — violated by Alpha (`true`). This is the one key the spec marks as
   a hard must-stay constraint, so it is the highest-severity violation.
4. `retry_backoff = exponential, base 250ms` — violated by Beta, which uses the
   `constant-interval` strategy. The base (250ms) matches, so only the strategy
   half of the constraint is violated.
5. `health_check_interval_s = 15` — violated by Beta (75).
6. `max_retries = 3` — violated by the runbook's operational guidance (6). The
   runbook's own scope note ("idempotent requests") matches the spec's note, so
   the contradiction is in the number, not the scope.
7. `tls_min_version = 1.3`, described as a "Hard floor for all listeners" —
   violated by the runbook instructing operators to set the load balancer floor
   to 1.2 during certificate rotation. The spec admits no rotation-window
   exception, so the runbook procedure contradicts the hard floor.

Not violated: `idle_timeout_s`, `db_pool_size_per_replica`, `log_level` (see
Rejected candidates).

### Rejected candidates

Each of these looked contradictory on a literal value comparison but is
consistent with the spec once the stated conversion is applied.

**1. `impl-beta.md` / `idle_timeout_s` — rejected.**
Why it looked suspicious: the spec says `idle_timeout_s` | 90, and Beta reads
`IDLE_TIMEOUT_TICKS=5400` — a 60× apparent mismatch.
Conversion that clears it: Beta states `Beta counts idle time in scheduler
ticks; the scheduler runs at 60 ticks per second`. Therefore
5400 ticks ÷ 60 ticks/s = 90 s, exactly the spec value. The key name itself
signals the unit (`_TICKS`, not `_S`). Compliant.

**2. `runbook.md` / `db_pool_size_per_replica` — rejected.**
Why it looked suspicious: the spec says `db_pool_size_per_replica` | 32, and the
runbook says `the two replicas hold 64 pooled connections in total` — 64 vs 32.
Conversion that clears it: the runbook figure is an aggregate across replicas,
while the spec figure is per replica. The spec's own note confirms the replica
count: `Two replicas run in production.` So 64 total ÷ 2 replicas = 32 per
replica, exactly the spec value. Both implementations independently confirm the
per-replica figure (`dbPoolSizePerReplica = 32`, `DB_POOL_SIZE_PER_REPLICA=32`).
Compliant.

**3. `runbook.md` / `log_level` — rejected.**
Why it looked suspicious: the spec says `log_level` | info (lowercase), and the
runbook says `Run all services at INFO verbosity in production` (uppercase), plus
it mentions DEBUG, which is not the spec value.
Reasoning that clears it: `INFO` and `info` are the same log level differing only
in letter case — a normalization, not a value difference. The DEBUG mention is
not a contradiction of the production default either: the runbook scopes it as
`DEBUG is allowed only on a single canary replica for up to one hour`, i.e. a
bounded, non-production-default exception, and it explicitly reaffirms INFO for
`all services in production`. Both implementations also read `logLevel = info` /
`LOG_LEVEL=info`. Compliant.

Note on a fourth near-candidate that is NOT rejected: for
`impl-alpha.md` / `queue_max_depth`, 10000 ÷ 8 = 1250 is arithmetically tidy and
could tempt a bits-vs-bytes style reading. I did not accept that reconciliation
because nothing in `impl-alpha.md` or `spec.md` states any per-shard, per-unit,
or aggregate basis for the queue depth — the spec defines it in requests
(`Requests beyond depth are shed.`) and Alpha's own note says `values above are
read at boot; there is no runtime override layer in Alpha`. An unstated divisor
is not evidence, so this stays a confirmed drift.

## Evidence

Verbatim lines from the fixtures backing every claim above.

`spec.md` (canonical rows, quoted exactly):

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

`impl-alpha.md`:

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

Alpha prose: `values above are read at boot; there is no runtime override layer
in Alpha. The legacy auth flag was toggled during the March incident bridge and
has not been revisited since.` — this confirms `enableLegacyAuth = true` is the
live effective value, not a transient override.

`impl-beta.md`:

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

Beta prose: `Beta counts idle time in scheduler ticks; the scheduler runs at 60
ticks per second. Backoff strategy names follow the retry library's enum
(`constant-interval`, `exponential`, `decorrelated-jitter`).` — the enum listing
is what makes `constant-interval` a real, distinct strategy from `exponential`
rather than a synonym, so the `retry_backoff` drift is not a naming artifact.

`runbook.md`:

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
Alert thresholds are derived from that aggregate figure.
```

```
Liveness probes are configured centrally; see the spec for cadence. If probes
flap during deploys, extend the grace period rather than the cadence.
```

The last quote matters for scoping: the runbook defers `health_check_interval_s`
to the spec rather than restating a number, so the interval drift is attributable
to `impl-beta.md` only, not to the runbook.

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

## Assumptions

1. `spec.md` is the sole source of truth; where an implementation or the runbook
   differs, the implementation/runbook is the defect. Stated in `spec.md`: `This
   table is the canonical contract. Implementations and runbooks must match it.`
2. Key identity across naming styles is by normalized name — `requestTimeoutMs`,
   `REQUEST_TIMEOUT_MS`, and `request_timeout_ms` are the same key; likewise
   `MAX_QUEUE_DEPTH` ↔ `queue_max_depth` and `HEALTH_CHECK_INTERVAL_SECONDS` ↔
   `health_check_interval_s`. Without this assumption no comparison is possible.
3. Log-level case (`INFO` vs `info`) is presentational, not a value difference.
4. The runbook's prose guidance is treated as a configuration assertion about the
   same keys the spec governs (retry count, TLS floor, log level, pool size), so
   it can drift from the spec. If the runbook were treated as non-normative
   narrative, the two runbook drifts would instead be documentation defects; the
   task prompt directs auditing the runbook for contradictions, so they are
   reported as drift.
5. Beta's tick rate (60 ticks/s) is accurate as stated in the fixture; it was not
   independently verified against any implementation.
6. Absence of a key in a file is not treated as drift. `impl-alpha.md` does not
   list `health_check_interval_s`, `idle_timeout_s`, or a `max_retries`-scoped
   note, and `impl-beta.md` does not list a runbook-style TLS procedure; these
   omissions were not adjudicated because they are not in the item list and the
   fixtures are labeled as excerpts (`excerpt from deployed config`).

### Unmeasured

- No configuration was executed, loaded, or validated against a running service.
  All findings are static comparisons of the four fixture documents.
- Runtime effective values, deployment history, and whether the drifts are
  currently live in any real environment are **unmeasured**.
- Severity/impact of each drift (e.g. blast radius of `enable_legacy_auth=true`)
  is **unmeasured**; the ordering commentary is a reading of the spec's own notes,
  not a measurement.
- Nothing outside the four named fixtures was read, so any drift recorded
  elsewhere in the repository is **unmeasured**.

## Stop Condition

Met. All of the following hold:

- This artifact exists at
  `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-config-spec-drift-claude_code_goal_command-rep1.md`
  and contains the headings Goal, Result, Evidence, Assumptions, Stop Condition.
- A confirmed-drift table with file, key, spec value, actual value, and quoted
  evidence is present.
- A rejected-candidates section is present, with the conversion or reasoning that
  clears each of the three rejected items.
- Violated spec constraints are stated explicitly and enumerated.
- Exactly one findings table with the header
  `| location | key | expected | actual | verdict |` is present, with exactly one
  row per adjudicated item (10 rows) and verdicts limited to `drift` /
  `no-finding`.
- No commits, pushes, installs, or network access were performed; exactly one
  file was written and only the four named fixtures were read. No claim of
  benchmark superiority is made.
