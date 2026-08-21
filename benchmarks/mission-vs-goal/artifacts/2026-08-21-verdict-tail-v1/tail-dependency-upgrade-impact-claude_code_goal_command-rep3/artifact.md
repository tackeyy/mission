# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

Arm: `claude_code_goal_command`
Task category: refactoring

## Goal

Assess the relaykit v2-to-v3 upgrade using exactly the two named fixtures
(`benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
and `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`),
map every breaking change to the concrete call sites it affects, state the
migration steps including any ordering constraint, and reject changelog entries
that look breaking but affect no call site — each rejection backed by quoted
inventory evidence.

## Result

Four changelog entries are impactful (they hit a concrete call site in the
inventory) and three entries that look breaking or disruptive are rejected
because the inventory shows no affected call site.

### Confirmed findings (breaking change → affected call site)

**F1. `parseConfig` strict-unknown-key → `services/ingest/loader`**
Changelog entry 1: "`parseConfig` is now strict: unknown keys raise
`ConfigKeyError` (v2 silently ignored them)."
Inventory row: `` `services/ingest/loader` `` / `` `parseConfig(raw)` `` /
"Config file still contains the deprecated `flush_interval` key kept \"for
reference\"."
Impact: under v3 the retained `flush_interval` key is an unknown key and will
raise `ConfigKeyError` at load, which in v2 was silently ignored. Fix: remove
`flush_interval` from the config file (or move it out of the parsed document)
before running v3.

**F2. `onRetry` signature change → `services/dispatch/retry-metrics`**
Changelog entry 2: "The `onRetry` hook signature changed from `(attempt, error)`
to a single `(context)` object; two-argument callbacks are no longer invoked."
Inventory row: `` `services/dispatch/retry-metrics` `` /
`` `onRetry((attempt, error) => ...)` `` / "Two-argument callback records retry
counters."
Impact: the existing two-argument callback is "no longer invoked", so retry
counters silently stop recording — no exception, just missing metrics. Fix:
rewrite the callback to `onRetry((context) => ...)` and read attempt/error off
the context object.

**F3. `publish()` default encoding msgpack → JSON → `services/edge-cache/consumer`**
Changelog entry 3: "`publish()` default payload encoding changed from msgpack to
JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be
set before the first `publish()` call."
Inventory row: `` `services/edge-cache/consumer` `` / "subscribes to `publish()`
output" / "Decodes payloads with a msgpack reader; no codec pin is set anywhere
in the repo."
Impact: the consumer's msgpack reader will receive JSON payloads after the
upgrade and fail to decode. Because "no codec pin is set anywhere in the repo",
nothing currently protects the wire format. This is the entry that carries the
**migration-order constraint**: the codec pin "must be set before the first
`publish()` call" — a pin applied after publishing has started leaves already-
published payloads in the new format.

**F4. `Queue.drain()` is now async → `scripts/shutdown-hook`**
Changelog entry 4: "`Queue.drain()` is now async and returns a Promise;
synchronous callers will no longer block until the queue is empty."
Inventory row: `` `scripts/shutdown-hook` `` / `` `queue.drain()` `` / "Called
synchronously as the last line before process exit."
Impact: as the last line before process exit, the un-awaited Promise means the
process can exit with a non-empty queue — silent data loss on shutdown. Fix:
`await queue.drain()` (making the hook async) before exiting.

### Migration steps and ordering constraints

1. **Pin the codec before anything publishes (hard ordering constraint).** Set an
   explicit msgpack codec pin so it is in effect "before the first `publish()`
   call" (changelog entry 3). This must precede any v3 process that publishes;
   otherwise `services/edge-cache/consumer` receives JSON its msgpack reader
   cannot decode. Alternative ordering, if you prefer to move to JSON: migrate
   the consumer's reader to JSON *first*, then upgrade the publisher — the
   constraint is that the format decision is fixed before the first publish
   either way, never after.
2. **Remove the deprecated `flush_interval` key** from the ingest config before
   `services/ingest/loader` runs under v3 (F1). This is a startup-blocking
   failure, so it must land no later than the upgrade itself.
3. **Convert the `onRetry` callback** in `services/dispatch/retry-metrics` to the
   single-`(context)` form (F2). No hard ordering against the others, but do it
   in the same change as the upgrade — the failure mode is silent (metrics stop),
   so it will not surface in smoke tests.
4. **Await `queue.drain()`** in `scripts/shutdown-hook` (F4). Also same-change: a
   deferred fix means every shutdown between upgrade and fix can drop queued work.

Only step 1 has an ordering constraint stated by the source (before the first
`publish()`). Steps 2–4 are independent of each other; their relative order is
unconstrained by the fixtures. Whether these code changes were actually applied
or tested is **unmeasured** — this assessment is a static reading of the two
fixture files only, with no build, test, or runtime observation.

### Rejected candidates (look breaking, no affected call site)

**R1. `Logger.warnOnce` removed (changelog entry 5).**
Looks breaking: an outright API removal ("`Logger.warnOnce` has been removed;
use `Logger.warn` with a dedupe key"), which normally means compile/runtime
failures at every use.
Why rejected: the inventory row `` `services/*/logging` `` records usage
`` `Logger.warn` `` with the detail "No `warnOnce` call sites found (grep
returned zero)." Zero call sites → no impact.

**R2. `connect()` default timeout lowered from 30s to 10s (changelog entry 6).**
Looks breaking: a silent behavioural change to a default (30s → 10s) that could
turn slow connections into failures without any code change.
Why rejected: the changelog itself scopes it — "Call sites passing an explicit
timeout are unaffected" — and the inventory row `` `services/*/bootstrap` ``
shows `` `connect({ timeout: 20_000 })` `` with the detail "Every `connect()`
call site passes an explicit timeout." Since every call site is explicit, the
default never applies. (Note the explicit 20s sits between the old and new
defaults, which is what makes this look suspicious at a glance; it is
nonetheless unaffected because it is explicit.)

**R3. `Queue.peek()` new API (changelog entry 8) at `services/billing/exporter`.**
Looks breaking: the inventory lists a `Queue.peek()` entry against a real
service, which reads like an existing dependency on a changed API.
Why rejected: the entry is additive ("New `Queue.peek()` API" — not a removal or
signature change), and the inventory row `` `services/billing/exporter` `` is
marked `` `Queue.peek()` (planned) `` with the detail "Not yet using it; listed
from the design doc." No call site exists, and a new API is not breaking.

**Changelog entries with no inventory bearing at all:** entry 7 ("Internal buffer
pooling rewritten; ~12% lower allocation rate"), entry 9 ("Documentation moved to
a new site"), and entry 10 ("Minimum supported runtime raised to LTS") are not
API-breaking against any inventoried call site. Entry 7 is a stated performance
characteristic that this assessment did **not** measure. Entry 10 is an
environment prerequisite rather than a call-site change; the repo's current
runtime version is **not recorded in either fixture and is therefore unmeasured**.

## Evidence

All quotes below are verbatim from the two named fixtures; no other file was
opened, grepped, or listed.

| Claim | Source | Quoted evidence |
|---|---|---|
| F1 breaking change | upgrade-changelog.md #1 | "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)." |
| F1 call site | usage-inventory.md | "`services/ingest/loader`" / "`parseConfig(raw)`" / "Config file still contains the deprecated `flush_interval` key kept \"for reference\"." |
| F2 breaking change | upgrade-changelog.md #2 | "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked." |
| F2 call site | usage-inventory.md | "`services/dispatch/retry-metrics`" / "`onRetry((attempt, error) => ...)`" / "Two-argument callback records retry counters." |
| F3 breaking change + ordering | upgrade-changelog.md #3 | "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call." |
| F3 call site | usage-inventory.md | "`services/edge-cache/consumer`" / "subscribes to `publish()` output" / "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo." |
| F4 breaking change | upgrade-changelog.md #4 | "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty." |
| F4 call site | usage-inventory.md | "`scripts/shutdown-hook`" / "`queue.drain()`" / "Called synchronously as the last line before process exit." |
| R1 rejection | usage-inventory.md | "`services/*/logging`" / "`Logger.warn`" / "No `warnOnce` call sites found (grep returned zero)." |
| R2 rejection | upgrade-changelog.md #6 + usage-inventory.md | "Call sites passing an explicit timeout are unaffected." / "`connect({ timeout: 20_000 })`" / "Every `connect()` call site passes an explicit timeout." |
| R3 rejection | upgrade-changelog.md #8 + usage-inventory.md | "New `Queue.peek()` API." / "`Queue.peek()` (planned)" / "Not yet using it; listed from the design doc." |

### Findings table

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | ingest_loader_parseconfig | config parsed by `parseConfig(raw)` contains no unknown keys under v3 strict parsing | config "still contains the deprecated `flush_interval` key kept \"for reference\"" — raises `ConfigKeyError` | drift |
| usage-inventory.md | dispatch_retry_metrics_onretry | `onRetry` callback uses the single `(context)` object signature | `onRetry((attempt, error) => ...)` two-argument callback — "no longer invoked" | drift |
| usage-inventory.md | edge_cache_consumer_encoding | msgpack codec pinned before the first `publish()` call, or consumer decodes JSON | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo" | drift |
| usage-inventory.md | shutdown_hook_queue_drain | `queue.drain()` awaited, since v3 returns a Promise | "Called synchronously as the last line before process exit" | drift |
| usage-inventory.md | logging_warnonce | no `Logger.warnOnce` call sites remain after its removal | "`Logger.warn`" only — "No `warnOnce` call sites found (grep returned zero)" | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | every `connect()` call site passes an explicit timeout so the lowered default does not apply | "`connect({ timeout: 20_000 })`" — "Every `connect()` call site passes an explicit timeout" | no-finding |
| usage-inventory.md | billing_exporter_queue_peek | no existing call site depends on `Queue.peek()` (additive API) | "`Queue.peek()` (planned)" — "Not yet using it; listed from the design doc" | no-finding |

## Assumptions

- The two named fixture files are the complete and authoritative source of truth.
  No repository source code was read, so call-site details beyond the inventory's
  `Detail` column are unknown, not assumed.
- The inventory's grep is treated as complete for the repo as stated in its title
  ("repo-wide grep, current main"); R1's rejection rests on that completeness
  claim ("grep returned zero"), which I did not independently re-run.
- `verdict = drift` is interpreted as "this inventory item is defective under
  relaykit v3 and requires a migration change"; `no-finding` as "evaluated and
  compliant / unaffected".
- Silent-failure severity ordering (F2, F4 fail without raising) is my inference
  from the changelog wording, not a statement in the fixtures.
- No runtime, performance, or test evidence was collected; entries 7 and 10 of
  the changelog remain unmeasured, as stated above.

## Stop Condition

Satisfied when this artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-dependency-upgrade-impact-claude_code_goal_command-rep3.md`
with the headings Goal, Result, Evidence, Assumptions, and Stop Condition; every
impactful breaking change mapped to its call site with quoted inventory evidence;
migration steps stated including the `publish()` codec-pin ordering constraint; a
rejected-candidates section present with inventory evidence for each rejection;
and exactly one findings table carrying one row per adjudicated item using the
prescribed `location`/`key` strings and a `drift`/`no-finding` verdict. All of
these hold as written. No commits, pushes, installs, or network access were
performed, and no file outside this artifact was modified.
