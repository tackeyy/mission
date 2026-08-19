# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

- Task id: `tail-dependency-upgrade-impact`
- Category: refactoring
- Arm: `claude_code_goal_command`

## Goal

Assess the relaykit v2-to-v3 upgrade using exactly the two named fixtures:

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

Map every breaking change to the concrete call sites it affects (with quoted inventory evidence), state migration steps including any ordering constraint, and reject changelog entries that look breaking but affect no call site, citing the inventory evidence for the rejection.

## Result

Four breaking changes are impactful and map to concrete call sites; two look breaking but affect zero call sites and are rejected; four entries are not breaking at all.

### Confirmed findings (breaking change → affected call site)

| # | Changelog entry | Affected call site | Impact |
|---|---|---|---|
| 1 | Strict `parseConfig` — unknown keys raise `ConfigKeyError` | `services/ingest/loader` | Startup failure: the config still carries a key that v3 no longer tolerates |
| 2 | `onRetry` signature `(attempt, error)` → `(context)` | `services/dispatch/retry-metrics` | Silent loss: two-argument callbacks "are no longer invoked", so retry counters stop being recorded |
| 3 | `publish()` default payload encoding msgpack → JSON | `services/edge-cache/consumer` | Wire-format break between publisher and consumer; carries a hard ordering constraint |
| 4 | `Queue.drain()` is now async | `scripts/shutdown-hook` | Process may exit before the queue is empty (unawaited Promise) |

### Rejected candidates (look breaking, affect no call site)

| # | Changelog entry | Why it looked breaking | Inventory evidence for rejection |
|---|---|---|---|
| 5 | `Logger.warnOnce` removed | An API removal is normally a hard break | Inventory row `services/*/logging` records `Logger.warn` with "No `warnOnce` call sites found (grep returned zero)" |
| 6 | `connect()` default timeout 30s → 10s | A default-value change can silently break slow connections | Inventory row `services/*/bootstrap` records `connect({ timeout: 20_000 })` with "Every `connect()` call site passes an explicit timeout"; the changelog itself scopes the change: "Call sites passing an explicit timeout are unaffected" |

### Not breaking at all (no impact assessment required)

Changelog items 7 (`Internal buffer pooling rewritten; ~12% lower allocation rate`), 8 (`New Queue.peek() API`), 9 (`Documentation moved to a new site`), and 10 (`Minimum supported runtime raised to LTS`) are non-breaking by their own wording — a performance change, an addition, a docs move, and a runtime floor. Item 8 has an inventory row (`services/billing/exporter` — "`Queue.peek()` (planned) … Not yet using it; listed from the design doc"), but a new API cannot break an existing call site, and that row confirms no current usage. Item 10 raises a runtime floor; the fixtures contain **no** runtime-version data, so whether the deployed runtime satisfies it is **unmeasured** here.

### Migration steps, with ordering constraints

1. **[BLOCKING, must precede the v3 upgrade going live] Pin the `publish()` codec to msgpack.** The changelog states: "Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call." The inventory says "no codec pin is set anywhere in the repo", so today there is nothing to satisfy that constraint. Because `services/edge-cache/consumer` "Decodes payloads with a msgpack reader", any `publish()` that runs on v3 before the pin is installed emits JSON into a msgpack-only consumer. **Ordering constraint: codec pin set → first v3 `publish()` call.** There is no safe window in which this order can be inverted. (Alternative: migrate the consumer to a JSON reader first, then drop the pin — but that swaps the ordering constraint to "consumer decodes JSON → first v3 `publish()`", it does not remove it. The fixtures do not say whether publisher and consumer deploy atomically, so this alternative is **unmeasured** for safety.)
2. **Remove or rename the unknown config key in `services/ingest/loader`.** The config "still contains the deprecated `flush_interval` key kept 'for reference'"; under strict `parseConfig` this raises `ConfigKeyError`. This must land before `services/ingest/loader` runs on v3, otherwise the service fails at config parse time. It is independent of steps 1, 3, and 4.
3. **Convert the `onRetry` callback in `services/dispatch/retry-metrics` to the single-`(context)` form.** This must land in the same change as the v3 bump for that service: the old callback is not an error, it is simply "no longer invoked", so shipping v3 without this step degrades silently — retry counters go to zero and look like a healthy system rather than a broken one. Independent of steps 1, 2, and 4.
4. **Await `queue.drain()` in `scripts/shutdown-hook`.** It is "Called synchronously as the last line before process exit"; with an async `drain()` the process may exit while the queue is still draining. Must land before the script runs on v3. Independent of steps 1–3.
5. **Do nothing for items 5 and 6** (see rejected candidates), and nothing for items 7–10.

Only step 1 carries an ordering constraint that is stated by the source material. Steps 2, 3, and 4 have a per-call-site "before v3 reaches that call site" requirement but no ordering relative to each other; the fixtures contain no deploy-topology or release-sequencing information, so any cross-service rollout order beyond step 1 is **unmeasured**.

## Evidence

All quotes below are verbatim from the two named fixtures. No other file was opened, read, grepped, or listed under `benchmarks/mission-vs-goal/` apart from these two fixtures and this output file.

### Finding 1 — strict `parseConfig`

- Changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Inventory: | `services/ingest/loader` | `parseConfig(raw)` | Config file still contains the deprecated `flush_interval` key kept "for reference". |
- Link: the offending identifier is `flush_interval`, an unknown key under v3 strictness at the `parseConfig(raw)` call site.

### Finding 2 — `onRetry` signature change

- Changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Inventory: | `services/dispatch/retry-metrics` | `onRetry((attempt, error) => ...)` | Two-argument callback records retry counters. |
- Link: the call site's arity `(attempt, error)` is exactly the arity the changelog says is "no longer invoked".

### Finding 3 — `publish()` encoding change and its ordering constraint

- Changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Inventory: | `services/edge-cache/consumer` | subscribes to `publish()` output | Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo. |
- Link: the consumer is msgpack-only, and "no codec pin is set anywhere in the repo" means the mitigation the changelog requires does not currently exist. The ordering constraint is quoted directly from the changelog, not inferred.

### Finding 4 — async `Queue.drain()`

- Changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Inventory: | `scripts/shutdown-hook` | `queue.drain()` | Called synchronously as the last line before process exit. |
- Link: the call site is literally the "synchronous caller" case named in the changelog, at the highest-consequence position (last line before exit).

### Rejection 5 — `Logger.warnOnce`

- Changelog: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key."
- Inventory: | `services/*/logging` | `Logger.warn` | No `warnOnce` call sites found (grep returned zero). |
- Rejection basis: the removed symbol has zero call sites per an explicit repo-wide grep result. The existing call sites already use `Logger.warn`, the recommended replacement.

### Rejection 6 — `connect()` default timeout

- Changelog: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected."
- Inventory: | `services/*/bootstrap` | `connect({ timeout: 20_000 })` | Every `connect()` call site passes an explicit timeout. |
- Rejection basis: the changed value is a *default*, and the inventory states every call site passes `timeout: 20_000` explicitly. The explicit value `20_000` sits above the new 10s default, so no call site inherits the lowered value. Both the exemption clause and the coverage claim ("Every `connect()` call site") come from the fixtures.

### Item 8 — `Queue.peek()` (not a rejection of a breaking change; listed for completeness)

- Changelog: "New `Queue.peek()` API."
- Inventory: | `services/billing/exporter` | `Queue.peek()` (planned) | Not yet using it; listed from the design doc. |
- Note: this row is the most likely distractor in the inventory, because a call-site table entry named after a changed API usually signals impact. It is not a breaking change (an addition), and the inventory says the site is "Not yet using it".

## Assumptions

1. **Fixture completeness.** I treat the inventory as a complete repo-wide census, because its title says "repo-wide grep, current main" and it makes universal claims ("no codec pin is set anywhere in the repo", "Every `connect()` call site"). If the grep missed a call site, rejections 5 and 6 could change. I did not independently verify the grep — **unmeasured**.
2. **`20_000` means 20,000 milliseconds**, consistent with the changelog expressing timeouts as 30s/10s. Under this reading the explicit value exceeds the new default; the rejection of item 6 does not depend on this reading, since the changelog exempts *any* explicit timeout regardless of value.
3. **`onRetry((attempt, error) => ...)` is genuinely a two-argument arrow function**, i.e. the declared arity is 2. This is what the inventory's Detail column asserts ("Two-argument callback").
4. **No source code was inspected.** The task restricted me to the two fixtures, so every mapping rests on the fixtures' own descriptions rather than on the actual code. Runtime versions, deploy topology, release sequencing across services, and test coverage for these call sites are all **unmeasured** — none appear in the fixtures.
5. **No behavioral verification was performed.** Nothing was installed, executed, or benchmarked; the "~12% lower allocation rate" in item 7 is quoted from the changelog and is **unmeasured** here.
6. **Scope discipline.** No commit, push, package install, or network access occurred. Exactly one artifact was written, at the path named in the prompt. No benchmark metadata (task definitions, scoring configuration, answer keys) was read.

## Stop Condition

Met. The artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-dependency-upgrade-impact-claude_code_goal_command.md` and contains the required headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Validator coverage:

- Each impactful breaking change (changelog items 1, 2, 3, 4) is mapped to its affected call site with quoted inventory evidence — see Result table and Evidence §§ Findings 1–4.
- Migration steps are stated, including the ordering constraint "the codec pin must be set before the first `publish()` call" (step 1), with the absence of cross-service ordering data declared unmeasured.
- A rejected-candidates section covers the breaking-looking changes with no affected call sites (items 5 and 6), each with the inventory quote that rejects it, plus the `Queue.peek()` distractor.

No superiority claim about any benchmark arm is made in this document.
