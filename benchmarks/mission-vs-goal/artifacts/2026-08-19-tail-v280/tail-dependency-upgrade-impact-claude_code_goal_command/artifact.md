# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

Task id: `tail-dependency-upgrade-impact`
Category: refactoring
Arm: `claude_code_goal_command`

Sources used (exactly these two, nothing else under `benchmarks/mission-vs-goal/` was read):

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

## Goal

Produce this artifact at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280/tail-dependency-upgrade-impact-claude_code_goal_command.md`, containing the headings Goal, Result, Evidence, Assumptions, and Stop Condition, and:

1. map every changelog breaking change that actually affects code to the concrete call site it affects, quoting the inventory as evidence;
2. state migration steps including any required ordering constraint;
3. list rejected candidates — changelog entries that look breaking but affect no call site — with the inventory evidence that rejects them.

No commits, pushes, installs, or network access. No claim of benchmark superiority.

## Result

### Confirmed impactful breaking changes (4)

| # | Changelog entry | Affected call site | Failure mode if migrated as-is |
|---|---|---|---|
| 1 | `parseConfig` strict — unknown keys raise `ConfigKeyError` | `services/ingest/loader` | Startup-time throw: the config still carries a key relaykit v3 does not know. |
| 2 | `onRetry` signature `(attempt, error)` → `(context)` | `services/dispatch/retry-metrics` | Silent metric loss: "two-argument callbacks are no longer invoked" — retry counters stop recording, with no error raised. |
| 3 | `publish()` default encoding msgpack → JSON | `services/edge-cache/consumer` | Wire-format break between producer and consumer: producer emits JSON, consumer decodes msgpack. |
| 4 | `Queue.drain()` now async, returns a Promise | `scripts/shutdown-hook` | Data loss on shutdown: the process exits without waiting for the queue to empty. |

Ranked by silence of failure (most dangerous first): #2 (silent, no error), #3 (runtime decode failure in a separate service, possibly only under load/deploy skew), #4 (silent partial drain at exit), #1 (loud, fails fast at startup).

### Rejected candidates (4 that look breaking, plus 2 clearly non-code entries)

| Changelog entry | Why it looks breaking | Why it is not a real finding here |
|---|---|---|
| 5. `Logger.warnOnce` removed | An API removal is normally a hard break. | Inventory shows zero call sites: `services/*/logging` uses `Logger.warn`, and the detail column reads "No `warnOnce` call sites found (grep returned zero)." Nothing to migrate. |
| 6. `connect()` default timeout 30s → 10s | A silent behavioral change to a default is a classic latent break (long-running connects would start failing). | The change only bites call sites relying on the default. Inventory: `connect({ timeout: 20_000 })` and "Every `connect()` call site passes an explicit timeout." The changelog itself scopes it out: "Call sites passing an explicit timeout are unaffected." |
| 8. New `Queue.peek()` API | It appears in the inventory next to a named service, which reads like a live dependency. | The API is additive, and the inventory row is a plan, not a call site: "`Queue.peek()` (planned)" / "Not yet using it; listed from the design doc." No code to change. |
| 7. Internal buffer pooling rewritten, "~12% lower allocation rate" | A rewrite of internals can change behavior. | Stated as a performance characteristic, not an API or contract change, and no inventory row references buffer pooling. Not verifiable from these two fixtures either way — the 12% figure is upstream's claim and is **unmeasured here**. |
| 9. Documentation moved to a new site | — | No code impact; affects links only. No inventory row. |
| 10. Minimum supported runtime raised to LTS | Could block the upgrade entirely if the deployment runtime is older. | **Unmeasured, not rejected on evidence.** The inventory contains no runtime/engine information, so neither fixture can confirm or refute compliance. Treated as an open pre-flight check, not as a confirmed or rejected finding. |

### Migration steps, with ordering constraints

Hard ordering constraint (explicit in the changelog):

> "Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."

Combined with the inventory's "no codec pin is set anywhere in the repo", this means the codec pin must be added and be in effect **before** any v3 `publish()` executes — i.e. the pin lands in the same deploy that introduces v3 on the producer side, at bootstrap/init before the first publish, not lazily on first use.

Recommended order:

1. **Pre-flight (blocking, unmeasured input needed):** confirm the deployment runtime satisfies "Minimum supported runtime raised to LTS". Neither fixture records the current runtime; obtain this outside these fixtures before scheduling the upgrade.
2. **Add the explicit codec pin (msgpack) at producer init — before upgrading.** This is the ordering-critical step. Doing this first keeps `services/edge-cache/consumer` ("Decodes payloads with a msgpack reader") working across the cut-over, and satisfies "the codec pin must be set before the first `publish()` call". Deferring the pin until after v3 is live means the first `publish()` emits JSON and the consumer breaks.
3. **Clean the ingest config:** remove or relocate the `flush_interval` key from the config consumed by `services/ingest/loader` ("Config file still contains the deprecated `flush_interval` key kept \"for reference\""). Do this before v3 is live, or `parseConfig(raw)` raises `ConfigKeyError` at startup. Its "for reference" purpose can be preserved as a comment rather than a live key.
4. **Convert the retry hook:** rewrite `services/dispatch/retry-metrics` from `onRetry((attempt, error) => ...)` to the single `(context)` form, reading attempt and error off `context`. This must land in the same release as v3 — a two-argument callback under v3 is never invoked, so the counters go silently to zero rather than failing loudly.
5. **Await the drain:** in `scripts/shutdown-hook`, make the exit path async and `await queue.drain()` before process exit. The inventory notes it is "Called synchronously as the last line before process exit", so under v3 the returned Promise would be dropped and the process would exit early.
6. **Upgrade the dependency to v3 and deploy.** Steps 2–5 are all pre-upgrade or same-release changes; step 2 is the only one with an explicit upstream-stated ordering requirement.
7. **Post-upgrade verification:** confirm retry counters are still non-zero (guards the silent failure in step 4), confirm `services/edge-cache/consumer` still decodes successfully (guards steps 2/3), and confirm queue depth reaches zero before exit in a shutdown test. Optionally, decouple later: migrate the consumer to JSON and drop the codec pin as a separate change, after v3 is stable.

Note on sequencing steps 3–5 relative to the upgrade: steps 3, 4 and 5 are each *backward-incompatible in one direction only* per the fixtures, and the fixtures state no ordering requirement for them beyond "must be in place when v3 goes live." Only step 2 carries an explicit upstream ordering constraint.

## Evidence

Quoted verbatim. Changelog quotes are from `upgrade-changelog.md`; inventory quotes from `usage-inventory.md`.

**Finding 1 — `parseConfig` strictness → `services/ingest/loader`**
- Changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Inventory: call site `services/ingest/loader`, usage "`parseConfig(raw)`", detail "Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Link: the retained key `flush_interval` is exactly the "unknown key" class that v3 now rejects.

**Finding 2 — `onRetry` signature → `services/dispatch/retry-metrics`**
- Changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Inventory: call site `services/dispatch/retry-metrics`, usage "`onRetry((attempt, error) => ...)`", detail "Two-argument callback records retry counters."
- Link: the call site uses precisely the two-argument form the changelog says is "no longer invoked".

**Finding 3 — `publish()` encoding → `services/edge-cache/consumer` (ordering-critical)**
- Changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Inventory: call site `services/edge-cache/consumer`, usage "subscribes to `publish()` output", detail "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Link: the consumer depends on the old msgpack default, and the inventory confirms the mitigation ("codec pin") is absent today.

**Finding 4 — `Queue.drain()` async → `scripts/shutdown-hook`**
- Changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Inventory: call site `scripts/shutdown-hook`, usage "`queue.drain()`", detail "Called synchronously as the last line before process exit."
- Link: the call site is the exact "synchronous caller" pattern the changelog calls out.

**Rejection 5 — `Logger.warnOnce`**
- Changelog: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key."
- Inventory: `services/*/logging` uses "`Logger.warn`", detail "No `warnOnce` call sites found (grep returned zero)."

**Rejection 6 — `connect()` default timeout**
- Changelog: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected."
- Inventory: `services/*/bootstrap` uses "`connect({ timeout: 20_000 })`", detail "Every `connect()` call site passes an explicit timeout."

**Rejection 8 — `Queue.peek()`**
- Changelog: "New `Queue.peek()` API."
- Inventory: `services/billing/exporter`, usage "`Queue.peek()` (planned)", detail "Not yet using it; listed from the design doc."

**Rejection 7 / 9 — buffer pooling and docs**
- Changelog: "Internal buffer pooling rewritten; ~12% lower allocation rate." and "Documentation moved to a new site."
- Inventory: no row mentions buffer pooling or documentation. The inventory header states it is a "repo-wide grep, current main".

**Open item 10 — minimum runtime**
- Changelog: "Minimum supported runtime raised to LTS."
- Inventory: contains no runtime, engine, or platform column. Status: **unmeasured** from the permitted fixtures.

**Coverage check:** the changelog has 10 numbered entries (1–10). This artifact accounts for all 10: entries 1–4 confirmed impactful, 5/6/8 rejected on inventory evidence, 7/9 non-code, 10 unmeasured. The inventory has 7 call-site rows; all 7 are cited above (rows 1–4 as findings, rows 5–7 as rejections).

## Assumptions

- The two named fixtures are the complete and authoritative picture. The inventory self-describes as a "repo-wide grep, current main"; I did not read any source code, and no source code was consulted to corroborate it.
- `services/*/logging` and `services/*/bootstrap` are glob-style aggregate rows covering all matching services; I take the "Every `connect()` call site" and "grep returned zero" statements at face value rather than re-verifying them.
- "Codec pin" is assumed to be a configuration/initialization call on the producer side; the fixtures name the concept but give no API signature, so no concrete pin syntax is proposed here.
- Failure modes ("silent metric loss", "data loss on shutdown") are inferred from the changelog's own wording plus the inventory's description of each call site. They were **not** reproduced or executed — no code was run, so all runtime behavior claims are analytical, not measured.
- The "~12% lower allocation rate" figure is upstream's claim, reproduced verbatim; it is unmeasured here.
- Migration effort, risk of rollback, and deployment topology (e.g. whether producer and consumer deploy atomically) are not described by either fixture; the recommendation to pin the codec before upgrading is deliberately safe under both atomic and staggered deploys.

## Stop Condition

Stop when all of the following hold — all are satisfied by this document:

1. This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280/tail-dependency-upgrade-impact-claude_code_goal_command.md` and contains the headings Goal, Result, Evidence, Assumptions, Stop Condition. ✅
2. Every impactful breaking change is mapped to its concrete call site with quoted inventory evidence (4 mappings, each with a verbatim quote). ✅
3. Migration steps are stated, including the ordering constraint that the codec pin must be set before the first `publish()` call. ✅
4. A rejected-candidates section lists breaking-looking entries with no affected call site, each with the inventory evidence that rejects it. ✅
5. All 10 changelog entries and all 7 inventory rows are accounted for; anything not determinable from the two fixtures is labelled unmeasured. ✅
6. No commit, push, install, or network access was performed, and exactly one file was written. ✅

No further work is required for this task.
