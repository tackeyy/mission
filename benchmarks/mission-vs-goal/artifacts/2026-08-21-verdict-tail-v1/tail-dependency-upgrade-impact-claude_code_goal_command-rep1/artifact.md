# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

- Task id: `tail-dependency-upgrade-impact`
- Category: refactoring
- Arm: `claude_code_goal_command`
- Sources read (exactly two): `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`, `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

## Goal

Produce this artifact mapping every relaykit v3 breaking change to the concrete call sites it affects, stating migration steps including any ordering constraint, and separating confirmed findings from rejected candidates with quoted inventory evidence.

## Result

Four impactful breaking changes were confirmed against real call sites; three changelog entries that look breaking (or look actionable) were evaluated and rejected because the inventory shows no affected call site.

### Confirmed findings (impactful breaking changes)

**1. `parseConfig` strict-key enforcement → `services/ingest/loader`**

- Changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Inventory evidence: `` `services/ingest/loader` `` | `` `parseConfig(raw)` `` | "Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Impact: on v3 the retained `flush_interval` key is no longer silently ignored, so `parseConfig(raw)` raises `ConfigKeyError` at load time.
- Migration: delete `flush_interval` (or move it out of the parsed config into a comment/doc) **before** the v3 bump reaches this service.

**2. `onRetry` signature change → `services/dispatch/retry-metrics`**

- Changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Inventory evidence: `` `services/dispatch/retry-metrics` `` | `` `onRetry((attempt, error) => ...)` `` | "Two-argument callback records retry counters."
- Impact: the registered two-argument callback is "no longer invoked", so retry counters silently stop being recorded — a silent metrics loss, not a crash.
- Migration: rewrite the callback to `onRetry((context) => ...)` and read attempt/error off `context`.

**3. `publish()` default payload encoding msgpack → JSON → `services/edge-cache/consumer` (ordering constraint)**

- Changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Inventory evidence: `` `services/edge-cache/consumer` `` | "subscribes to `publish()` output" | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Impact: after the bump, publishers emit JSON while the consumer still runs a msgpack reader, so payloads fail to decode. Because "no codec pin is set anywhere in the repo", nothing currently protects the wire format.
- **Ordering constraint (the only one in this changelog):** the codec pin "must be set before the first `publish()` call". So the pin must be installed at publisher startup, ahead of any publish, and either the pin must land before the v3 rollout or the consumer must be switched to a JSON reader before publishers move to v3. Publishing on v3 first and fixing the consumer afterwards loses/garbles the messages published in between.

**4. `Queue.drain()` becomes async → `scripts/shutdown-hook`**

- Changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Inventory evidence: `` `scripts/shutdown-hook` `` | `` `queue.drain()` `` | "Called synchronously as the last line before process exit."
- Impact: as the last line before process exit, the un-awaited Promise means the process exits before the queue empties — data loss on shutdown.
- Migration: `await queue.drain()` (making the hook async) and ensure process exit is sequenced after the awaited drain resolves.

### Rejected candidates (look breaking, but no affected call site)

**R1. `Logger.warnOnce` removal** — Changelog item 5: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key." This reads as a hard API removal and would normally be a migration item. Rejected because the inventory states: `` `services/*/logging` `` | `` `Logger.warn` `` | "No `warnOnce` call sites found (grep returned zero)." Zero call sites → zero impact.

**R2. `connect()` default timeout 30s → 10s** — Changelog item 6: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected." A default-value change is a classic silent behavior break. Rejected because the inventory states: `` `services/*/bootstrap` `` | `` `connect({ timeout: 20_000 }) `` | "Every `connect()` call site passes an explicit timeout." The explicit `20_000` overrides the default, and the changelog itself scopes the break to call sites that rely on the default.

**R3. New `Queue.peek()` API** — Changelog item 8: "New `Queue.peek()` API." The inventory line `` `services/billing/exporter` `` | `` `Queue.peek()` (planned) `` | "Not yet using it; listed from the design doc." looks like a live dependency at a glance, and could be mistaken for a break. Rejected on two independent grounds: adding a new API is additive rather than breaking, and the call site is "Not yet using it" — planned only, so there is nothing to migrate.

**Non-breaking changelog entries not adjudicated as items** (recorded for completeness, no call-site mapping required): item 7 "Internal buffer pooling rewritten; ~12% lower allocation rate" (performance only; the ~12% figure is upstream's claim and is unmeasured here), item 9 "Documentation moved to a new site", item 10 "Minimum supported runtime raised to LTS" (the repo's current runtime version is not stated in either fixture, so LTS compliance is **unmeasured**).

### Migration steps (ordered)

1. **First, and before any v3 publisher goes live:** set the explicit codec pin for `publish()` (or convert `services/edge-cache/consumer` off the msgpack reader). Hard ordering constraint from the changelog: "the codec pin must be set before the first `publish()` call."
2. Remove the `flush_interval` key from the config consumed by `services/ingest/loader` — must precede the v3 bump for that service, or `parseConfig(raw)` raises `ConfigKeyError` on first load.
3. Convert `services/dispatch/retry-metrics` to the single-`(context)` `onRetry` callback (do this with the bump; otherwise retry counters silently go to zero with no error).
4. Make `scripts/shutdown-hook` await the now-Promise-returning `queue.drain()` before process exit.
5. No action for `Logger.warnOnce`, `connect()` timeouts, or `Queue.peek()` (see rejected candidates).

Steps 2–4 are order-independent relative to each other; only step 1 carries a stated ordering constraint.

## Evidence

Findings block — `expected` is what the v3 changelog requires; `actual` is what the usage inventory records.

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | ingest_loader_parseconfig | no unknown keys in parsed config (v3 `parseConfig` raises `ConfigKeyError` on unknown keys) | `parseConfig(raw)` with config that "still contains the deprecated `flush_interval` key kept \"for reference\"" | drift |
| usage-inventory.md | dispatch_retry_metrics_onretry | single-object callback `onRetry((context) => ...)` | `onRetry((attempt, error) => ...)` — "Two-argument callback records retry counters" | drift |
| usage-inventory.md | edge_cache_consumer_encoding | explicit codec pin set before first `publish()`, or consumer decodes the new JSON default | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo" | drift |
| usage-inventory.md | shutdown_hook_queue_drain | awaited `queue.drain()` Promise before process exit | `queue.drain()` "Called synchronously as the last line before process exit" | drift |
| usage-inventory.md | logging_warnonce | no `Logger.warnOnce` call sites (API removed in v3) | `Logger.warn` — "No `warnOnce` call sites found (grep returned zero)" | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | explicit timeout passed (default lowered 30s → 10s; explicit call sites unaffected) | `connect({ timeout: 20_000 })` — "Every `connect()` call site passes an explicit timeout" | no-finding |
| usage-inventory.md | billing_exporter_queue_peek | no migration required (`Queue.peek()` is a new additive API) | `Queue.peek()` (planned) — "Not yet using it; listed from the design doc" | no-finding |

## Assumptions

- The usage inventory is complete and current for the whole repo, as its title states ("repo-wide grep, current main"); no source code was opened to re-verify it, per the task's two-fixture restriction.
- "Every `connect()` call site passes an explicit timeout" is taken at face value, so item 6 is scoped out even though `20_000` ms is itself below the old 30s default (that is a pre-existing choice, not a v3 change).
- Only changelog items describing behavior/API changes were treated as candidate breaks; items 7, 9, and 10 are recorded but not adjudicated as findings rows since the prompt fixes the seven row keys.
- Runtime versions, actual publish/consume volumes, allocation rates, and any test results are **unmeasured** — neither fixture contains them and nothing was executed.

## Stop Condition

Stop when this artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command-rep1.md` with the headings Goal, Result, Evidence, Assumptions, Stop Condition; every impactful breaking change is mapped to a call site with quoted inventory evidence; the ordering constraint is stated; a rejected-candidates section is present; and exactly one findings table contains one row per the seven required `location` / `key` pairs with verdicts of `drift` or `no-finding`. All conditions are met. No commits, installs, or network access were performed; no benchmark metadata was read.
