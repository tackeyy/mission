# tail-config-spec-drift — claude_code_goal_command

## Goal

Audit configuration drift of `impl-alpha.md`, `impl-beta.md`, and `runbook.md` against the canonical
spec in `spec.md`, producing:

1. a confirmed-drift table (file, key, spec value, actual value, quoted evidence),
2. a rejected-candidates section showing the conversion or reasoning that clears each near-miss, and
3. an explicit statement of which spec constraints are violated.

Scope limits observed: only the four fixture files named in the task prompt were read, plus this
output file. No other path under `benchmarks/mission-vs-goal/` was opened, listed, or grepped. No
commits, pushes, installs, or network access. Exactly one artifact written (this file).

## Result

**7 confirmed drifts** across 3 files, violating **7 distinct spec keys**. **6 candidates rejected**
as consistent after unit/aggregate conversion or notation normalization.

Breakdown by file:

| File | Confirmed drifts |
|---|---|
| `impl-alpha.md` | 3 (`request_timeout_ms`, `queue_max_depth`, `enable_legacy_auth`) |
| `impl-beta.md` | 2 (`retry_backoff`, `health_check_interval_s`) |
| `runbook.md` | 2 (`max_retries`, `tls_min_version`) |

### Confirmed drift table

| # | File | Key | Spec value | Actual value | Quoted evidence (spec / actual) |
|---|---|---|---|---|---|
| D1 | `impl-alpha.md` | `request_timeout_ms` | `3000` | `27000` | spec: `` | `request_timeout_ms` | 3000 | Per-request upstream timeout. | `` — alpha: `requestTimeoutMs   = 27000` |
| D2 | `impl-alpha.md` | `queue_max_depth` | `10000` | `1250` | spec: `` | `queue_max_depth` | 10000 | Requests beyond depth are shed. | `` — alpha: `MAX_QUEUE_DEPTH    = 1250` |
| D3 | `impl-alpha.md` | `enable_legacy_auth` | `false` | `true` | spec: `` | `enable_legacy_auth` | false | Must stay false; scheduled for removal. | `` — alpha: `enableLegacyAuth   = true` |
| D4 | `impl-beta.md` | `retry_backoff` | `exponential, base 250ms` | `constant-interval` (base 250ms) | spec: `` | `retry_backoff` | exponential, base 250ms | Jitter enabled. | `` — beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| D5 | `impl-beta.md` | `health_check_interval_s` | `15` | `75` | spec: `` | `health_check_interval_s` | 15 | Liveness probe cadence. | `` — beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| D6 | `runbook.md` | `max_retries` | `3` | `6` | spec: `` | `max_retries` | 3 | Applies to idempotent requests only. | `` — runbook: `the gateway will retry idempotent` / `requests up to 6 times before shedding` |
| D7 | `runbook.md` | `tls_min_version` | `1.3` | `1.2` | spec: `` | `tls_min_version` | 1.3 | Hard floor for all listeners. | `` — runbook: `set the load balancer TLS floor to 1.2` / `first so older internal probes keep passing during the rotation window` |

### Violated spec constraints (explicit)

The following canonical spec rows are contradicted by at least one artifact:

1. `request_timeout_ms = 3000` — violated by Alpha (9× too high: 27000 ms).
2. `queue_max_depth = 10000` — violated by Alpha (1250, i.e. 8× too shallow; shed threshold fires far earlier than contracted).
3. `enable_legacy_auth = false` — violated by Alpha (`true`). The spec's note `Must stay false; scheduled for removal.` makes this an explicit, non-negotiable constraint, so this is the most severe of the seven.
4. `retry_backoff = exponential, base 250ms` — violated by Beta on the *strategy* component (`constant-interval`); the *base* component (250 ms) is compliant.
5. `health_check_interval_s = 15` — violated by Beta (75 s, 5× slower liveness cadence).
6. `max_retries = 3` — violated by the runbook's operator guidance (`up to 6 times`).
7. `tls_min_version = 1.3` — violated by the runbook's rotation procedure (`floor to 1.2`). The spec note `Hard floor for all listeners.` admits no temporary-window exception, so the runbook's "during the rotation window" framing does not clear it.

Spec rows **not** violated anywhere: `idle_timeout_s` and `log_level` and `db_pool_size_per_replica`
(see rejected candidates R1–R3).

## Evidence

### Per-key cross-file matrix

Values are quoted verbatim from the fixtures. `—` means the key does not appear in that file.

| Spec key | Spec | impl-alpha | impl-beta | runbook |
|---|---|---|---|---|
| `request_timeout_ms` | 3000 | `requestTimeoutMs   = 27000` ❌ | `REQUEST_TIMEOUT_MS=3000` ✅ | — |
| `max_retries` | 3 | `maxRetries         = 3` ✅ | `MAX_RETRIES=3` ✅ | `up to 6 times` ❌ |
| `retry_backoff` | exponential, base 250ms | `retryBackoff       = exponential` + `retryBackoffBaseMs = 250` ✅ | `RETRY_BACKOFF_STRATEGY=constant-interval` ❌ + `RETRY_BACKOFF_BASE_MS=250` ✅ | — |
| `queue_max_depth` | 10000 | `MAX_QUEUE_DEPTH    = 1250` ❌ | `QUEUE_MAX_DEPTH=10000` ✅ | — |
| `tls_min_version` | 1.3 | `tlsMinVersion      = 1.3` ✅ | `TLS_MIN_VERSION=1.3` ✅ | `TLS floor to 1.2` ❌ |
| `health_check_interval_s` | 15 | — (absent) | `HEALTH_CHECK_INTERVAL_SECONDS=75` ❌ | `see the spec for cadence` ✅ |
| `enable_legacy_auth` | false | `enableLegacyAuth   = true` ❌ | `ENABLE_LEGACY_AUTH=false` ✅ | — |
| `idle_timeout_s` | 90 | — (absent) | `IDLE_TIMEOUT_TICKS=5400` ✅ (after conversion, R1) | — |
| `log_level` | info | `logLevel           = info` ✅ | `LOG_LEVEL=info` ✅ | `INFO verbosity` ✅ (R3) |
| `db_pool_size_per_replica` | 32 | `dbPoolSizePerReplica = 32` ✅ | `DB_POOL_SIZE_PER_REPLICA=32` ✅ | `64 pooled connections in total` ✅ (after conversion, R2) |

### Rejected candidates

Each entry below looked like drift on a literal string comparison but is cleared by an explicit
conversion or notation rule stated in the fixtures.

**R1 — `impl-beta.md` `idle_timeout_s`: `IDLE_TIMEOUT_TICKS=5400` vs spec `90`.**
Why it looked suspicious: the literal `5400` is 60× the spec's `90`, and the key name differs
(`_TICKS` vs `_s`).
Conversion that clears it: Beta states `Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per` / `second.` Therefore 5400 ticks ÷ 60 ticks/s = **90 s**, which equals the spec's
`` | `idle_timeout_s` | 90 | Connection idle close. | ``. **Consistent — not drift.**

**R2 — `runbook.md` `db_pool_size_per_replica`: `64 pooled connections in total` vs spec `32`.**
Why it looked suspicious: 64 is literally double the spec's 32.
Aggregate conversion that clears it: the runbook says `the two replicas hold 64 pooled connections in total`, and the spec note says `Two replicas run in production.` Therefore 64 total ÷ 2 replicas =
**32 per replica**, which equals the spec's `` | `db_pool_size_per_replica` | 32 | ``. The runbook
figure is an aggregate; the spec figure is per-replica. **Consistent — not drift.**

**R3 — `runbook.md` `log_level`: `INFO` vs spec `info`.**
Why it looked suspicious: the string differs in case from the spec value.
Reasoning that clears it: the runbook says `Run all services at INFO verbosity in production.` Log
level names are case-insensitive by universal convention and both implementations write the lowercase
form (`logLevel           = info`, `LOG_LEVEL=info`). The runbook's DEBUG allowance is explicitly
scoped and bounded — `DEBUG is allowed only on a` / `single canary replica for up to one hour.` — so it
describes a time-boxed exception on one replica, not a change to the production default. **Consistent
— not drift.** (Caveat: whether the gateway's log parser is truly case-insensitive is *unmeasured* —
no fixture states the parser's behavior. The rejection rests on convention plus both implementations
agreeing with the spec.)

**R4 — `runbook.md` `health_check_interval_s`: no numeric value at all.**
Why it looked suspicious: given Beta's `HEALTH_CHECK_INTERVAL_SECONDS=75` drift, one expects the
runbook to also carry a bad cadence.
Reasoning that clears it: the runbook explicitly defers — `Liveness probes are configured centrally; see the spec for cadence.` — and its remediation advice steers away from touching cadence: `If probes` / `flap during deploys, extend the grace period rather than the cadence.` Deference to the spec cannot
contradict the spec. **Consistent — not drift.**

**R5 — Key-name and casing differences across all three files.**
Why it looked suspicious: no implementation uses the spec's literal key spelling — Alpha uses
lowerCamelCase (`requestTimeoutMs`, `tlsMinVersion`) plus one SCREAMING_SNAKE outlier with a reordered
noun (`MAX_QUEUE_DEPTH` vs spec `queue_max_depth`), and Beta uses SCREAMING_SNAKE throughout
(`REQUEST_TIMEOUT_MS`, `QUEUE_MAX_DEPTH`).
Reasoning that clears it: the spec constrains values, not identifier spelling — `This table is the canonical contract. Implementations and runbooks must match it.` refers to the value contract, and
each name maps unambiguously to exactly one spec row. Naming style is a per-language/per-format
convention (Alpha is a `.conf`, Beta a `.env`). **Not drift** *as a naming issue*. Note this rejection
covers only the *names*; the values behind `MAX_QUEUE_DEPTH` (D2) and `HEALTH_CHECK_INTERVAL_SECONDS`
(D5) are separately confirmed as drift.

**R6 — `impl-alpha.md` `retry_backoff` split across two keys.**
Why it looked suspicious: the spec has one row (`exponential, base 250ms`) while Alpha has two lines,
so a naive one-to-one key comparison finds no single matching key.
Reasoning that clears it: `retryBackoff       = exponential` supplies the strategy and
`retryBackoffBaseMs = 250` supplies the base, jointly reproducing the spec's compound value
`exponential, base 250ms`. The same decomposition appears in Beta
(`RETRY_BACKOFF_STRATEGY` + `RETRY_BACKOFF_BASE_MS=250`), confirming it is the house encoding.
**Consistent — not drift for Alpha.** (Beta's *strategy* half is still D4.)

### Candidate examined and NOT rejected — the `1250 × 8 = 10000` temptation (D2)

Alpha's `MAX_QUEUE_DEPTH    = 1250` sits in an exact 1:8 ratio with the spec's `10000`, which
superficially resembles a bits↔bytes conversion of the R1/R2 kind. This is **not** a valid rejection:

- No fixture anywhere states a unit for queue depth other than requests. The spec's note is
  `Requests beyond depth are shed.` — the unit is requests, a countable item that has no byte/bit
  representation to convert between.
- Alpha supplies no conversion note for this key. Its only stated caveat is about override layering:
  `values above are read at boot; there is no runtime override` / `layer in Alpha.` — which, if
  anything, confirms 1250 is the effective value with nothing downstream to correct it.
- Contrast with R1 and R2, where the fixture itself states the conversion factor (`60 ticks per second`; `two replicas ... in total`). Absent such a statement, an 8× ratio is numerology, not a conversion.

Therefore D2 stands as confirmed drift.

### Observations recorded but not counted as drift (absence ≠ contradiction)

- `impl-alpha.md` contains no entry for `health_check_interval_s` and none for `idle_timeout_s`. The
  task asks for places where an implementation **contradicts** the spec; a missing key states no
  conflicting value, so it is not scored as drift here. Alpha's own note that `there is no runtime override` / `layer in Alpha` means the effective runtime values for these two keys are **unmeasured**
  from the fixtures alone — they cannot be confirmed compliant either. Flagged for follow-up.
- `runbook.md` mentions no value for `request_timeout_ms`, `retry_backoff`, `queue_max_depth`,
  `enable_legacy_auth`, or `idle_timeout_s`. Same treatment: silence, not contradiction.
- The `enable_legacy_auth` drift (D3) carries a stated cause: `The legacy auth flag was toggled during the March incident` / `bridge and has not been revisited since.` This is context, not additional drift.

## Assumptions

1. **`spec.md` is authoritative for all three other files.** Grounded in its own text: `This table is the canonical contract. Implementations and runbooks must match it.` Where an implementation and the
   spec disagree, the implementation is the drift — never the reverse.
2. **The runbook is in scope as a drift source.** The same sentence names runbooks explicitly, so
   operator guidance that prescribes a value differing from the spec (D6, D7) counts as drift even
   though it is prose rather than a config key.
3. **A compound spec value can drift on one component independently.** For `retry_backoff`, strategy
   and base are treated as separately checkable, which is why Beta is drift on strategy while its
   `RETRY_BACKOFF_BASE_MS=250` is recorded as compliant.
4. **Key-name normalization across casing styles is legitimate** (R5), i.e. `REQUEST_TIMEOUT_MS`,
   `requestTimeoutMs`, and `request_timeout_ms` denote the same setting. Without this, every row would
   be a false positive.
5. **Log-level names are case-insensitive** (R3). This is convention-based and is **unmeasured** — no
   fixture documents the parser's behavior.
6. **`tls_min_version` admits no temporary exception.** Based on the spec note `Hard floor for all listeners.` If the organization intends rotation-window exceptions to be legal, the spec — not the
   runbook — is the artifact that must change; either way the two documents currently contradict.
7. **The fixtures' quoted excerpts represent the full effective configuration for the keys they
   mention.** Each implementation file is titled an `excerpt from deployed config`, so behavior driven
   by lines outside the excerpts is **unmeasured**.
8. **No runtime verification was performed.** Nothing was executed, deployed, or probed; every claim
   above is a static document comparison. Actual running-system values are **unmeasured**.

## Stop Condition

Stop when all of the following hold — all are satisfied at the time of writing:

- [x] Exactly one artifact written, at
      `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-config-spec-drift-claude_code_goal_command.md`.
- [x] All five required headings present: Goal, Result, Evidence, Assumptions, Stop Condition.
- [x] Confirmed-drift table present with columns file, key, spec value, actual value, quoted evidence
      (7 rows, D1–D7).
- [x] Rejected-candidates section present, each entry showing the conversion or reasoning that clears
      it (R1–R6), with the reason it looked suspicious stated for each.
- [x] Confirmed findings and rejected candidates are in explicitly separated sections.
- [x] Explicit statement of violated spec constraints present (7 enumerated spec rows), including the
      rows confirmed not violated.
- [x] Every confirmed finding quotes the exact identifier/value from the fixture.
- [x] Unmeasured items stated as unmeasured (Alpha's two absent keys; log-parser case sensitivity;
      any behavior outside the quoted excerpts; all runtime values).
- [x] Only the four named fixtures and this output file were accessed under
      `benchmarks/mission-vs-goal/`; no benchmark metadata, task definitions, scoring config, or answer
      keys were read.
- [x] No commit, push, package install, or network access performed.
- [x] No claim of benchmark superiority made; this artifact reports only the configuration audit.

Not stopping earlier would require new fixture evidence, which the task scope forbids gathering.
