# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-07-claude-goal-vs-mission-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: assess the relaykit v2→v3 upgrade using exactly `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md` and `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`. Map every breaking change to the concrete call sites it affects, including any required migration-order constraint. Changelog entries that look breaking but affect no call site must be rejected with inventory evidence.

## Arm

`claude_code_goal_command` — completion controlled by Claude Code's built-in `/goal` command.

# Result

## Confirmed breaking changes → affected call sites

1. **Changelog #1 — `parseConfig` strict mode**
   Changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
   Affected call site: `services/ingest/loader` — `parseConfig(raw)`.
   Inventory evidence: "Config file still contains the deprecated `flush_interval` key kept "for reference"."
   Impact: on v3 this call site's config still has the unknown `flush_interval` key, which will now raise `ConfigKeyError` at parse time instead of being silently ignored.
   Fix: remove (or otherwise stop passing) `flush_interval` from the config consumed by `services/ingest/loader` before the v3 cutover.

2. **Changelog #2 — `onRetry` hook signature change**
   Changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
   Affected call site: `services/dispatch/retry-metrics` — `onRetry((attempt, error) => ...)`.
   Inventory evidence: "Two-argument callback records retry counters."
   Impact: this two-argument callback will no longer be invoked at all under v3, so retry-count telemetry silently stops recording (no crash, silent regression).
   Fix: rewrite the callback to accept a single `context` object.

3. **Changelog #3 — `publish()` default encoding msgpack → JSON**
   Changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
   Affected call site: `services/edge-cache/consumer` — "subscribes to `publish()` output".
   Inventory evidence: "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
   Impact: with no codec pin set anywhere in the repo, `publish()` output flips to JSON on v3 while this consumer keeps decoding as msgpack, breaking decode.
   Migration-order constraint (explicit in changelog): the codec pin "must be set before the first `publish()` call." The codec-pin fix must be deployed in the same change as, or strictly before, the relaykit v3 version bump — never after — or every `publish()` call between the bump and the pin lands as an already-broken message for this consumer.

4. **Changelog #4 — `Queue.drain()` becomes async**
   Changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
   Affected call site: `scripts/shutdown-hook` — `queue.drain()`.
   Inventory evidence: "Called synchronously as the last line before process exit."
   Impact: this call site is a plain synchronous call as the last statement before `process.exit`; under v3 it no longer blocks until the queue empties, so the process can exit while messages are still in-flight.
   Fix: await/chain the Promise returned by `drain()` and delay process exit until it resolves.

## Rejected candidates (look breaking, no affected call site)

1. **Changelog #5 — `Logger.warnOnce` removed**
   Inventory evidence (`services/*/logging` row): "No `warnOnce` call sites found (grep returned zero)."
   Why it looked suspicious: removing a public API is a textbook breaking change.
   Why rejected: the inventory's own repo-wide grep for `warnOnce` returned zero call sites — there is nothing in this codebase for the removal to break.

2. **Changelog #6 — `connect()` default timeout 30s → 10s**
   Inventory evidence (`services/*/bootstrap` row): `connect({ timeout: 20_000 })` — "Every `connect()` call site passes an explicit timeout."
   Why it looked suspicious: a changed default value is a common source of silent behavior regressions.
   Why rejected: the changelog itself scopes this ("Call sites passing an explicit timeout are unaffected"), and the inventory confirms every `connect()` call site in this repo passes an explicit timeout (`20_000`), so the new lower default is never reached.

3. **Changelog #8 — new `Queue.peek()` API**
   Inventory evidence (`services/billing/exporter` row): "`Queue.peek()` (planned) ... Not yet using it; listed from the design doc."
   Why it looked suspicious: any new/changed API surface is worth checking against usage.
   Why rejected: this is an additive API and the changelog doesn't describe it as breaking; the only "usage" is a planned/design-doc reference with no call site actually invoking it in the current codebase, so nothing can break.

## Not evaluable from these two fixtures (unmeasured)

- Changelog #7 ("Internal buffer pooling rewritten; ~12% lower allocation rate.") and #9 ("Documentation moved to a new site.") are not breaking changes per the changelog's own wording — no call-site mapping applies, and they are not "look-breaking" candidates either.
- Changelog #10 ("Minimum supported runtime raised to LTS.") reads as a breaking constraint, but `usage-inventory.md` contains no row describing which runtime/interpreter version any call site actually runs on. This is unmeasured: the inventory gives no evidence to either confirm or reject impact for this entry.

## Migration steps (with ordering constraints)

1. Remove/stop passing the deprecated `flush_interval` key from the config read by `services/ingest/loader` (addresses changelog #1). No dependency on the other steps, but must land at or before the v3 cutover, or the service fails to boot (`ConfigKeyError`).
2. Rewrite the `onRetry` callback in `services/dispatch/retry-metrics` to the single-`context` signature (addresses changelog #2). Independent of the other steps; must land at or before cutover to avoid silently losing retry-count telemetry.
3. Set an explicit codec pin for `publish()` covering the path consumed by `services/edge-cache/consumer` (addresses changelog #3). **Hard ordering constraint, stated explicitly in the changelog**: the pin must be in effect before the first `publish()` call after the upgrade. Ship this in the same deploy as, or strictly before, the relaykit v3 dependency bump — never after.
4. Update `scripts/shutdown-hook` to await the Promise returned by `queue.drain()` before letting the process exit (addresses changelog #4). Independent of the other steps; must land at or before cutover to avoid dropped in-flight messages on shutdown.
5. Steps 1, 2, and 4 have no ordering dependency on each other or on step 3. Step 3 is the only step in this set with a documented "must happen before X" constraint. All four should be merged before, or atomically with, the relaykit v3 version bump, since each becomes a live break the instant v3 is running.

# Evidence

Direct quotes used above, grouped by changelog item:

- #1 changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)." — inventory: "Config file still contains the deprecated `flush_interval` key kept "for reference"." (`services/ingest/loader`)
- #2 changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked." — inventory: "Two-argument callback records retry counters." (`services/dispatch/retry-metrics`, `onRetry((attempt, error) => ...)`)
- #3 changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call." — inventory: "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo." (`services/edge-cache/consumer`)
- #4 changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty." — inventory: "Called synchronously as the last line before process exit." (`scripts/shutdown-hook`, `queue.drain()`)
- #5 changelog: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key." — inventory: "No `warnOnce` call sites found (grep returned zero)." (`services/*/logging`)
- #6 changelog: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected." — inventory: "Every `connect()` call site passes an explicit timeout." (`services/*/bootstrap`, `connect({ timeout: 20_000 })`)
- #8 changelog: "New `Queue.peek()` API." — inventory: "`Queue.peek()` (planned) ... Not yet using it; listed from the design doc." (`services/billing/exporter`)
- #10 changelog: "Minimum supported runtime raised to LTS." — no matching row exists in `usage-inventory.md`; absence noted, not inferred.

# Assumptions

- Both fixture files are treated as complete for this exercise: `upgrade-changelog.md` is labeled "upstream, verbatim" and `usage-inventory.md` is labeled "repo-wide grep, current main," so an entry's absence from the inventory is treated as "no known call site" rather than "not checked."
- The `services/*/logging` and `services/*/bootstrap` inventory rows are read as aggregating all matching call sites (wildcard path), so their stated grep results ("zero," "every call site") are taken at face value rather than re-derived.
- The `services/billing/exporter` row is treated as not-yet-live code (explicitly "planned," "not yet using it," sourced from a design doc) and therefore not a real call site for breaking-change impact purposes today.
- No independent verification of either fixture's accuracy was performed or is claimed — this assessment is a direct mapping between the two named files only, per the task's scope restriction.
- Changelog #10 (minimum runtime) is explicitly called out as unmeasured rather than guessed at, since no runtime/interpreter data appears anywhere in `usage-inventory.md`.

# Stop Condition

Met. This artifact has been written to the exact required path (`benchmarks/mission-vs-goal/run-output/2026-07-07-claude-goal-vs-mission-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command.md`) and contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition). It maps each impactful breaking change (changelog #1–#4) to its affected call site with quoted inventory evidence, states migration steps including the one explicit ordering constraint (codec pin before first `publish()` call), and includes a rejected-candidates section (changelog #5, #6, #8) with the inventory evidence showing why each has no affected call site. No commit, push, package install, or network access was performed while producing this artifact.
