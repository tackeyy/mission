# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

- Task id: `tail-dependency-upgrade-impact`
- Task category: refactoring
- Arm: `claude_code_goal_command`
- Sources used (exactly these two, read in full):
  - `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
  - `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

## Goal

Produce this artifact at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command-rep2.md`,
mapping every impactful relaykit v3 breaking change to the concrete call site it
affects with quoted inventory evidence, stating migration steps including any
ordering constraint, and separating confirmed findings from rejected candidates
(changelog entries that look breaking but affect no call site).

## Result

Four of the ten changelog entries are breaking **and** hit a real call site in
the inventory. Two entries are genuinely breaking in the library but hit **zero**
call sites, so they are rejected. The remaining four entries are not breaking at
all.

Confirmed impactful breaking changes (4):

1. **Changelog #1 — strict `parseConfig`** hits `services/ingest/loader`.
2. **Changelog #2 — `onRetry` signature change** hits `services/dispatch/retry-metrics`.
3. **Changelog #3 — `publish()` default encoding msgpack → JSON** hits `services/edge-cache/consumer`, and carries the only explicit **ordering constraint** in the changelog.
4. **Changelog #4 — `Queue.drain()` now async** hits `scripts/shutdown-hook`.

Rejected candidates (2): changelog #5 (`Logger.warnOnce` removal) and changelog
#6 (`connect()` default timeout lowered). Both are real breaking changes in the
library, but the inventory shows no call site that can be affected.

### Findings table

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | billing_exporter_queue_peek | Call site must be broken by a v3 breaking change to count as impact; `Queue.peek()` is listed in the changelog as `New \`Queue.peek()\` API` (additive, entry 8) and the site is `\`Queue.peek()\` (planned)` / `Not yet using it; listed from the design doc.` | Additive new API, and the call site does not exist yet ("Not yet using it") — no code to migrate | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | Changelog entry 6 states `Call sites passing an explicit timeout are unaffected.`; inventory must show explicit timeouts for the site to be unaffected | `connect({ timeout: 20_000 })` with `Every \`connect()\` call site passes an explicit timeout.` — explicit timeout at every site, so the lowered 10s default never applies | no-finding |
| usage-inventory.md | dispatch_retry_metrics_onretry | Callback must use the v3 single `(context)` object signature, since `two-argument callbacks are no longer invoked` | `onRetry((attempt, error) => ...)` — two-argument callback; `Two-argument callback records retry counters.` It will silently stop being invoked under v3 | drift |
| usage-inventory.md | edge_cache_consumer_encoding | Consumer must either decode JSON or rely on a codec pin set before the first `publish()`; changelog: `Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first \`publish()\` call.` | `Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo.` — v3 publishes JSON by default, so the msgpack reader breaks on the first message | drift |
| usage-inventory.md | ingest_loader_parseconfig | Config passed to `parseConfig` must contain no unknown keys, since v3 raises `ConfigKeyError` for them | `parseConfig(raw)` where `Config file still contains the deprecated \`flush_interval\` key kept "for reference".` — the stale key now raises `ConfigKeyError` at load time | drift |
| usage-inventory.md | logging_warnonce | `Logger.warnOnce` has been removed, so any `warnOnce` call site would need to move to `Logger.warn` with a dedupe key | `Logger.warn` with `No \`warnOnce\` call sites found (grep returned zero).` — nothing to migrate | no-finding |
| usage-inventory.md | shutdown_hook_queue_drain | `queue.drain()` result must be awaited, because in v3 it `is now async and returns a Promise; synchronous callers will no longer block until the queue is empty` | `queue.drain()` `Called synchronously as the last line before process exit.` — the returned Promise is dropped and the process exits before the queue empties | drift |

## Evidence

### Confirmed finding 1 — strict `parseConfig` breaks `services/ingest/loader`

- Changelog (entry 1), verbatim: "``parseConfig`` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Inventory call site, verbatim: `services/ingest/loader` | `parseConfig(raw)` | "Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Mapping: the exact identifier `flush_interval` is an unknown key in v3. Under v2 it was "silently ignored"; under v3 the same config raises `ConfigKeyError`.
- Failure mode: hard failure at config load, i.e. the ingest service fails to start.

### Confirmed finding 2 — `onRetry` signature change breaks `services/dispatch/retry-metrics`

- Changelog (entry 2), verbatim: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Inventory call site, verbatim: `services/dispatch/retry-metrics` | `onRetry((attempt, error) => ...)` | "Two-argument callback records retry counters."
- Mapping: the registered callback is literally the `(attempt, error)` two-argument shape the changelog names as no longer invoked.
- Failure mode: **silent**. No exception is raised; the hook simply stops firing and retry counters go to zero. This is the most easily missed of the four because nothing crashes.

### Confirmed finding 3 — `publish()` encoding change breaks `services/edge-cache/consumer` (ordering constraint)

- Changelog (entry 3), verbatim: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Inventory call site, verbatim: `services/edge-cache/consumer` | "subscribes to `publish()` output" | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Mapping: producer defaults flip to JSON while the consumer still runs a msgpack reader, and the quoted evidence confirms the escape hatch is not in place ("no codec pin is set anywhere in the repo").
- **Ordering constraint (the only one stated in the changelog):** the codec pin "must be set before the first `publish()` call". Any publish that happens before the pin is installed is emitted in the new JSON format and is undecodable by the existing msgpack consumer — data already on the wire cannot be fixed retroactively by pinning later.
- Failure mode: decode errors / dropped messages at the consumer, starting with the very first message published under v3.

### Confirmed finding 4 — async `Queue.drain()` breaks `scripts/shutdown-hook`

- Changelog (entry 4), verbatim: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Inventory call site, verbatim: `scripts/shutdown-hook` | `queue.drain()` | "Called synchronously as the last line before process exit."
- Mapping: the site is exactly the "synchronous caller" pattern the changelog calls out, and it is "the last line before process exit", so nothing after it keeps the process alive while the Promise settles.
- Failure mode: **silent data loss on shutdown**. The process exits with queue items still pending; no error is raised.

### Rejected candidates

These changelog entries are genuinely breaking API changes and would normally
warrant migration work — that is why they look suspicious — but the inventory
evidence shows no call site they can reach.

- **Changelog #5 — `Logger.warnOnce` removed.** Verbatim: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key." A removed public method is prima facie breaking. Rejected on inventory evidence: `services/*/logging` | `Logger.warn` | "No `warnOnce` call sites found (grep returned zero)." The repo already uses `Logger.warn`, and the grep result is explicitly zero, so there is nothing to migrate.
- **Changelog #6 — `connect()` default timeout lowered 30s → 10s.** Verbatim: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected." A silent behavior change to a default timeout is a classic source of upgrade regressions, so it warrants a look. Rejected on inventory evidence: `services/*/bootstrap` | `connect({ timeout: 20_000 })` | "Every `connect()` call site passes an explicit timeout." The explicit `20_000` overrides the default in both v2 and v3, and the changelog itself carves out this exact case. Note the value `20_000` sits between the old and new defaults, which is what makes this look alarming at a glance — but the default is never consulted, so the gap is irrelevant.
- **Changelog #8 — new `Queue.peek()` API / `services/billing/exporter`.** Verbatim changelog: "New `Queue.peek()` API." This is additive, not breaking. The inventory entry `services/billing/exporter` | `Queue.peek()` (planned) | "Not yet using it; listed from the design doc." looks like a call site but is not one — it is a design-doc aspiration with no code. Rejected on both grounds (non-breaking entry, non-existent call site).

### Non-impactful changelog entries (no call-site analysis needed)

- Entry 7: "Internal buffer pooling rewritten; ~12% lower allocation rate." — internal, performance-only. The ~12% figure is an upstream claim; **unmeasured** in this repo.
- Entry 9: "Documentation moved to a new site." — no code impact.
- Entry 10: "Minimum supported runtime raised to LTS." — an environment constraint, not an API break. The inventory contains **no** runtime-version information, so whether this repo's runtime satisfies "LTS" is **unmeasured** from the two permitted fixtures.

## Migration steps (with ordering constraints)

The only ordering constraint stated by the changelog is the codec pin
(entry 3). It dictates step 1.

1. **Set the codec pin first — before any v3 `publish()` runs.** Changelog: "the codec pin must be set before the first `publish()` call." Pin msgpack explicitly on the producer so the wire format is unchanged by the upgrade. Doing this after deploying v3 is too late for every message already published in JSON. (Alternative: cut the `services/edge-cache/consumer` msgpack reader over to JSON first; but that only works if producer and consumer flip atomically, which the fixtures give no mechanism for — so pinning is the safe path.)
2. **Remove `flush_interval` from the ingest config** before the v3 loader runs, so `parseConfig(raw)` does not raise `ConfigKeyError` on startup. This is a startup-blocking failure, so it must precede any v3 deploy of `services/ingest/loader`.
3. **Convert the `onRetry` callback** in `services/dispatch/retry-metrics` from `(attempt, error)` to the single `(context)` object. Because the v2 form fails silently under v3, land this in the same change as the upgrade — a later fix means an unknown window of missing retry counters with no alarm.
4. **Await `queue.drain()`** in `scripts/shutdown-hook` and make the surrounding shutdown path async so the process does not exit until the Promise resolves. Same reasoning as step 3: silent failure, so it must not lag the upgrade.
5. **Then upgrade the dependency to v3** and verify. Steps 1–4 are all safe to land while still on v2 (an explicit codec pin, a removed dead config key, an awaited call, and a callback rewrite are all v2-compatible except the `onRetry` shape — see the caveat below).

**Ordering summary:** step 1 strictly before the first v3 `publish()`; step 2
strictly before the first v3 `parseConfig` call; steps 3–4 no later than the
v3 cutover.

**Caveat on step 3's ordering:** the `onRetry` rewrite is the one change that is
*not* forward-and-backward compatible — a `(context)` callback under v2 would
receive `attempt` as its first argument. So it must land **with** the upgrade,
not before it. The fixtures do not describe a shim or dual-signature support, so
whether an interim compatible form exists is **unmeasured**.

## Assumptions

- The two named fixture files are the complete and authoritative source of truth. Nothing else under `benchmarks/mission-vs-goal/` was opened, read, grepped, or listed, per the run rules; no repository source code was inspected to corroborate the inventory.
- The inventory's claim of repo-wide grep coverage ("repo-wide grep, current main") is taken at face value. In particular the zero-result claims — "No `warnOnce` call sites found (grep returned zero)" and "Every `connect()` call site passes an explicit timeout" — are load-bearing for the two rejections and were **not independently verified**.
- Changelog entries are treated as an exhaustive list of v3 changes; undocumented behavior changes are out of scope and **unmeasured**.
- Glob-style inventory paths (`services/*/logging`, `services/*/bootstrap`) are read as covering all matching services uniformly, as the Detail column asserts.
- No code was changed, no commands were run, no tests were executed, and no packages were installed. Runtime behavior of the upgrade is therefore entirely **unmeasured**; every claim above is a static reading of the two fixtures.
- Severity/failure-mode labels ("silent", "startup-blocking") are inferences from the changelog wording, not observed behavior.

## Stop Condition

Stop when all of the following hold:

1. The artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command-rep2.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition. — met.
2. Every impactful breaking change is mapped to its concrete call site with a quoted identifier or value from the fixture. — met (4 mappings, each quoting the inventory row verbatim).
3. Migration steps are stated, including the codec-pin ordering constraint. — met (5 steps plus an ordering summary).
4. A rejected-candidates section explains, with inventory evidence, why each suspicious-looking breaking change affects no call site. — met (changelog #5, #6, and the `Queue.peek()` non-call-site).
5. Exactly one findings table is present with the required header and exactly one row per adjudicated item, using the mandated `location`/`key` strings and only `drift` / `no-finding` verdicts. — met (7 rows).
6. No commit, push, install, or network access occurred; exactly one file was written. — met.

No benchmark-superiority claim is made here; this artifact covers only the
`tail-dependency-upgrade-impact` task.
