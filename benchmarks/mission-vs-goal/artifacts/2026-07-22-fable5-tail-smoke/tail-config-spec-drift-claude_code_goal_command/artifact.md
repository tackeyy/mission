# Config Spec Drift Audit — tail-config-spec-drift (arm: claude_code_goal_command)

## Goal

Audit configuration drift against the canonical spec at
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`, comparing exactly three
documents: `impl-alpha.md`, `impl-beta.md`, and `runbook.md` (all under
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/`). Report every contradiction with
quoted evidence, and explicitly reject candidates that are consistent after unit or aggregate
conversion. Completion is controlled by Claude Code's built-in `/goal` command (Stop hook active
until this artifact exists with the required headings).

## Result

**7 confirmed drifts** (3 in impl-alpha, 2 in impl-beta, 2 in the runbook) and **5 rejected
candidates** that look contradictory but are consistent after conversion or are
representation-only differences.

### Confirmed drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` (`requestTimeoutMs`) | `3000` | `27000` | Spec: "`request_timeout_ms` \| 3000"; Alpha: "`requestTimeoutMs   = 27000`" |
| impl-alpha.md | `queue_max_depth` (`MAX_QUEUE_DEPTH`) | `10000` | `1250` | Spec: "`queue_max_depth` \| 10000"; Alpha: "`MAX_QUEUE_DEPTH    = 1250`" |
| impl-alpha.md | `enable_legacy_auth` (`enableLegacyAuth`) | `false` | `true` | Spec: "`enable_legacy_auth` \| false \| Must stay false"; Alpha: "`enableLegacyAuth   = true`" |
| impl-beta.md | `retry_backoff` (`RETRY_BACKOFF_STRATEGY`) | `exponential, base 250ms` | `constant-interval` | Spec: "`retry_backoff` \| exponential, base 250ms"; Beta: "`RETRY_BACKOFF_STRATEGY=constant-interval`" |
| impl-beta.md | `health_check_interval_s` (`HEALTH_CHECK_INTERVAL_SECONDS`) | `15` | `75` | Spec: "`health_check_interval_s` \| 15"; Beta: "`HEALTH_CHECK_INTERVAL_SECONDS=75`" |
| runbook.md | `max_retries` (Retry guidance) | `3` | `6` | Spec: "`max_retries` \| 3"; Runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| runbook.md | `tls_min_version` (TLS rotation step) | `1.3` (hard floor) | `1.2` during rotation | Spec: "`tls_min_version` \| 1.3 \| Hard floor for all listeners."; Runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

Notes on each confirmed drift:

- **Alpha `requestTimeoutMs = 27000`**: both values are in milliseconds (both keys carry an
  explicit ms suffix), so no unit conversion applies. 27000 ms is not 3000 ms; this is a 9x drift,
  not a microsecond/millisecond confusion (which would give 3000000 or 3).
- **Alpha `MAX_QUEUE_DEPTH = 1250`**: neither the spec nor Alpha's fixture states any
  per-shard/per-worker divisor that would map 1250 to 10000 (10000 / 1250 = 8, but no factor of 8
  is documented anywhere in the fixtures). With no stated conversion basis, this is confirmed drift.
- **Alpha `enableLegacyAuth = true`**: the fixture itself notes "The legacy auth flag was toggled
  during the March incident bridge and has not been revisited since" — an acknowledged,
  un-reverted deviation from a spec key marked "Must stay false; scheduled for removal."
- **Beta `RETRY_BACKOFF_STRATEGY=constant-interval`**: the base interval matches (see rejected
  candidates), but the strategy itself contradicts the spec's `exponential`. Beta's own note
  confirms `constant-interval` and `exponential` are distinct enum values of the retry library
  ("`constant-interval`, `exponential`, `decorrelated-jitter`").
- **Beta `HEALTH_CHECK_INTERVAL_SECONDS=75`**: both sides are in seconds (spec key `_s`, Beta key
  `_SECONDS`), so no unit conversion applies. 75 s is not 15 s.
- **Runbook "up to 6 times"**: under either reading the runbook contradicts `max_retries = 3` —
  read as 6 retries it is 6 vs 3; read as 6 total attempts it implies 5 retries vs 3 (3 retries
  would be at most 4 total attempts).
- **Runbook TLS floor 1.2**: the spec marks 1.3 as a "Hard floor for all listeners" with no
  exception window, so an operational instruction to lower the load-balancer floor to 1.2, even
  temporarily, contradicts the spec as written.

### Rejected candidates (look contradictory, but are not drift)

1. **Beta `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s = 90`** — REJECTED. Beta's fixture
   states "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second."
   Conversion: 5400 ticks / 60 ticks-per-second = **90 s**, exactly the spec value. Suspicious
   because 5400 differs from 90 at face value; cleared by the documented tick rate.
2. **Runbook "the two replicas hold 64 pooled connections in total" vs spec
   `db_pool_size_per_replica = 32`** — REJECTED. The runbook figure is an aggregate; the spec notes
   "Two replicas run in production." Conversion: 64 total / 2 replicas = **32 per replica**,
   matching the spec. Suspicious because 64 differs from 32 at face value; cleared by the
   aggregate-to-per-replica conversion.
3. **Alpha `retryBackoff = exponential` plus `retryBackoffBaseMs = 250` vs spec
   `retry_backoff = exponential, base 250ms`** — REJECTED. Alpha splits one spec entry into two
   keys, but the combined content (strategy `exponential`, base `250` ms) equals the spec value
   exactly. Suspicious because the key structure differs; cleared because it is a representation
   difference, not a value difference.
4. **Runbook "DEBUG is allowed only on a single canary replica for up to one hour" vs spec
   `log_level = info`** — REJECTED. The runbook's primary instruction, "Run all services at INFO
   verbosity in production," matches the spec, whose note says "Production default." A bounded
   single-canary DEBUG exception does not change the production default, so it does not contradict
   the spec as written. Suspicious because DEBUG differs from info at face value.
5. **Key naming/casing differences (e.g. Alpha `MAX_QUEUE_DEPTH` vs spec `queue_max_depth`)** —
   REJECTED as a naming finding. The fixtures use camelCase (Alpha) and SCREAMING_SNAKE (Beta)
   variants of the spec's snake_case keys throughout; key naming style is not a configured value
   and carries no drift by itself. (The *value* of Alpha's `MAX_QUEUE_DEPTH` is separately
   confirmed drift above.)

### Spec constraints violated

The following spec constraints are violated by at least one document:

- `request_timeout_ms = 3000` — violated by impl-alpha (`27000`).
- `queue_max_depth = 10000` — violated by impl-alpha (`1250`).
- `enable_legacy_auth = false` ("Must stay false") — violated by impl-alpha (`true`).
- `retry_backoff = exponential, base 250ms` — violated by impl-beta (strategy `constant-interval`).
- `health_check_interval_s = 15` — violated by impl-beta (`75`).
- `max_retries = 3` — violated by runbook ("up to 6 times").
- `tls_min_version = 1.3` ("Hard floor for all listeners") — violated by runbook (temporary floor
  of `1.2`).

Constraints **not** violated by any document: `idle_timeout_s = 90`, `log_level = info`, and
`db_pool_size_per_replica = 32` (each has an apparent conflict that is cleared by conversion or
reading — see rejected candidates 1, 2, and 4).

## Evidence

All evidence is quoted verbatim from the four permitted fixture files (each read in full on
2026-07-22; no other files under `benchmarks/mission-vs-goal/` were opened):

- `spec.md`: canonical table rows quoted per finding above, e.g. "`request_timeout_ms` | 3000",
  "`enable_legacy_auth` | false | Must stay false; scheduled for removal.",
  "`tls_min_version` | 1.3 | Hard floor for all listeners.",
  "`db_pool_size_per_replica` | 32 | Two replicas run in production."
- `impl-alpha.md`: "`requestTimeoutMs   = 27000`", "`MAX_QUEUE_DEPTH    = 1250`",
  "`enableLegacyAuth   = true`", "`retryBackoff       = exponential`",
  "`retryBackoffBaseMs = 250`", and the note "The legacy auth flag was toggled during the March
  incident bridge and has not been revisited since."
- `impl-beta.md`: "`RETRY_BACKOFF_STRATEGY=constant-interval`",
  "`HEALTH_CHECK_INTERVAL_SECONDS=75`", "`IDLE_TIMEOUT_TICKS=5400`", and the note "the scheduler
  runs at 60 ticks per second."
- `runbook.md`: "retry idempotent requests up to 6 times before shedding", "set the load balancer
  TLS floor to 1.2 first", "the two replicas hold 64 pooled connections in total", "Run all
  services at INFO verbosity in production."

Unmeasured: nothing was executed or probed at runtime; all findings are static comparisons of the
four fixture documents. Whether the deployed systems actually behave per these excerpts is
unmeasured. Keys absent from an implementation excerpt (e.g. Alpha shows no
`health_check_interval_s` or `idle_timeout_s` line) were **not** counted as drift, because both
implementation files are explicitly labeled "excerpt" — absence from an excerpt is unmeasured, not
evidence of a missing key.

## Assumptions

- Both implementation files are excerpts, so missing keys are treated as unmeasured rather than as
  drift (stated in each fixture header: "excerpt from deployed config").
- Key-name casing/formatting differences (camelCase / SCREAMING_SNAKE / snake_case) map to the same
  logical spec keys by obvious correspondence; naming style itself is not drift.
- The spec's "Hard floor" note on `tls_min_version` admits no temporary exception, so the runbook's
  rotation procedure counts as a contradiction even though it is transient.
- The spec's "Production default" note on `log_level` permits the runbook's bounded single-canary
  DEBUG exception; a stricter reading would make it an eighth drift, but the note's wording
  ("default") supports the permissive reading.
- Beta's stated tick rate (60 ticks per second) is taken as accurate for the idle-timeout
  conversion.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-22-fable5-tail-smoke/tail-config-spec-drift-claude_code_goal_command.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, plus the
validator-required confirmed-drift table, rejected-candidates section, and explicit statement of
violated spec constraints. Only the four named fixture files and this output file were touched; no
commits, pushes, package installs, or network access occurred. No benchmark-superiority claim is
made — this is a single task artifact for arm `claude_code_goal_command`.
