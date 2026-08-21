# tail-config-spec-drift — claude_code_goal_command (rep3)

## Goal

Audit configuration drift of `impl-alpha.md`, `impl-beta.md`, and `runbook.md` against the canonical spec `spec.md`, adjudicating exactly the 10 named items: report each contradiction with the key and both quoted values, and explicitly reject candidates that are consistent after unit or aggregate conversion, showing the conversion.

Sources read (only these four fixtures, plus this output file):

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`

## Result

7 confirmed drifts, 3 rejected candidates (all cleared by an explicit conversion or normalization).

### Findings (machine-checkable)

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks @ 60 ticks/s = 90 s | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval, base 250ms | drift |
| runbook.md | db_pool_size_per_replica | 32 | 64 total / 2 replicas = 32 per replica | no-finding |
| runbook.md | log_level | info | INFO | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |

### Confirmed drift table

| file | key | spec value | actual value | quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | 3000 | 27000 | spec: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. | `` — alpha: `requestTimeoutMs   = 27000` |
| impl-alpha.md | `queue_max_depth` | 10000 | 1250 | spec: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. | `` — alpha: `MAX_QUEUE_DEPTH    = 1250` |
| impl-alpha.md | `enable_legacy_auth` | false | true | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | `` — alpha: `enableLegacyAuth   = true` |
| impl-beta.md | `retry_backoff` | exponential, base 250ms | constant-interval (base 250ms) | spec: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. | `` — beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| impl-beta.md | `health_check_interval_s` | 15 | 75 | spec: `` | `health_check_interval_s` | 15 | Liveness probe cadence. | `` — beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| runbook.md | `max_retries` | 3 | 6 | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | `` — runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| runbook.md | `tls_min_version` | 1.3 | 1.2 | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | `` — runbook: "set the load balancer TLS floor to 1.2 first" |

## Evidence

Per-item reasoning, with the exact fixture strings.

### Confirmed

1. **impl-alpha.md / `request_timeout_ms`** — spec requires `3000`; Alpha's deployed config line is `requestTimeoutMs   = 27000`. Both are already in milliseconds (the spec key and Alpha's key both carry the `Ms` suffix), so no unit conversion applies; 27000 ms ≠ 3000 ms. 27000 is exactly 9× the spec value, but no aggregate basis (shards, attempts, replicas) is documented anywhere in Alpha, and the spec note pins the semantics as `Per-request upstream timeout.` Alpha's own note says "values above are read at boot; there is no runtime override layer in Alpha", so 27000 is the effective value. **Drift.**

2. **impl-alpha.md / `queue_max_depth`** — spec requires `10000`; Alpha has `MAX_QUEUE_DEPTH    = 1250`. 10000 / 1250 = 8, which would be cleared if Alpha documented 8 shards/instances each holding a share of the depth — but Alpha's fixture contains no sharding, per-instance, or fan-out statement of any kind. The only note is about boot-time reads and the legacy auth flag. With no documented divisor, the quoted value contradicts the spec. **Drift.** (Unmeasured: whether Alpha actually runs 8 shards in production — that fact is not in the fixtures, and I did not look outside them.)

3. **impl-alpha.md / `enable_legacy_auth`** — spec: `| `enable_legacy_auth` | false | Must stay false; scheduled for removal. |`; Alpha: `enableLegacyAuth   = true`. Boolean, no conversion possible. Alpha's note confirms the state is live and unreverted: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since." **Drift.**

4. **impl-beta.md / `retry_backoff`** — spec: `exponential, base 250ms`; Beta: `RETRY_BACKOFF_STRATEGY=constant-interval`. Beta's base is compliant (`RETRY_BACKOFF_BASE_MS=250` matches `base 250ms`), so the drift is the *strategy*, not the base. Beta's note removes any naming-alias defense: "Backoff strategy names follow the retry library's enum (`constant-interval`, `exponential`, `decorrelated-jitter`)" — `exponential` exists as a distinct enum member, so `constant-interval` is not a synonym for it. **Drift.**

5. **impl-beta.md / `health_check_interval_s`** — spec: `15`; Beta: `HEALTH_CHECK_INTERVAL_SECONDS=75`. Beta's key name states seconds explicitly, matching the spec's `_s` suffix, so the tick conversion documented for idle time does not apply here (the note scopes ticks narrowly: "Beta counts idle time in scheduler ticks"). 75 s ≠ 15 s. **Drift.**

6. **runbook.md / `max_retries`** — spec: `3` with note `Applies to idempotent requests only.`; runbook: "the gateway will retry idempotent requests up to 6 times before shedding." Same scope (idempotent requests), same unit (retry count), double the value. The runbook further entrenches it: "Do not raise this further during incidents." **Drift.**

7. **runbook.md / `tls_min_version`** — spec: `1.3`, note `Hard floor for all listeners.`; runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window." The spec calls 1.3 a *hard* floor with no rotation exemption, and the load balancer is a listener, so a documented procedure that drops the floor to 1.2 contradicts it. **Drift.** (The drift is in the runbook procedure; whether any listener currently runs at 1.2 is unmeasured — the fixtures contain no runtime TLS observation.)

### Rejected candidates (looked suspicious, cleared)

1. **impl-beta.md / `idle_timeout_s`** — *Why it looked wrong:* the spec says `| `idle_timeout_s` | 90 | Connection idle close. |` while Beta says `IDLE_TIMEOUT_TICKS=5400`, a 60× numeric mismatch. *Conversion that clears it:* Beta's note states "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." 5400 ticks ÷ 60 ticks/s = **90 s**, exactly the spec value. The key name itself (`_TICKS`, not `_S`) signals the different unit. **No finding.**

2. **runbook.md / `db_pool_size_per_replica`** — *Why it looked wrong:* the spec says `| `db_pool_size_per_replica` | 32 | Two replicas run in production. |` while the runbook says "the two replicas hold 64 pooled connections in total," and 64 ≠ 32. *Aggregate conversion that clears it:* the runbook figure is a total across replicas, the spec figure is per replica. 64 total ÷ 2 replicas = **32 per replica**, and the replica count agrees between both documents (spec note "Two replicas run in production" / runbook "the two replicas"). **No finding.** (Alpha and Beta also both carry the compliant literal `dbPoolSizePerReplica = 32` / `DB_POOL_SIZE_PER_REPLICA=32`.)

3. **runbook.md / `log_level`** — *Why it looked wrong:* the spec value is lowercase `info` while the runbook says "Run all services at INFO verbosity in production," a literal string mismatch; the DEBUG sentence also reads at a glance like a permitted deviation. *Normalization that clears it:* `INFO` is the same severity level as `info` differing only in case, so the production default matches. The DEBUG allowance is explicitly bounded and non-default — "DEBUG is allowed only on a single canary replica for up to one hour" — which is a scoped, time-boxed exception to a production *default*, not a redefinition of it; the spec note itself qualifies the value as `Production default.` Both implementations also carry the compliant value (`logLevel          = info`, `LOG_LEVEL=info`). **No finding.**

### Spec constraints violated

The following canonical `spec.md` rows are violated by at least one artifact:

- `request_timeout_ms = 3000` (`Per-request upstream timeout.`) — violated by impl-alpha.md (27000).
- `max_retries = 3` (`Applies to idempotent requests only.`) — violated by runbook.md (6).
- `retry_backoff = exponential, base 250ms` (`Jitter enabled.`) — violated by impl-beta.md (`constant-interval`).
- `queue_max_depth = 10000` (`Requests beyond depth are shed.`) — violated by impl-alpha.md (1250).
- `tls_min_version = 1.3` (`Hard floor for all listeners.`) — violated by runbook.md (1.2 during rotation).
- `health_check_interval_s = 15` (`Liveness probe cadence.`) — violated by impl-beta.md (75).
- `enable_legacy_auth = false` (`Must stay false; scheduled for removal.`) — violated by impl-alpha.md (true).

Spec rows **not** violated by any adjudicated item: `idle_timeout_s = 90`, `log_level = info`, `db_pool_size_per_replica = 32`.

By artifact: impl-alpha.md violates 3 constraints, impl-beta.md violates 2, runbook.md violates 2. The most severe by stated spec language are `enable_legacy_auth` (spec: "Must stay false") and `tls_min_version` (spec: "Hard floor for all listeners") because the spec wording admits no exception; severity beyond that wording is a judgement call and is **unmeasured** — the fixtures contain no incident, traffic, or risk data.

## Assumptions

1. **`spec.md` is the sole source of truth.** The runbook and both implementations are audited against it, never against each other. Where the runbook and spec disagree, the runbook is the defect.
2. **Key identity across naming conventions.** `requestTimeoutMs` (Alpha camelCase), `REQUEST_TIMEOUT_MS` (Beta SCREAMING_SNAKE), and spec `request_timeout_ms` are the same key; likewise `MAX_QUEUE_DEPTH` ↔ `queue_max_depth`, `enableLegacyAuth` ↔ `enable_legacy_auth`, `HEALTH_CHECK_INTERVAL_SECONDS` ↔ `health_check_interval_s`, `IDLE_TIMEOUT_TICKS` ↔ `idle_timeout_s`, `RETRY_BACKOFF_STRATEGY` ↔ `retry_backoff`. Prose statements in the runbook are mapped to the spec key they describe by subject matter (retry count → `max_retries`, TLS floor → `tls_min_version`, verbosity → `log_level`, pooled connections → `db_pool_size_per_replica`).
3. **Only conversions documented inside the fixtures are accepted.** The tick rate (60 ticks/s) and the replica count (two) are stated in the fixtures, so those conversions are applied. No undocumented divisor is invented to rescue `queue_max_depth` (÷8) or `request_timeout_ms` (÷9) in Alpha.
4. **Case-only differences in enumerated string values** (`INFO` vs `info`) are treated as the same value; differences in enum *member* (`constant-interval` vs `exponential`) are not.
5. **The fixtures describe effective/deployed state.** Alpha's "there is no runtime override layer in Alpha" and Beta's "effective configuration (excerpt from deployed config)" headers are taken at face value; I performed no runtime observation. Actual running values are **unmeasured**.
6. **Scope.** Only the 10 named items were adjudicated. Keys present in a fixture but not on the item list (e.g. Alpha's `maxRetries = 3`, Alpha's `tlsMinVersion = 1.3`, Beta's `REQUEST_TIMEOUT_MS=3000`) were read as context and appear compliant, but they carry no findings row. The runbook's "Health" section defers to the spec ("Liveness probes are configured centrally; see the spec for cadence") and therefore asserts no competing value.

## Stop Condition

Met. All of the following hold:

- This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-config-spec-drift-claude_code_goal_command-rep3.md` and is the only file written by this run.
- It contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition.
- It contains exactly one markdown table with the header `| location | key | expected | actual | verdict |`, with 10 rows — one per named item — using the exact required location/key strings and only the values `drift` and `no-finding`.
- It contains a confirmed-drift table (file, key, spec value, actual value, quoted evidence), a rejected-candidates section showing the conversion or normalization that clears each of the three, and an explicit list of violated spec constraints.
- Only the four named fixtures and this output file were opened; no benchmark metadata, task definition, scoring configuration, or answer key was read. No commit, push, package install, or network access occurred. No claim of benchmark superiority is made.
