# Task: tail-config-spec-drift (arm: claude_code_goal_command)

## Goal

Audit configuration drift against the canonical spec by reading exactly four
fixtures — `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md` under
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/` — and find every
place where an implementation or the runbook contradicts the spec, quoting key
and both values. Entries that look contradictory but are consistent after unit
or aggregate conversion must be explicitly rejected with the conversion shown.
Completion is controlled by Claude Code's built-in `/goal` command: the goal
is satisfied only once this artifact exists at the required path with all five
required headings (Goal, Result, Evidence, Assumptions, Stop Condition).

## Result

Confirmed **7 drift findings** (3 in `impl-alpha.md`, 2 in `impl-beta.md`, 2 in
`runbook.md`) and cleared **3 candidates** that looked contradictory but hold
up under unit/aggregate conversion or under the spec's own exception language.

### Confirmed-drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `requestTimeoutMs` | `request_timeout_ms` = 3000 | 27000 | `requestTimeoutMs   = 27000` |
| `impl-alpha.md` | `MAX_QUEUE_DEPTH` | `queue_max_depth` = 10000 | 1250 | `MAX_QUEUE_DEPTH    = 1250` |
| `impl-alpha.md` | `enableLegacyAuth` | `enable_legacy_auth` = false | true | `enableLegacyAuth   = true` |
| `impl-beta.md` | `RETRY_BACKOFF_STRATEGY` | `retry_backoff` = exponential, base 250ms | constant-interval | `RETRY_BACKOFF_STRATEGY=constant-interval` |
| `impl-beta.md` | `HEALTH_CHECK_INTERVAL_SECONDS` | `health_check_interval_s` = 15 | 75 | `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| `runbook.md` | retry count (Retry guidance section) | `max_retries` = 3 | "up to 6 times" | "the gateway will retry idempotent requests up to 6 times before shedding" |
| `runbook.md` | TLS floor (TLS section) | `tls_min_version` = 1.3 ("Hard floor for all listeners") | temporarily 1.2 | "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

### Rejected-candidates section

| Candidate | Why it looked suspicious | Why it is not a real finding (conversion/reasoning) |
|---|---|---|
| `impl-beta.md` `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s` = 90 | Raw numbers (5400 vs 90) look wildly different, ~60x apart. | Beta's own note: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." 5400 ticks ÷ 60 ticks/s = 90s, which matches the spec exactly. Unit conversion clears it. |
| `runbook.md` "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica` = 32 | 64 ≠ 32, looks like a doubled/contradictory pool size. | Spec's own note says "Two replicas run in production," and both `impl-alpha.md` (`dbPoolSizePerReplica = 32`) and `impl-beta.md` (`DB_POOL_SIZE_PER_REPLICA=32`) confirm 32 per replica. 32 × 2 replicas = 64 total — the runbook is stating the aggregate figure, not a per-replica override. Aggregate conversion clears it. |
| `runbook.md` Logging section allowing "DEBUG ... on a single canary replica for up to one hour" vs spec `log_level` = info | Mentions a non-`info` level in a document that should match the "info" default, looks like sanctioned drift. | Spec's own note for `log_level` says "Production default," not an absolute always-on constraint (contrast with `enable_legacy_auth`'s explicit "Must stay false"). The runbook's carve-out is narrowly scoped (one canary replica, ≤1 hour) and does not change the stated production default of `info`. No contradiction of the spec value itself. |

### Not evaluable (explicitly noted, not counted as drift or as cleared)

- `impl-alpha.md` does not list `health_check_interval_s` or `idle_timeout_s` at
  all in its excerpt. This is an absence, not a stated contradictory value —
  unmeasured for Alpha, not claimed as a finding.

## Evidence

All evidence is quoted verbatim from the four named fixtures, read in full
before analysis:

- `spec.md` — canonical table, 10 keys (`request_timeout_ms`, `max_retries`,
  `retry_backoff`, `queue_max_depth`, `tls_min_version`,
  `health_check_interval_s`, `enable_legacy_auth`, `idle_timeout_s`,
  `log_level`, `db_pool_size_per_replica`), each with a stated value and a
  notes column carrying constraint language (e.g. "Hard floor for all
  listeners", "Must stay false; scheduled for removal", "Applies to idempotent
  requests only").
- `impl-alpha.md` — code block `alpha/config/production.conf` with 9 key/value
  lines, plus a deployment note explaining `enableLegacyAuth` was toggled
  during an incident and never reverted (corroborates the drift is real and
  unresolved, not a documentation typo).
- `impl-beta.md` — code block `beta/config/production.env` with 11 key/value
  lines, plus a note that idle time is measured in scheduler ticks at 60
  ticks/second, and that backoff strategy names come from an enum including
  `constant-interval`, `exponential`, `decorrelated-jitter`.
- `runbook.md` — five prose sections (Retry guidance, TLS, Logging, Database
  connections, Health), each checked sentence-by-sentence against the
  corresponding spec key.

Cross-checks performed: every key present in `impl-alpha.md` and
`impl-beta.md` was compared against its literal spec counterpart; every
numeric or qualitative claim in `runbook.md` was compared against the spec row
it references (explicitly, e.g. "see the spec for cadence," or implicitly,
e.g. retry count / TLS floor / log level / pool size).

## Assumptions

- Key-name mapping across naming conventions (camelCase in Alpha, UPPER_SNAKE
  in Beta, snake_case in spec) was resolved by semantic match (e.g.
  `requestTimeoutMs` / `REQUEST_TIMEOUT_MS` / `request_timeout_ms` are the same
  key). No fixture disputes this mapping.
- `retry_backoff`'s "Jitter enabled" clause in the spec has no corresponding
  explicit field in either `impl-alpha.md` or `impl-beta.md` (neither states a
  jitter on/off value). This is treated as unmeasured, not as a drift finding,
  since there is no quoted actual value to contradict the spec with.
- The Health section of `runbook.md` ("Liveness probes are configured
  centrally; see the spec for cadence") was read as deferring to the spec
  rather than asserting its own value, so it is not itself a drift source; the
  actual drift on this constraint is Beta's `HEALTH_CHECK_INTERVAL_SECONDS=75`.
- No files outside the four named fixtures and this output file were opened,
  read, grepped, or listed, per the task's scope restriction.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-10-claude-goal-vs-mission-tail-smoke-v2/tail-config-spec-drift-claude_code_goal_command.md`
and includes all five required headings (Goal, Result, Evidence, Assumptions,
Stop Condition), a confirmed-drift table (file, key, spec value, actual value,
quoted evidence), a rejected-candidates section with conversions/reasoning,
and this explicit statement of violated spec constraints:

- **`enable_legacy_auth` — "Must stay false; scheduled for removal."** Violated
  by `impl-alpha.md`'s `enableLegacyAuth = true`.
- **`tls_min_version` — "Hard floor for all listeners."** Violated by
  `runbook.md`'s rotation guidance to temporarily set the floor to 1.2.
- **`max_retries` — "Applies to idempotent requests only"** (value 3).
  Violated by `runbook.md`'s "up to 6 times" retry guidance.
- **`health_check_interval_s`** (value 15, "Liveness probe cadence"). Violated
  by `impl-beta.md`'s `HEALTH_CHECK_INTERVAL_SECONDS=75`.
- **`request_timeout_ms`** (value 3000). Violated by `impl-alpha.md`'s
  `requestTimeoutMs = 27000`.
- **`queue_max_depth`** (value 10000, "Requests beyond depth are shed").
  Violated by `impl-alpha.md`'s `MAX_QUEUE_DEPTH = 1250`.
- **`retry_backoff`** (exponential, base 250ms). Violated by `impl-beta.md`'s
  `RETRY_BACKOFF_STRATEGY=constant-interval` (base 250ms itself matches, but
  the strategy type does not).

No commits, pushes, package installs, or network access were performed. No
files outside this artifact and the four named fixtures were touched.
