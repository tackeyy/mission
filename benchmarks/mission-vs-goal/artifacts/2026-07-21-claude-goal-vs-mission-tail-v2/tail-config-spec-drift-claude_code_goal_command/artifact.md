# tail-config-spec-drift — claude_code_goal_command

## Goal

Audit configuration drift against the canonical spec by reading exactly four fixtures — `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md` (all under `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/`) — and find every place where an implementation or the runbook contradicts the spec, quoting the key and both values. Entries that look contradictory but are consistent after unit or aggregate conversion must be explicitly rejected with the conversion shown. Completion is controlled by Claude Code's built-in `/goal` command, which was set to require this artifact to exist with the headings Goal, Result, Evidence, Assumptions, and Stop Condition.

## Result

Read all four named fixtures in full (no other files under `benchmarks/mission-vs-goal/` were opened). Identified **7 confirmed drifts** against the canonical spec and **3 rejected candidates** that appeared contradictory but resolve cleanly under unit/aggregate conversion or under a closer reading of the spec's own wording.

### Confirmed-drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `request_timeout_ms` | `3000` | `27000` | spec: "`request_timeout_ms` \| 3000"; alpha: "requestTimeoutMs   = 27000" |
| `impl-alpha.md` | `queue_max_depth` | `10000` | `1250` | spec: "`queue_max_depth` \| 10000"; alpha: "MAX_QUEUE_DEPTH    = 1250" |
| `impl-alpha.md` | `enable_legacy_auth` | `false` (must stay false) | `true` | spec: "`enable_legacy_auth` \| false \| Must stay false; scheduled for removal."; alpha: "enableLegacyAuth   = true" (deployment note: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since.") |
| `impl-beta.md` | `retry_backoff` (strategy) | `exponential` | `constant-interval` | spec: "`retry_backoff` \| exponential, base 250ms"; beta: "RETRY_BACKOFF_STRATEGY=constant-interval" |
| `impl-beta.md` | `health_check_interval_s` | `15` | `75` | spec: "`health_check_interval_s` \| 15"; beta: "HEALTH_CHECK_INTERVAL_SECONDS=75" |
| `runbook.md` | `max_retries` | `3` | `6` | spec: "`max_retries` \| 3"; runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| `runbook.md` | `tls_min_version` | `1.3` (hard floor for all listeners) | `1.2` (instructed floor during rotation) | spec: "`tls_min_version` \| 1.3 \| Hard floor for all listeners."; runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

### Rejected candidates

| File | Candidate | Why it looked suspicious | Why it's not a real finding |
|---|---|---|---|
| `impl-beta.md` | `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s: 90` | 5400 ≠ 90, looks like a large mismatch | Beta's own note: "the scheduler runs at 60 ticks per second." Conversion: 5400 ticks ÷ 60 ticks/s = **90 s**, exactly matching spec. Rejected — unit conversion clears it. |
| `runbook.md` | "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica: 32` | 64 ≠ 32, looks like a mismatch | Spec note: "Two replicas run in production." Conversion: 32 connections/replica × 2 replicas = **64** total, exactly matching the runbook's aggregate figure. Rejected — aggregate conversion clears it. |
| `runbook.md` | "DEBUG is allowed only on a single canary replica for up to one hour" vs spec `log_level: info` | Looks like it contradicts the fixed `info` value | Spec's own Notes column labels `info` as "Production default," not an absolute prohibition on any other level. The runbook's first sentence — "Run all services at INFO verbosity in production" — restates the same default, and the DEBUG exception is narrowly scoped (single canary replica, ≤1 hour), not a change to the production default. Rejected — consistent with the spec's "default" framing, not a contradicting value. |

### Spec constraints explicitly violated

- **`enable_legacy_auth` must stay false; scheduled for removal** — violated by `impl-alpha.md` (`enableLegacyAuth = true`).
- **`tls_min_version` is a hard floor for all listeners (1.3)** — violated by `runbook.md`'s instruction to temporarily set the floor to 1.2 during certificate rotation.
- **`max_retries` (3) applies to idempotent requests** — violated by `runbook.md`'s retry guidance of up to 6 attempts.
- **`retry_backoff` must be exponential** — violated by `impl-beta.md`'s `constant-interval` strategy (the base-250ms value itself is not violated: both alpha and beta set `retryBackoffBaseMs`/`RETRY_BACKOFF_BASE_MS` to `250`, matching spec).
- **`request_timeout_ms` (3000)** — violated by `impl-alpha.md` (`27000`).
- **`queue_max_depth` (10000)** — violated by `impl-alpha.md` (`1250`).
- **`health_check_interval_s` (15)** — violated by `impl-beta.md` (`75`).

Not violated / not evaluable as drift: `tls_min_version` steady-state value (1.3, matches in both alpha and beta), `log_level` steady-state value (`info`, matches in both), `db_pool_size_per_replica` (32, matches in both), `max_retries` numeric value in both configs (3, matches in both — only the runbook's guidance text diverges).

## Evidence

All evidence is quoted verbatim from the four named fixture files, read in full via the Read tool:

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md` — canonical 10-row config table (`request_timeout_ms`, `max_retries`, `retry_backoff`, `queue_max_depth`, `tls_min_version`, `health_check_interval_s`, `enable_legacy_auth`, `idle_timeout_s`, `log_level`, `db_pool_size_per_replica`).
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md` — `alpha/config/production.conf` excerpt, 8 keys, plus a deployment note about the legacy-auth toggle and "no runtime override layer."
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md` — `beta/config/production.env` excerpt, 11 keys, plus a note that idle time is counted in scheduler ticks (60 ticks/second) and that backoff strategy names follow a retry-library enum.
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md` — five sections (Retry guidance, TLS, Logging, Database connections, Health) with free-text operational guidance.

Every table row and rejected-candidate row above quotes the exact key/value string from its source file, as required by the task validator.

## Assumptions

- Alpha's excerpt does not include `health_check_interval_s` or `idle_timeout_s` keys at all. This is treated as **unmeasured** for Alpha, not as a drift — the fixture is explicitly labeled an "excerpt," so absence of a key is not evidence of a contradicting value.
- `impl-alpha.md`'s and `impl-beta.md`'s naming conventions (camelCase vs. `UPPER_SNAKE_CASE`) are assumed to be cosmetic differences in configuration-file format between the two services, not drift findings in themselves — only value mismatches against the spec are scored as drift.
- The runbook's TLS section describes a temporary, time-boxed procedural step ("first ... then proceed with the rotation") rather than a permanent config value; it is still counted as a confirmed drift because the spec labels `tls_min_version: 1.3` an unconditional "hard floor for all listeners," which the runbook's instruction to intentionally lower to 1.2 directly contradicts, even temporarily.
- No fixtures or files outside the four named ones, and no other content under `benchmarks/mission-vs-goal/`, were opened, read, or listed, per the task rules.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-21-claude-goal-vs-mission-tail-v2/tail-config-spec-drift-claude_code_goal_command.md` and contains the five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a confirmed-drift table (file, key, spec value, actual value, quoted evidence) with 7 rows, a rejected-candidates section with 3 rows each showing the clearing conversion/reasoning, and an explicit list of which spec constraints are violated. No commit, push, package install, or network access was performed. No files outside this output path were modified. This satisfies the `/goal` completion condition set for this run.
