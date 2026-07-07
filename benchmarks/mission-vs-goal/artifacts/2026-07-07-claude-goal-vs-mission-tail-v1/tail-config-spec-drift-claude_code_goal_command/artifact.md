# Task: tail-config-spec-drift (arm: claude_code_goal_command)

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-07-claude-goal-vs-mission-tail-v1/tail-config-spec-drift-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit configuration drift against the canonical spec by reading exactly
four fixtures — `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md` under
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/` — and find every
place where an implementation or the runbook contradicts the spec, quoting key
and both values. Entries that look contradictory but are consistent after unit
or aggregate conversion must be explicitly rejected with the conversion shown.

Completion is controlled by Claude Code's built-in `/goal` command (session-scoped
Stop hook), not by the `mission` orchestrator.

## Result

Read all four fixtures once each (no other files under `benchmarks/mission-vs-goal/`
were opened, listed, or grepped). Found **7 confirmed drifts** against the
canonical spec (3 in impl-alpha.md, 2 in impl-beta.md, 2 in runbook.md) and
**3 candidates that initially looked like drift but clear after unit/aggregate
conversion or scoped-exception reasoning**, detailed below.

### Confirmed-drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | `3000` | `27000` | Spec: `` `request_timeout_ms` \| 3000 `` — Alpha: `requestTimeoutMs   = 27000` |
| impl-alpha.md | `queue_max_depth` | `10000` | `1250` | Spec: `` `queue_max_depth` \| 10000 `` — Alpha: `MAX_QUEUE_DEPTH    = 1250` |
| impl-alpha.md | `enable_legacy_auth` | `false` | `true` | Spec: `` `enable_legacy_auth` \| false \| Must stay false; scheduled for removal. `` — Alpha: `enableLegacyAuth   = true` (deployment notes: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since.") |
| impl-beta.md | `retry_backoff` | `exponential, base 250ms` | `constant-interval` (base 250ms) | Spec: `` `retry_backoff` \| exponential, base 250ms \| Jitter enabled. `` — Beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| impl-beta.md | `health_check_interval_s` | `15` | `75` | Spec: `` `health_check_interval_s` \| 15 \| Liveness probe cadence. `` — Beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| runbook.md | `max_retries` | `3` | `6` | Spec: `` `max_retries` \| 3 \| Applies to idempotent requests only. `` — Runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| runbook.md | `tls_min_version` | `1.3` (hard floor, no exceptions stated) | temporarily `1.2` | Spec: `` `tls_min_version` \| 1.3 \| Hard floor for all listeners. `` — Runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window, then proceed with the rotation" |

### Rejected candidates (looked contradictory, but consistent)

| File | Key | Spec value | Actual value | Why it looked suspicious | Why it's rejected (conversion/reasoning) |
|---|---|---|---|---|---|
| impl-beta.md | `idle_timeout_s` | `90` | `IDLE_TIMEOUT_TICKS=5400` | 5400 vs 90 looks like a huge, unexplained blow-up. | Beta's own note: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." Conversion: 5400 ticks ÷ 60 ticks/s = **90 s**, exactly matching spec. Not a drift — unit conversion (ticks → seconds) accounts for the full difference. |
| runbook.md | `db_pool_size_per_replica` (aggregate) | `32` per replica, "Two replicas run in production." | "the two replicas hold 64 pooled connections in total" | 64 vs 32 looks like double the spec value. | Aggregate conversion: 32 per replica × 2 replicas = **64** total. The runbook is stating the aggregate capacity figure, not a per-replica override — consistent with spec. |
| runbook.md | `log_level` | `info` ("Production default.") | canary exception: "DEBUG is allowed only on a single canary replica for up to one hour" | DEBUG vs INFO looks like a direct contradiction of the info setting. | Spec labels `info` as the "Production default," not a hard floor (contrast with the explicit "Hard floor for all listeners" wording used for `tls_min_version`). The runbook's canary DEBUG allowance is a scoped, time-boxed (≤1 hour), single-replica operational exception layered on top of the production default (all services still run at INFO), not a change to the default value itself. No spec constraint text forbids temporary exceptions the way it does for TLS. Rejected as reasoning-based, not a numeric conversion. |

### Not evaluated (absent from implementation fixtures, not contradictions)

- impl-alpha.md does not list `health_check_interval_s`, `idle_timeout_s`, or an explicit jitter setting at all (only `retryBackoff = exponential` and `retryBackoffBaseMs = 250` are shown, matching spec's strategy and base). Their absence from the excerpt is **unmeasured**, not a confirmed contradiction — there is no actual value in the fixture to quote against the spec for these three keys in Alpha.

### Spec constraints violated

Based on the confirmed-drift table above, the following canonical spec constraints are violated:

1. `request_timeout_ms` (spec: 3000) — violated by impl-alpha.md (27000).
2. `queue_max_depth` (spec: 10000, "Requests beyond depth are shed.") — violated by impl-alpha.md (1250), which sheds load far earlier than the spec's shedding threshold.
3. `enable_legacy_auth` (spec: false, "Must stay false; scheduled for removal.") — violated by impl-alpha.md (true). This is the most explicit violation: the spec states a hard "must stay false" constraint, and Alpha overrides it.
4. `retry_backoff` (spec: exponential, base 250ms) — violated by impl-beta.md, which uses `constant-interval` instead of `exponential` (base ms value itself does not drift).
5. `health_check_interval_s` (spec: 15) — violated by impl-beta.md (75).
6. `max_retries` (spec: 3, "Applies to idempotent requests only.") — violated by runbook.md, which instructs operators to retry up to 6 times.
7. `tls_min_version` (spec: 1.3, "Hard floor for all listeners.") — violated by runbook.md, which instructs temporarily lowering the floor to 1.2 during certificate rotation, contradicting the "hard floor" language (no exception is carved out in the spec for rotation windows).

Constraints **not** violated (rejected candidates): `idle_timeout_s`, the aggregate reading of `db_pool_size_per_replica`, and `log_level`.

## Evidence

All evidence is quoted verbatim from the four named fixtures read in this session:

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md` (canonical config table, 10 rows).
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md` (`alpha/config/production.conf` excerpt + deployment notes).
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md` (`beta/config/production.env` excerpt + tick-rate note).
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md` (Retry/TLS/Logging/Database/Health sections).

Every table row above embeds the exact quoted line(s) used as evidence — see
"Quoted evidence" and "Why it's rejected" columns. No other files under
`benchmarks/mission-vs-goal/` were opened, read, grepped, or listed; no network
access, package installation, commit, or push was performed during this run.

## Assumptions

- Assumed `impl-alpha.md`'s field names (`requestTimeoutMs`, `MAX_QUEUE_DEPTH`, etc.) map 1:1 to the spec's snake_case keys by semantic name only (no explicit mapping table was provided in the fixtures) — this is a reasonable but unverified assumption since the value domains and units align (e.g., both explicitly "Ms").
- Assumed impl-alpha.md's silence on `health_check_interval_s`, `idle_timeout_s`, and an explicit jitter flag means those settings are unmeasured in that fixture excerpt, not that they are deliberately set to some hidden value — this is stated explicitly rather than guessed.
- Assumed the runbook's operational guidance (retry count, TLS rotation step, log verbosity, DB aggregate) is meant to describe actual operational values/behavior comparable to the spec's constraints, since the runbook explicitly cross-references the spec in its Health section ("see the spec for cadence").
- Did not verify these findings against any answer key, scoring config, or other benchmark metadata — per the task rules, nothing under `benchmarks/mission-vs-goal/` other than the four named fixtures and this output file was opened.

## Stop Condition

This artifact exists at the required path and includes all five required
headings (Goal, Result, Evidence, Assumptions, Stop Condition), a confirmed-drift
table (file, key, spec value, actual value, quoted evidence) with 7 entries, a
rejected-candidates section with conversion/reasoning clearing 3 entries, and an
explicit statement of which spec constraints are violated (7) versus not
violated (3). No commit, push, package install, or network access occurred.
Per task rules, no benchmark metadata (task definitions, scoring configuration,
answer keys) under `benchmarks/mission-vs-goal/` was read, listed, or grepped —
only the four named fixtures and this output file were touched. This satisfies
the stated completion goal; the run is complete.
