# tail-config-spec-drift — claude_code_goal_command (rep2)

## Goal

Audit configuration drift of `impl-alpha.md`, `impl-beta.md`, and `runbook.md`
against the canonical spec in `spec.md`, reporting every real contradiction with
quoted evidence, and explicitly rejecting the candidates that only *look*
contradictory but reconcile under unit or aggregate conversion.

Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`,
which states: "This table is the canonical contract. Implementations and
runbooks must match it."

## Result

**7 confirmed drifts** and **5 rejected candidates**.

### Confirmed drift table

| # | File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|---|
| 1 | `impl-alpha.md` | `request_timeout_ms` | 3000 | 27000 | spec: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. |`` — alpha: `requestTimeoutMs   = 27000` |
| 2 | `impl-alpha.md` | `queue_max_depth` | 10000 | 1250 | spec: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. |`` — alpha: `MAX_QUEUE_DEPTH    = 1250` |
| 3 | `impl-alpha.md` | `enable_legacy_auth` | false | true | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. |`` — alpha: `enableLegacyAuth   = true` |
| 4 | `impl-beta.md` | `retry_backoff` (strategy) | exponential | constant-interval | spec: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. |`` — beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| 5 | `impl-beta.md` | `health_check_interval_s` | 15 | 75 | spec: `` | `health_check_interval_s` | 15 | Liveness probe cadence. |`` — beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| 6 | `runbook.md` | `max_retries` | 3 | 6 | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. |`` — runbook: "the gateway will retry idempotent / requests up to 6 times before shedding" |
| 7 | `runbook.md` | `tls_min_version` | 1.3 | 1.2 | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. |`` — runbook: "set the load balancer TLS floor to 1.2 / first so older internal probes keep passing during the rotation window" |

### Rejected candidates (look contradictory, are not)

**R1 — `impl-beta.md` `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s` = 90.**
Looked suspicious because 5400 is 60× the spec value and appears in the same
position as a timeout. Cleared by unit conversion: beta states "Beta counts idle
time in scheduler ticks; the scheduler runs at 60 ticks per second."
Conversion: `5400 ticks ÷ 60 ticks/s = 90 s` = spec value. **Compliant.**

**R2 — `runbook.md` "the two replicas hold 64 pooled connections in total" vs
spec `db_pool_size_per_replica` = 32.** Looked suspicious because 64 ≠ 32.
Cleared by aggregate conversion: the spec key is *per replica* and its note says
"Two replicas run in production."
Conversion: `32 per replica × 2 replicas = 64 total` = the runbook figure.
The runbook explicitly frames it as an aggregate ("Alert thresholds are derived
from that aggregate figure"). **Compliant.**

**R3 — `impl-alpha.md` splitting `retryBackoff = exponential` and
`retryBackoffBaseMs = 250` into two keys** vs the spec's single cell
"exponential, base 250ms". Looked suspicious because no single alpha key matches
the spec string. Cleared by decomposition: the spec cell encodes two facts
(strategy = exponential, base = 250 ms), and alpha satisfies both
(`retryBackoff       = exponential`, `retryBackoffBaseMs = 250`). Representation
difference only. **Compliant.** (The same holds for beta's `RETRY_BACKOFF_BASE_MS=250`;
beta's *strategy* half is drift #4, its base half is compliant.)

**R4 — `runbook.md` "DEBUG is allowed only on a single canary replica for up to
one hour"** vs spec `log_level` = info. Looked suspicious because DEBUG ≠ info.
Cleared by reading the spec's own qualifier: the note for `log_level` is
"Production default." — i.e. the value is a default, not a hard floor (contrast
`tls_min_version`, whose note *is* "Hard floor for all listeners"). The runbook's
baseline instruction agrees with the spec: "Run all services at INFO verbosity in
production." The time-boxed, single-replica canary exception does not contradict
a stated default. **Not asserted as drift.**

**R5 — `runbook.md` health section not naming a cadence** vs spec
`health_check_interval_s` = 15. Looked suspicious as a possible omission.
Cleared because the runbook explicitly defers to the spec: "Liveness probes are
configured centrally; see the spec for cadence. If probes flap during deploys,
extend the grace period rather than the cadence." Deferral plus an instruction
*not* to change cadence is agreement, not contradiction. **Compliant.**

### Spec constraints violated

Stating explicitly which canonical constraints are broken:

1. **`request_timeout_ms` = 3000 ("Per-request upstream timeout")** — violated by
   Alpha, which runs a 27000 ms per-request timeout (9× the contract).
2. **`queue_max_depth` = 10000 ("Requests beyond depth are shed")** — violated by
   Alpha at 1250; Alpha sheds load at 12.5% of the contracted depth.
3. **`enable_legacy_auth` = false ("Must stay false; scheduled for removal")** —
   violated by Alpha at `true`. This is the strictest wording in the spec ("Must
   stay false") and is a security-relevant violation.
4. **`retry_backoff` = "exponential, base 250ms"** — violated by Beta's strategy
   `constant-interval`. Beta's base (250 ms) is correct; the *strategy* is not.
   Note: the spec also says "Jitter enabled." — Beta's enum
   (`constant-interval`, `exponential`, `decorrelated-jitter`) does not expose a
   separate jitter flag in the excerpt, so jitter state is **unmeasured**.
5. **`health_check_interval_s` = 15 ("Liveness probe cadence")** — violated by
   Beta at 75 s. The key name `HEALTH_CHECK_INTERVAL_SECONDS` states seconds
   explicitly, so no unit conversion clears it.
6. **`max_retries` = 3 ("Applies to idempotent requests only")** — violated by the
   runbook, which instructs operators to allow 6 retries on idempotent requests.
   Both spec and runbook scope this to idempotent requests, so the scopes match
   and only the count differs.
7. **`tls_min_version` = 1.3 ("Hard floor for all listeners")** — violated by the
   runbook, which instructs lowering the load-balancer floor to 1.2 during
   certificate rotation. "Hard floor for all listeners" admits no temporary
   exception, so the rotation window is a violation rather than a carve-out.

## Evidence

All quotes below are verbatim from the four named fixtures. No other file under
`benchmarks/mission-vs-goal/` was opened, read, grepped, or listed except the
output file itself.

**`spec.md` (canonical rows referenced):**
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

**`impl-alpha.md`:**
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
Alpha context quote: "there is no runtime override layer in Alpha" — so the
deployed values above are the effective values; drifts #1–#3 cannot be masked by
an override. Alpha context quote: "The legacy auth flag was toggled during the
March incident bridge and has not been revisited since." — corroborates #3 as a
live, unreverted state rather than a documentation error.

**`impl-beta.md`:**
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
Beta context quotes used for the R1 rejection and the #4 enum reading: "the
scheduler runs at 60 ticks per second" and "Backoff strategy names follow the
retry library's enum (`constant-interval`, `exponential`, `decorrelated-jitter`)".
Because `exponential` is an available enum value in Beta's own library, choosing
`constant-interval` is a real configuration difference, not a naming mismatch.

**`runbook.md`:**
- Retry: "the gateway will retry idempotent / requests up to 6 times before shedding. Do not raise this further during / incidents; shed load instead."
- TLS: "set the load balancer TLS floor to 1.2 / first so older internal probes keep passing during the rotation window, then / proceed with the rotation."
- Logging: "Run all services at INFO verbosity in production. DEBUG is allowed only on a / single canary replica for up to one hour."
- Database: "the two replicas hold 64 pooled connections in total. / Alert thresholds are derived from that aggregate figure."
- Health: "Liveness probes are configured centrally; see the spec for cadence."

### Machine-checkable findings block

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | max_retries | 3 | 3 | no-finding |
| impl-alpha.md | retry_backoff strategy | exponential | exponential | no-finding |
| impl-alpha.md | retry_backoff base ms | 250 | 250 | no-finding |
| impl-alpha.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-alpha.md | log_level | info | info | no-finding |
| impl-alpha.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| impl-beta.md | retry_backoff strategy | exponential | constant-interval | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | request_timeout_ms | 3000 | 3000 | no-finding |
| impl-beta.md | max_retries | 3 | 3 | no-finding |
| impl-beta.md | retry_backoff base ms | 250 | 250 | no-finding |
| impl-beta.md | queue_max_depth | 10000 | 10000 | no-finding |
| impl-beta.md | tls_min_version | 1.3 | 1.3 | no-finding |
| impl-beta.md | enable_legacy_auth | false | false | no-finding |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks / 60 = 90 | no-finding |
| impl-beta.md | log_level | info | info | no-finding |
| impl-beta.md | db_pool_size_per_replica | 32 | 32 | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |
| runbook.md | db_pool_size_per_replica | 32 per replica | 64 total / 2 = 32 | no-finding |
| runbook.md | log_level | info | INFO (DEBUG canary exception) | no-finding |
| runbook.md | health_check_interval_s | 15 | defers to spec | no-finding |

## Assumptions

1. **Key identity across naming styles is by semantics, not string match.** Alpha
   uses camelCase (`requestTimeoutMs`), Beta uses SCREAMING_SNAKE
   (`REQUEST_TIMEOUT_MS`), the spec uses snake_case (`request_timeout_ms`). I
   treat these as the same key. If the benchmark intended naming-convention
   divergence itself to be drift, that class is **not reported here**.
2. **Keys absent from an implementation are not asserted as drift.** Alpha's
   excerpt contains no `health_check_interval_s` and no `idle_timeout_s`. Both
   files are labeled "excerpt from deployed config", so absence may be excerpt
   truncation rather than misconfiguration. Their actual Alpha values are
   **unmeasured**; I filed no row for them rather than guess.
3. **`queue_max_depth` = 1250 in Alpha has no stated conversion basis.** 1250 × 8
   = 10000, so a per-shard reading would clear it — but Alpha's text names no
   shards, workers, or partitions, unlike Beta (which states its tick rate) and
   the runbook (which states "two replicas"). Both real rejections in this
   fixture supply their conversion factor in-text; Alpha supplies none, so I
   report #2 as drift. This is the lowest-confidence row in the drift set and the
   assumption is stated here so it can be checked.
4. **Spec note wording is normative and graded.** "Hard floor" and "Must stay
   false" are treated as absolute (driving #7 and #3), while "Production default"
   is treated as admitting a documented exception (driving rejection R4).
5. **No runtime state was observed.** Nothing was executed, deployed, or probed.
   Every claim rests on the four fixture texts only; actual running behavior of
   Alpha, Beta, and the gateway is **unmeasured**.
6. No commit, push, package install, or network access was performed. The only
   filesystem write is this artifact (plus creating its parent directory
   `run-output/2026-08-21-sens-normal/`, which did not exist).

## Stop Condition

Met when all of the following hold — each verified against this document:

- [x] Artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal/tail-config-spec-drift-claude_code_goal_command-rep2.md`.
- [x] Contains headings **Goal**, **Result**, **Evidence**, **Assumptions**, **Stop Condition**.
- [x] Contains a confirmed-drift table with file, key, spec value, actual value, and quoted evidence (7 rows).
- [x] Contains a rejected-candidates section with the conversion or reasoning clearing each one (R1–R5).
- [x] Contains an explicit statement of which spec constraints are violated (7 enumerated constraints).
- [x] Contains exactly one markdown table with the header `| location | key | expected | actual | verdict |`, one row per evaluated item, verdicts limited to `drift` / `no-finding` (25 rows: 7 `drift`, 18 `no-finding`).
- [x] Only the four named fixtures and this output file were accessed under `benchmarks/mission-vs-goal/`; no benchmark metadata, task definitions, scoring config, or answer keys were opened.
- [x] Exactly one task artifact written; no commits, pushes, installs, or network access.
- [x] No claim of benchmark superiority is made anywhere in this artifact.

Unmeasured items are labeled as such in **Assumptions** (Alpha's
`health_check_interval_s` and `idle_timeout_s`, Beta's jitter setting, and all
runtime behavior) rather than inferred.
