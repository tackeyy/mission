# tail-dependency-upgrade-impact — mission arm (rep3)

## Mission

Assess the relaykit v2 → v3 upgrade using exactly two fixtures:

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md` (10 numbered entries)
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md` (7 call-site rows)

Deliverables: map every impactful breaking change to the concrete call site it affects with quoted inventory evidence; state migration steps including any ordering constraint; reject changelog entries that look breaking but affect no call site, citing the inventory evidence that proves non-impact.

No other file under `benchmarks/mission-vs-goal/` was opened, grepped, or listed. Nothing was committed, pushed, installed, or fetched over the network.

## Plan

Adopted canonical plan: `.mission-state/plans/843b691bddc8c4c8.json` (`mission-plan/1`, digest `sha256:843b691bddc8c4c8a95e9e5701a4cf9f8b20ec4fcdcfb8d322bbda7c58c25eb1`, source `core`, adopted via `mission-state.py planning adopt-core`).

Steps:

1. **S1 (read)** — enumerate all 10 changelog entries and all 7 inventory rows.
2. **S2 (analyze)** — cross-map each changelog entry against every inventory row; record the verbatim quote that establishes impact or non-impact.
3. **S3 (decide)** — split into confirmed findings (an affected call site exists) and rejected candidates (the inventory proves no affected call site).
4. **S4 (analyze)** — derive migration steps and extract ordering constraints **only** where the fixture states one.
5. **S5 (write)** — emit this artifact with the eight required headings and exactly one findings table.
6. **S6 (decide)** — three independent reviews, `review-import` → `review-finalize` → gate decision.

## Execution

### Entry-by-entry classification of the changelog

- **Entry 1 — `parseConfig` strict, unknown keys raise `ConfigKeyError`** → BREAKING, impacts `services/ingest/loader`. Confirmed.
- **Entry 2 — `onRetry` signature `(attempt, error)` → `(context)`; "two-argument callbacks are no longer invoked"** → BREAKING, impacts `services/dispatch/retry-metrics`. Confirmed.
- **Entry 3 — `publish()` default payload encoding msgpack → JSON** → BREAKING, impacts `services/edge-cache/consumer`. Confirmed, and carries the only explicit ordering constraint in the fixture.
- **Entry 4 — `Queue.drain()` is now async and returns a Promise** → BREAKING, impacts `scripts/shutdown-hook`. Confirmed.
- **Entry 5 — `Logger.warnOnce` removed** → looks breaking, but zero call sites. Rejected.
- **Entry 6 — `connect()` default timeout 30s → 10s** → looks breaking, but every call site passes an explicit timeout. Rejected.
- **Entry 7 — internal buffer pooling rewritten, "~12% lower allocation rate"** → not an API change; no inventory row references buffer pooling. Not adjudicated (no required key maps to it); no migration work identified.
- **Entry 8 — "New `Queue.peek()` API"** → additive, not breaking; the only related inventory row is a planned, non-existent usage. Rejected as a candidate for the `billing_exporter_queue_peek` item.
- **Entry 9 — "Documentation moved to a new site"** → no code impact; no inventory row. Not adjudicated.
- **Entry 10 — "Minimum supported runtime raised to LTS"** → potentially environment-breaking, but the inventory contains **no runtime-version row**, so impact is **UNMEASURED** from the permitted evidence. Not adjudicated and not claimed as either impactful or non-impactful.

### Confirmed findings — breaking change → affected call site

**F1. Entry 1 (`parseConfig` strict) → `services/ingest/loader`**

- Changelog: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Inventory row: "`services/ingest/loader` | `parseConfig(raw)` | Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Impact: the retained key `flush_interval` is exactly the "unknown key" class that v2 silently ignored. Under v3 the same config raises `ConfigKeyError`, so the loader fails at config-parse time. This is a hard failure, not a degradation.

**F2. Entry 2 (`onRetry` signature) → `services/dispatch/retry-metrics`**

- Changelog: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Inventory row: "`services/dispatch/retry-metrics` | `onRetry((attempt, error) => ...)` | Two-argument callback records retry counters."
- Impact: the registered callback is arity-2, so under v3 it is "no longer invoked". This fails **silently** — no exception is raised, retry counters simply stop incrementing, which can be misread as "retries went to zero".

**F3. Entry 3 (`publish()` encoding) → `services/edge-cache/consumer`**

- Changelog: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Inventory row: "`services/edge-cache/consumer` | subscribes to `publish()` output | Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Impact: producer and consumer desynchronize on the wire format. The consumer's msgpack reader receives JSON bytes and fails to decode. The inventory phrase "no codec pin is set anywhere in the repo" removes the escape hatch the changelog offers — the mitigation is not currently in place.

**F4. Entry 4 (`Queue.drain()` async) → `scripts/shutdown-hook`**

- Changelog: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Inventory row: "`scripts/shutdown-hook` | `queue.drain()` | Called synchronously as the last line before process exit."
- Impact: the hook relies on `drain()` blocking. Under v3 it returns an unawaited Promise and the process exits immediately, dropping queued items. Another **silent** failure — shutdown appears to succeed.

### Migration steps and ordering constraints

Ordering constraint **explicitly stated by the fixture** (the only one):

> Entry 3: "the codec pin must be set before the first `publish()` call."

Derived ordering (stated as derived, not quoted):

- **Step 1 — Remove `flush_interval` from the ingest config (fixes F1). Must run before any v3 process starts.** Rationale: `parseConfig` runs at load time, so under v3 the ingest service cannot boot at all until this is done; leaving it in place blocks verification of every other fix. This is a derived precedence, not a constraint quoted from the changelog.
- **Step 2 — Pin the msgpack codec before the first `publish()` call under v3 (fixes F3). Hard, fixture-stated ordering constraint.** If any v3 `publish()` executes before the pin is set, that first call emits JSON to a consumer whose "msgpack reader" cannot decode it, and the pin cannot retroactively fix already-published payloads. The alternative migration — converting `services/edge-cache/consumer` to a JSON reader — must likewise be completed **before** the first v3 `publish()`, otherwise the same window of undecodable payloads occurs. Either way, the encoding decision precedes first publish.
- **Step 3 — Convert the `onRetry` callback to the single-`(context)` signature (fixes F2).** No ordering constraint is stated in either fixture; it can be sequenced freely. It should not be deferred indefinitely because the failure is silent (counters read as zero rather than erroring).
- **Step 4 — Await `queue.drain()` in `scripts/shutdown-hook` (fixes F4).** No ordering constraint is stated in either fixture. Also a silent failure mode, so it should not be left to a later phase.

Summary of ordering: only F3 carries an ordering constraint asserted by the source (`codec pin before first publish()`); F1 carries a derived boot-time precedence; F2 and F4 are order-independent per the available evidence.

### Rejected candidates — looked breaking, but no affected call site

**R1. Entry 5 — `Logger.warnOnce` removed.**
Why it looked suspicious: an outright API **removal** is the strongest breaking-change signal in the changelog ("`Logger.warnOnce` has been removed").
Why it is not a finding: the inventory row states "`services/*/logging` | `Logger.warn` | No `warnOnce` call sites found (grep returned zero)." The repo already uses `Logger.warn`, and the grep result is reported as zero. A removed API with zero call sites produces zero migration work.

**R2. Entry 6 — `connect()` default timeout lowered from 30s to 10s.**
Why it looked suspicious: a silent behavioural change to a *default* is the classic case that breaks callers who never touched the setting, and 30s → 10s could turn slow-but-successful connects into failures.
Why it is not a finding: only *default*-dependent call sites are affected, and the inventory row states "`services/*/bootstrap` | `connect({ timeout: 20_000 })` | Every `connect()` call site passes an explicit timeout." The changelog itself scopes the change: "Call sites passing an explicit timeout are unaffected." With every call site explicit at `20_000`, the default is never consulted.

**R3. Entry 8 — `Queue.peek()` (adjudicated for `billing_exporter_queue_peek`).**
Why it looked suspicious: the inventory names a call site (`services/billing/exporter`) against a changelog entry, which superficially reads as a change-to-call-site mapping.
Why it is not a finding: entry 8 is "New `Queue.peek()` API" — additive, not breaking — and the inventory row states "`services/billing/exporter` | `Queue.peek()` (planned) | Not yet using it; listed from the design doc." A planned, non-existent call site cannot be broken by a v3 upgrade. No migration work.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | ingest_loader_parseconfig | Under v3 entry 1 ("unknown keys raise `ConfigKeyError`"), the config passed to `parseConfig(raw)` must contain no unknown keys | "Config file still contains the deprecated `flush_interval` key kept \"for reference\"" — unknown key retained, so `parseConfig(raw)` raises `ConfigKeyError` at load | drift |
| usage-inventory.md | dispatch_retry_metrics_onretry | Under v3 entry 2, `onRetry` must take a single `(context)` object; "two-argument callbacks are no longer invoked" | "`onRetry((attempt, error) => ...)`" — arity-2 callback that "records retry counters", so it is silently never invoked under v3 | drift |
| usage-inventory.md | edge_cache_consumer_encoding | Under v3 entry 3, msgpack wire format requires an explicit codec pin set "before the first `publish()` call" | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo" — consumer expects msgpack while v3 defaults to JSON | drift |
| usage-inventory.md | shutdown_hook_queue_drain | Under v3 entry 4, `Queue.drain()` returns a Promise and must be awaited to block until the queue is empty | "`queue.drain()` ... Called synchronously as the last line before process exit" — unawaited, so the process exits without draining | drift |
| usage-inventory.md | logging_warnonce | Under v3 entry 5, no call site may use the removed `Logger.warnOnce` | "No `warnOnce` call sites found (grep returned zero)"; the row's usage is "`Logger.warn`" — already compliant, zero migration work | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | Under v3 entry 6, call sites must not rely on the `connect()` default timeout ("Call sites passing an explicit timeout are unaffected") | "`connect({ timeout: 20_000 })`" and "Every `connect()` call site passes an explicit timeout" — default never consulted | no-finding |
| usage-inventory.md | billing_exporter_queue_peek | Entry 8 is "New `Queue.peek()` API" (additive); a real, existing call site would be required for any upgrade impact | "`Queue.peek()` (planned) ... Not yet using it; listed from the design doc" — no existing call site to break | no-finding |

## Review

Three independent reviewers (review_tier `full`, reviewer_count 3) were spawned in a single message and scored this artifact against the task validator on four axes (mission achievement, accuracy, completeness, usability). Raw reviews are stored under `.mission-state/archive/` and aggregated by `mission-state.py review-finalize`; they are not transcribed here per the output-compression rule.

Validator checklist, self-assessed before review:

- Each impactful breaking change mapped to its affected call site with quoted inventory evidence — F1–F4 above, each quoting the inventory row verbatim.
- Migration steps with ordering constraints — Steps 1–4; the fixture-stated constraint ("codec pin must be set before the first `publish()` call") is quoted and distinguished from the derived boot-time precedence for F1.
- Rejected-candidates section for breaking changes with no affected call sites — R1–R3, each citing the inventory line that proves non-impact.
- Exactly one markdown table with the required header, seven rows, keys matching the required strings verbatim.

Pre-review verification (executed facts, recorded via `mission-state.py verification record`): heading count and names, table count and header string, row count and key strings, and quote-fidelity of every quoted fixture fragment against the fixture files.

## Score

Gate values are produced by the CLI (`review-finalize` = `aggregate-reviews` → `push-score --scoring-json`), not asserted by hand. Recorded values:

- composite_score: see `score_history` in `.mission-state/sessions/cc-aafa2414-d410-4d8a-8a59-b04a18372799.json`
- threshold: 4.0
- min(scored_items) requirement: >= 3.5
- open_high requirement: 0
- max_agreement_delta requirement: <= 1.5

The pass expression evaluated by `closeout` is the authority; no score is claimed in prose beyond what the CLI recorded.

## Stop Decision

Stop when `mission-state.py closeout` returns exit 0 with `next_action=report-complete` (`passes=true`). `--max-iter` for this run is 2; if the gate is not satisfied within 2 scored iterations, the run halts via `mark-halt --category partial-done` with the artifact left in its best verified state.

No benchmark-superiority claim is made. This artifact reports only the completion of this single task.

## Evidence

Fixture evidence (verbatim, both files read in full):

- `upgrade-changelog.md` L3–4: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- `upgrade-changelog.md` L5–6: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- `upgrade-changelog.md` L7–9: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- `upgrade-changelog.md` L10–11: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- `upgrade-changelog.md` L12: "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key."
- `upgrade-changelog.md` L13–14: "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected."
- `upgrade-changelog.md` L16: "New `Queue.peek()` API."
- `usage-inventory.md` L5: "`services/ingest/loader` | `parseConfig(raw)` | Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- `usage-inventory.md` L6: "`services/dispatch/retry-metrics` | `onRetry((attempt, error) => ...)` | Two-argument callback records retry counters."
- `usage-inventory.md` L7: "`services/edge-cache/consumer` | subscribes to `publish()` output | Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- `usage-inventory.md` L8: "`scripts/shutdown-hook` | `queue.drain()` | Called synchronously as the last line before process exit."
- `usage-inventory.md` L9: "`services/*/logging` | `Logger.warn` | No `warnOnce` call sites found (grep returned zero)."
- `usage-inventory.md` L10: "`services/*/bootstrap` | `connect({ timeout: 20_000 })` | Every `connect()` call site passes an explicit timeout."
- `usage-inventory.md` L11: "`services/billing/exporter` | `Queue.peek()` (planned) | Not yet using it; listed from the design doc."

Mission-state evidence:

- session: `.mission-state/sessions/cc-aafa2414-d410-4d8a-8a59-b04a18372799.json`
- canonical plan: `.mission-state/plans/843b691bddc8c4c8.json`
- assumptions: `.mission-state/sessions/cc-aafa2414-d410-4d8a-8a59-b04a18372799-assumptions.md`
- reviews and scoring JSON: `.mission-state/archive/`

Explicitly unmeasured:

- Changelog entry 10 ("Minimum supported runtime raised to LTS"): the inventory has no runtime-version row, so runtime compatibility is **unmeasured**. It is not claimed as impactful or as non-impactful.
- Changelog entry 7 (buffer-pool rewrite, "~12% lower allocation rate"): the ~12% figure is an upstream claim; no measurement was performed here.
- Changelog entry 9 (documentation move): no code-impact evidence exists in either fixture; treated as out of scope for call-site mapping.
- Whether the four confirmed findings are the *complete* set of impacts depends on the inventory being a complete repo-wide grep. The inventory header asserts "repo-wide grep, current main"; this was not independently verified, since the repository itself is out of bounds for this task.

## Assumptions

- **A1** Only the two named fixture files are in-bounds evidence; no other file under `benchmarks/mission-vs-goal/` was opened, grepped, or listed.
- **A2** `drift` means the call site is impacted by a v3 breaking change and requires migration work; `no-finding` means the item was evaluated and requires none. Both are drawn from verbatim fixture text.
- **A3** Changelog entries 7, 9 and 10 are not adjudication rows because none of the seven required keys maps to them; entry 10 is additionally recorded as unmeasured.
- **A4** `--max-iter` is 2 per the run instruction.
- **A5** Disclosed process deviation: planning used the `mission-planner` skill and review uses three independent reviewers, but the artifact write step was executed inline by the orchestrator rather than by a spawned `mission-executor`.
- **A6** The inventory is taken at face value as an accurate repo-wide grep of `current main`; it was not cross-checked against source code, which is outside the permitted evidence set.
