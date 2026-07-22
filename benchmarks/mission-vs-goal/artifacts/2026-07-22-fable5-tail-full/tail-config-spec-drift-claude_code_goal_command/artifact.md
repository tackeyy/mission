# tail-config-spec-drift — claude_code_goal_command

## Goal

Audit configuration drift against the canonical spec (`spec.md`) across `impl-alpha.md`, `impl-beta.md`, and `runbook.md`. Report every contradiction with quoted evidence, and explicitly reject candidates that are consistent after unit or aggregate conversion. Deliver exactly one artifact at `benchmarks/mission-vs-goal/run-output/2026-07-22-fable5-tail-full/tail-config-spec-drift-claude_code_goal_command.md` with headings Goal, Result, Evidence, Assumptions, and Stop Condition. Arm: `claude_code_goal_command`, using Claude Code's built-in `/goal` command as the completion controller.

## Result

**7 confirmed drifts** (3 in impl-alpha, 2 in impl-beta, 2 in runbook) and **4 rejected candidates** cleared by unit conversion, aggregate conversion, or key-format equivalence.

### Confirmed-drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` (`requestTimeoutMs`) | `3000` | `27000` | Spec row: `request_timeout_ms` = 3000; Alpha: `requestTimeoutMs   = 27000`. Both values are in milliseconds — no unit conversion reconciles 27000 ms with 3000 ms. |
| impl-alpha.md | `queue_max_depth` (`MAX_QUEUE_DEPTH`) | `10000` | `1250` | Spec row: `queue_max_depth` = 10000; Alpha: `MAX_QUEUE_DEPTH    = 1250`. Both are request counts (unitless depth); no conversion basis (e.g. worker count) is stated in any fixture, so 1250 ≠ 10000 is a direct contradiction. |
| impl-alpha.md | `enable_legacy_auth` (`enableLegacyAuth`) | `false` | `true` | Spec row: `enable_legacy_auth` = false, note "Must stay false; scheduled for removal."; Alpha: `enableLegacyAuth   = true`. Alpha's own notes confirm it is live drift: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since." |
| impl-beta.md | `retry_backoff` (`RETRY_BACKOFF_STRATEGY`) | `exponential, base 250ms` | `constant-interval` | Spec row: `retry_backoff` = exponential, base 250ms; Beta: `RETRY_BACKOFF_STRATEGY=constant-interval`. The retry library enum in Beta's notes (`constant-interval`, `exponential`, `decorrelated-jitter`) shows `exponential` was available and a different strategy was chosen — a semantic contradiction, not a naming difference. |
| impl-beta.md | `health_check_interval_s` (`HEALTH_CHECK_INTERVAL_SECONDS`) | `15` | `75` | Spec row: `health_check_interval_s` = 15; Beta: `HEALTH_CHECK_INTERVAL_SECONDS=75`. Both keys are explicitly in seconds — no unit conversion reconciles 75 s with 15 s. |
| runbook.md | `max_retries` | `3` | `6` | Spec row: `max_retries` = 3; Runbook "Retry guidance": "the gateway will retry idempotent requests up to 6 times before shedding". Both count retries of idempotent requests; 6 ≠ 3. |
| runbook.md | `tls_min_version` | `1.3` (hard floor) | `1.2` (during rotation) | Spec row: `tls_min_version` = 1.3, note "Hard floor for all listeners."; Runbook "TLS": "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window". A hard floor admits no temporary lowering; the runbook instructs an operation the spec forbids. |

### Rejected candidates (look contradictory, but are not drift)

1. **impl-beta.md `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s` = 90** — Looks suspicious because 5400 ≠ 90 and the units differ. Cleared by unit conversion: Beta's notes state "the scheduler runs at 60 ticks per second", so 5400 ticks ÷ 60 ticks/s = **90 s**, exactly matching the spec's `idle_timeout_s` of 90.
2. **runbook.md "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica` = 32** — Looks suspicious because 64 ≠ 32. Cleared by aggregate conversion: the spec notes "Two replicas run in production", so 32 connections/replica × 2 replicas = **64 total**, exactly the runbook's aggregate figure. Per-replica vs aggregate framing, same underlying value.
3. **impl-alpha.md / impl-beta.md splitting `retry_backoff` into two keys (`retryBackoff       = exponential` + `retryBackoffBaseMs = 250` in Alpha; `RETRY_BACKOFF_BASE_MS=250` in Beta)** — Looks suspicious because the spec expresses one compound value ("exponential, base 250ms") and the implementations use different key names and shapes. Cleared by format equivalence: Alpha's pair (`exponential`, base `250` ms) reproduces both components of the spec value exactly; Beta's base value `250` ms also matches. (Beta's *strategy* component is confirmed drift #4 in the table above — only its base value is cleared here.)
4. **runbook.md "DEBUG is allowed only on a single canary replica for up to one hour" vs spec `log_level` = info** — Looks suspicious because DEBUG ≠ info. Cleared by scope reasoning: the spec annotates `log_level` = info as the "Production default", and the runbook's primary instruction agrees ("Run all services at INFO verbosity in production"). A time-boxed, single-canary diagnostic exception does not change the production default value, so there is no contradiction of the spec's stated contract.

### Spec constraints violated

- `request_timeout_ms` = 3000 — violated by impl-alpha (`27000`).
- `queue_max_depth` = 10000 — violated by impl-alpha (`1250`).
- `enable_legacy_auth` = false ("Must stay false") — violated by impl-alpha (`true`); this is the explicitly named must-stay constraint in the spec.
- `retry_backoff` = exponential, base 250ms — strategy component violated by impl-beta (`constant-interval`).
- `health_check_interval_s` = 15 — violated by impl-beta (`75`).
- `max_retries` = 3 — contradicted by the runbook's retry guidance ("up to 6 times").
- `tls_min_version` = 1.3 ("Hard floor for all listeners") — contradicted by the runbook's rotation procedure (temporary floor of 1.2).

Not violated by any fixture: `idle_timeout_s`, `log_level`, `db_pool_size_per_replica`, and (in Beta only) `request_timeout_ms`, `max_retries`, `queue_max_depth`, `tls_min_version`, `enable_legacy_auth`.

## Evidence

All evidence is quoted verbatim from the four permitted fixtures; each confirmed-drift row above quotes both the spec value and the actual value.

- `spec.md` (canonical table): request_timeout_ms 3000; max_retries 3; retry_backoff "exponential, base 250ms"; queue_max_depth 10000; tls_min_version 1.3 ("Hard floor for all listeners."); health_check_interval_s 15; enable_legacy_auth false ("Must stay false; scheduled for removal."); idle_timeout_s 90; log_level info ("Production default."); db_pool_size_per_replica 32 ("Two replicas run in production.").
- `impl-alpha.md`: `requestTimeoutMs   = 27000`, `maxRetries         = 3`, `retryBackoff       = exponential`, `retryBackoffBaseMs = 250`, `MAX_QUEUE_DEPTH    = 1250`, `tlsMinVersion      = 1.3`, `enableLegacyAuth   = true`, `logLevel           = info`, `dbPoolSizePerReplica = 32`; notes: "The legacy auth flag was toggled during the March incident bridge and has not been revisited since."
- `impl-beta.md`: `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`, `RETRY_BACKOFF_STRATEGY=constant-interval`, `RETRY_BACKOFF_BASE_MS=250`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `HEALTH_CHECK_INTERVAL_SECONDS=75`, `ENABLE_LEGACY_AUTH=false`, `IDLE_TIMEOUT_TICKS=5400`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`; notes: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second."
- `runbook.md`: "retry idempotent requests up to 6 times before shedding"; "set the load balancer TLS floor to 1.2 first"; "Run all services at INFO verbosity in production. DEBUG is allowed only on a single canary replica for up to one hour."; "the two replicas hold 64 pooled connections in total"; "Liveness probes are configured centrally; see the spec for cadence."

Unmeasured items (stated per the run rules, not claimed as findings):

- `impl-alpha.md` is labeled an "excerpt from deployed config" and contains no key for `health_check_interval_s` or `idle_timeout_s`. Whether Alpha's deployed values for those two keys match the spec is **unmeasured** — absence from an excerpt is not evidence of drift or of compliance.
- Whether the runbook's TLS-rotation procedure has actually been executed (i.e., whether a listener currently runs at 1.2) is **unmeasured**; the confirmed finding is that the documented procedure contradicts the spec's hard floor.

## Assumptions

- `spec.md` is the sole canonical contract, per its own header: "This table is the canonical contract. Implementations and runbooks must match it."
- Key-name casing/format differences (`requestTimeoutMs` / `REQUEST_TIMEOUT_MS` / `request_timeout_ms`, `MAX_QUEUE_DEPTH` / `queue_max_depth`) map to the same spec keys; only value semantics were audited, not naming conventions.
- The runbook's "retry ... up to 6 times" refers to the same quantity as spec `max_retries` (retries of idempotent requests before shedding), since both texts describe retries of idempotent requests.
- "Hard floor" in the spec's `tls_min_version` note is read literally: no temporary or scoped lowering is permitted, so a documented procedure lowering it to 1.2 is a contradiction even though it is transient.
- Only the four named fixtures and this output file were read; no other files under `benchmarks/mission-vs-goal/` were opened, per the run rules.

## Stop Condition

This run stops when this artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-22-fable5-tail-full/tail-config-spec-drift-claude_code_goal_command.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, with: (a) a confirmed-drift table (file, key, spec value, actual value, quoted evidence), (b) a rejected-candidates section showing the conversion or reasoning that clears each one, and (c) an explicit statement of which spec constraints are violated. All three are present above; the `/goal` Stop hook for this session verifies the same condition and auto-clears on satisfaction. No commits, pushes, package installs, or network access were performed, and no other files were modified.
