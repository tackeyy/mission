# tail-config-spec-drift — configuration drift audit (arm: claude_code_goal_command)

## Goal

Audit configuration drift of `impl-alpha.md`, `impl-beta.md`, and `runbook.md` against the
canonical `spec.md`, adjudicating exactly the 10 named items: report every real contradiction
with quoted key/value evidence, and explicitly reject the candidates that only *look*
contradictory but reconcile after a unit or aggregate conversion.

Sources read (exactly these four, per prompt):

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`

## Result

7 confirmed drifts, 3 rejected candidates. All 10 adjudicated items are covered.

### Confirmed drift table

| file | key | spec value | actual value | quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | `3000` | `27000` | spec: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. | ``; alpha: `requestTimeoutMs   = 27000` |
| impl-alpha.md | `queue_max_depth` | `10000` | `1250` | spec: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. | ``; alpha: `MAX_QUEUE_DEPTH    = 1250` |
| impl-alpha.md | `enable_legacy_auth` | `false` | `true` | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | ``; alpha: `enableLegacyAuth   = true` |
| impl-beta.md | `retry_backoff` | `exponential, base 250ms` | `constant-interval` | spec: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. | ``; beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| impl-beta.md | `health_check_interval_s` | `15` | `75` | spec: `` | `health_check_interval_s` | 15 | Liveness probe cadence. | ``; beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| runbook.md | `max_retries` | `3` | `6` | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | ``; runbook: `the gateway will retry idempotent requests up to 6 times before shedding` |
| runbook.md | `tls_min_version` | `1.3` | `1.2` | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | ``; runbook: `set the load balancer TLS floor to 1.2 first` |

### Rejected candidates (look contradictory, are not)

| file | key | apparent conflict | conversion / reasoning that clears it |
|---|---|---|---|
| impl-beta.md | `idle_timeout_s` | spec `90` vs beta `5400` — a 60× numeric gap | Beta stores ticks, not seconds: `IDLE_TIMEOUT_TICKS=5400` and the file states `the scheduler runs at 60 ticks per second`. Conversion: 5400 ticks ÷ 60 ticks/s = **90 s** = spec's `90`. Compliant. |
| runbook.md | `db_pool_size_per_replica` | spec `32` per replica vs runbook `64` — looks doubled | The runbook figure is an **aggregate**, not per-replica: `the two replicas hold 64 pooled connections in total`, and spec's note says `Two replicas run in production`. Conversion: 32 conn/replica × 2 replicas = **64 total**. Compliant. |
| runbook.md | `log_level` | spec `info` (lowercase) vs runbook `INFO`, plus a `DEBUG` allowance | Case is the only textual difference (`INFO` vs `info`) and denotes the same level; the runbook mandates it in production: `Run all services at INFO verbosity in production`. The DEBUG allowance is scoped and does not contradict spec's `Production default`: `DEBUG is allowed only on a single canary replica for up to one hour`. Compliant. |

### Violated spec constraints (explicit)

The 7 confirmed drifts violate these canonical rows of `spec.md`:

1. `request_timeout_ms = 3000` — Alpha runs 9× longer (27000 ms = 27 s), so the "Per-request upstream timeout" contract is not enforced.
2. `queue_max_depth = 10000` — Alpha sheds at 1250, i.e. load shedding begins at 12.5% of the contracted depth.
3. `enable_legacy_auth = false` ("Must stay false; scheduled for removal") — Alpha has it enabled; this is the security-relevant violation.
4. `retry_backoff = exponential, base 250ms` ("Jitter enabled") — Beta uses `constant-interval`, so neither the exponential growth nor the jitter requirement holds, despite Beta's base being correct (`RETRY_BACKOFF_BASE_MS=250`).
5. `health_check_interval_s = 15` — Beta probes every 75 s, 5× slower liveness detection.
6. `max_retries = 3` — The runbook instructs operators to allow 6 retries, doubling the contracted retry budget.
7. `tls_min_version = 1.3` ("Hard floor for all listeners") — The runbook instructs lowering the load-balancer floor to 1.2, which the word "hard floor" forbids even transiently.

## Evidence

Machine-checkable findings block (one row per adjudicated item):

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks ÷ 60 ticks/s = 90 s | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval | drift |
| runbook.md | db_pool_size_per_replica | 32 | 64 total ÷ 2 replicas = 32 | no-finding |
| runbook.md | log_level | info | INFO | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |

Verbatim source lines backing each row:

- `spec.md` canonical rows: `| `request_timeout_ms` | 3000 |`, `| `max_retries` | 3 |`, `| `retry_backoff` | exponential, base 250ms |`, `| `queue_max_depth` | 10000 |`, `| `tls_min_version` | 1.3 |`, `| `health_check_interval_s` | 15 |`, `| `enable_legacy_auth` | false |`, `| `idle_timeout_s` | 90 |`, `| `log_level` | info |`, `| `db_pool_size_per_replica` | 32 |` (note: `Two replicas run in production.`). Header states: `This table is the canonical contract. Implementations and runbooks must match it.`
- `impl-alpha.md`: `requestTimeoutMs   = 27000`, `maxRetries         = 3`, `retryBackoff       = exponential`, `retryBackoffBaseMs = 250`, `MAX_QUEUE_DEPTH    = 1250`, `tlsMinVersion      = 1.3`, `enableLegacyAuth   = true`, `logLevel           = info`, `dbPoolSizePerReplica = 32`. Alpha has no override layer: `there is no runtime override layer in Alpha`, and the flag state is acknowledged: `The legacy auth flag was toggled during the March incident bridge and has not been revisited since.`
- `impl-beta.md`: `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`, `RETRY_BACKOFF_STRATEGY=constant-interval`, `RETRY_BACKOFF_BASE_MS=250`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `HEALTH_CHECK_INTERVAL_SECONDS=75`, `ENABLE_LEGACY_AUTH=false`, `IDLE_TIMEOUT_TICKS=5400`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`. Unit basis: `Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second.` Enum basis: `Backoff strategy names follow the retry library's enum (`constant-interval`, `exponential`, `decorrelated-jitter`).`
- `runbook.md`: `the gateway will retry idempotent requests up to 6 times before shedding`; `set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window`; `Run all services at INFO verbosity in production. DEBUG is allowed only on a single canary replica for up to one hour.`; `the two replicas hold 64 pooled connections in total`; `Liveness probes are configured centrally; see the spec for cadence.`

Reasoning notes for the closer calls:

- **Beta `retry_backoff` is drift despite a correct base.** `RETRY_BACKOFF_BASE_MS=250` matches spec's `base 250ms`, which makes the row look partially compliant, but the strategy name is drawn from the library enum that also offers `exponential`; selecting `constant-interval` is a deliberate, non-equivalent choice. There is no conversion under which a constant interval equals exponential growth with jitter.
- **Beta `health_check_interval_s` gets no tick conversion.** The tick note in `impl-beta.md` is scoped to idle time only (`Beta counts idle time in scheduler ticks`), and the key itself is named `HEALTH_CHECK_INTERVAL_SECONDS`, i.e. already seconds. 75 s vs 15 s is a same-unit comparison. The runbook also defers to spec here (`see the spec for cadence`), so 75 has no independent authority.
- **Alpha `queue_max_depth` gets no aggregate conversion.** 10000 ÷ 1250 = 8, but no fixture states that Alpha runs 8 shards, partitions, or instances of the queue; `impl-alpha.md` presents `MAX_QUEUE_DEPTH` as the effective deployed value with no override layer. The aggregate reading is unsupported, unlike the db-pool case where the runbook explicitly says `in total` and spec explicitly says two replicas.
- **Alpha `request_timeout_ms` gets no unit conversion.** Both spec and Alpha express the value in milliseconds (`request_timeout_ms` / `requestTimeoutMs`), and 27000 is not a ms/s restatement of 3000 (3000 ms = 3 s; 27000 ms = 27 s).

## Assumptions

1. `spec.md` is the sole source of truth; where the runbook and spec disagree, the runbook is the drifting artifact (spec: `Implementations and runbooks must match it.`).
2. Casing and naming style differences (`logLevel` / `LOG_LEVEL` / `log_level`; `INFO` / `info`) are treated as presentation, not value, differences.
3. The runbook's TLS instruction is read as a contradiction of a stated *hard* floor even though it is described as temporary (`during the rotation window`). Under a reading where transient exceptions are permitted, this row would flip; the spec's wording `Hard floor for all listeners` is the basis for calling it drift.
4. The runbook's DEBUG canary allowance is read as an operational exception to a *default*, not a redefinition of it, because spec's note is `Production default` rather than a hard constraint. This is the mirror-image judgment of assumption 3 and rests on the differing spec wording.
5. Keys present in a fixture but not in the 10 adjudicated items (e.g. Alpha `maxRetries = 3`, Beta `REQUEST_TIMEOUT_MS=3000`) were read for context and found consistent with spec, but are not given findings rows because the prompt fixes the row set.

### Unmeasured

- **Runtime behaviour is unmeasured.** No service was executed, no configuration was loaded, and no probe/timeout/retry behaviour was observed. Every claim here is a static text comparison of the four fixture files.
- **Real-world impact is unmeasured.** Statements such as "5× slower liveness detection" are arithmetic on the configured values, not observed incident or latency data.
- **Fixture completeness is unmeasured.** Only the four named files were read; whether other configuration layers exist for Alpha or Beta outside these excerpts was not checked (Alpha's excerpt asserts no override layer, but that assertion was not independently verified).
- **No benchmark comparison is made.** This artifact makes no claim about the relative performance of this arm versus any other.

## Stop Condition

Satisfied when all of the following hold, and they do:

1. This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-config-spec-drift-claude_code_goal_command-rep2.md` — the only file written this run.
2. It contains the headings Goal, Result, Evidence, Assumptions, Stop Condition. ✔
3. It contains a confirmed-drift table with file, key, spec value, actual value, and quoted evidence. ✔ (7 rows)
4. It contains a rejected-candidates section showing the conversion or reasoning that clears each one. ✔ (3 rows, each with the explicit conversion)
5. It contains an explicit statement of which spec constraints are violated. ✔ (7 enumerated constraints)
6. It contains exactly one table with the header `| location | key | expected | actual | verdict |`, with one row per adjudicated item and `verdict` values restricted to `drift` / `no-finding`. ✔ (10 rows: 7 `drift`, 3 `no-finding`)
7. No commits, pushes, installs, or network access were performed; no file under `benchmarks/mission-vs-goal/` was opened other than the four named fixtures and this output file. ✔
