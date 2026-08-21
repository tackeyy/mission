# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment

Arm: mission (profile: full) / Task id: `tail-dependency-upgrade-impact` / Category: refactoring

## Mission

Assess the relaykit v2-to-v3 upgrade using exactly two fixtures:

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

Deliverable: map every breaking change to the concrete call sites it affects
(with quoted inventory evidence), state the migration steps including any
required ordering constraint, and reject changelog entries that look breaking
but affect no call site.

Scope boundary: the two fixtures above are the sole source of truth. The real
relaykit package, the real repository under `services/`, and the actual runtime
behaviour were **not** executed or inspected — that is unmeasured here.

## Plan

Adopted as `mission-plan/1` via `mission-state.py planning adopt-core`
(generation 1, validated 2026-08-21T03:14:07Z).

| Step | Action | Acceptance check |
|---|---|---|
| s1 | Read `upgrade-changelog.md`; classify all 10 entries as breaking / non-breaking | all 10 entries classified |
| s2 | Join each breaking candidate against the 7 inventory rows; decide drift / no-finding with a verbatim quote | all 7 adjudicated items carry quoted evidence |
| s3 | Derive migration steps, including the ordering constraint stated by the changelog | codec-pin-before-first-`publish()` ordering recorded |
| s4 | Write this artifact (8 required headings, 7-row findings table, rejected-candidates section) | artifact exists and satisfies the validator |

## Execution

### Step 1 — changelog classification (10 entries)

| # | Changelog entry (abridged, verbatim keywords) | Breaking? |
|---|---|---|
| 1 | "`parseConfig` is now strict: unknown keys raise `ConfigKeyError`" | yes |
| 2 | "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object" | yes |
| 3 | "`publish()` default payload encoding changed from msgpack to JSON" | yes |
| 4 | "`Queue.drain()` is now async and returns a Promise" | yes |
| 5 | "`Logger.warnOnce` has been removed" | yes (API removal) |
| 6 | "`connect()` default timeout lowered from 30s to 10s" | behaviour change, default-only |
| 7 | "Internal buffer pooling rewritten; ~12% lower allocation rate" | no (internal/perf) |
| 8 | "New `Queue.peek()` API" | no (additive) |
| 9 | "Documentation moved to a new site" | no |
| 10 | "Minimum supported runtime raised to LTS" | environment requirement, not an API break |

Entry 10 is an environment constraint. The inventory contains no row recording
the runtime version in use, so whether the current runtime already satisfies
"LTS" is **unmeasured** by these fixtures. It is not counted as an impacted call
site and is not one of the adjudicated items.

### Step 2 — confirmed findings (breaking change → affected call site)

**F1 — `parseConfig` strict mode breaks the ingest loader.**
Changelog 1: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
Inventory evidence (`services/ingest/loader`, usage `parseConfig(raw)`):
"Config file still contains the deprecated `flush_interval` key kept "for reference"."
Impact: on v3 the retained `flush_interval` key is no longer silently ignored;
`parseConfig(raw)` raises `ConfigKeyError` at load time. Failure is loud and
immediate (startup-time), not silent.

**F2 — `onRetry` two-argument callback is silently dropped.**
Changelog 2: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
Inventory evidence (`services/dispatch/retry-metrics`, usage `onRetry((attempt, error) => ...)`):
"Two-argument callback records retry counters."
Impact: the registered callback is "no longer invoked", so retry counters stop
being recorded. This fails **silently** — no exception, just missing metrics —
which makes it the highest-risk item for post-upgrade detection.

**F3 — `publish()` encoding flip breaks the msgpack consumer (ordering-constrained).**
Changelog 3: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
Inventory evidence (`services/edge-cache/consumer`, usage "subscribes to `publish()` output"):
"Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
Impact: after the upgrade the publisher emits JSON while the consumer still runs
a msgpack reader, so every payload fails to decode. Because "no codec pin is set
anywhere in the repo", nothing currently protects the wire format. This is the
one item carrying an explicit **ordering constraint** quoted above.

**F4 — `Queue.drain()` became async; the synchronous shutdown hook no longer waits.**
Changelog 4: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
Inventory evidence (`scripts/shutdown-hook`, usage `queue.drain()`):
"Called synchronously as the last line before process exit."
Impact: the returned Promise is discarded and the process exits immediately,
so queued items that were previously flushed at shutdown can be lost. This also
fails silently (no error, just dropped work).

### Step 3 — migration steps and ordering constraints

1. **Set the codec pin before anything on v3 publishes** (hard, changelog-stated
   ordering constraint: "the codec pin must be set before the first `publish()`
   call"). Either pin msgpack to preserve the current wire format, or migrate
   `services/edge-cache/consumer` off its "msgpack reader" to JSON *before* the
   v3 publisher goes live. A pin applied after the first `publish()` is too late
   per the changelog wording.
   - Consumer-first sub-ordering: if you choose to move to JSON rather than pin
     msgpack, the consumer must be able to read JSON **before** the publisher is
     upgraded, otherwise there is a window where JSON payloads hit a msgpack
     reader.
2. **Remove `flush_interval` from the ingest config** (or move it to a comment)
   before running v3's `parseConfig(raw)`. Ordering: must precede the first v3
   start of `services/ingest/loader`, which would otherwise fail fast with
   `ConfigKeyError`.
3. **Rewrite the `onRetry` callback** in `services/dispatch/retry-metrics` from
   `(attempt, error) => ...` to the single `(context)` form. No ordering
   dependency on the other steps, but it must land with the upgrade itself —
   there is no error to alert you afterwards.
4. **Make `scripts/shutdown-hook` await `queue.drain()`** (or otherwise keep the
   process alive until the Promise resolves). Must land with the upgrade; after
   the upgrade the old code silently stops waiting.
5. Re-verify after the upgrade. Not done here: no test run, no runtime
   observation, no repository grep beyond the supplied inventory — **unmeasured**.

Steps 2, 3 and 4 are mutually independent. Step 1 is the only step with an
externally stated ordering constraint, and its sub-ordering (consumer before
publisher) follows from the inventory's "no codec pin is set anywhere in the repo".

### Step 4 — rejected candidates (look breaking, affect no call site)

**R1 — `Logger.warnOnce` removal (changelog 5).**
Why it looks breaking: it is an outright API removal — "`Logger.warnOnce` has
been removed; use `Logger.warn` with a dedupe key." Removals normally break
callers.
Why it is not a finding: inventory row `services/*/logging` records usage
`Logger.warn` with the detail "No `warnOnce` call sites found (grep returned
zero)." With zero call sites, the removal has no target. Verdict: no-finding.

**R2 — `connect()` default timeout lowered from 30s to 10s (changelog 6).**
Why it looks suspicious: a 3× reduction in a connection timeout is exactly the
kind of default change that causes upgrade-day incidents on slow links.
Why it is not a finding: the change is default-only — the changelog itself says
"Call sites passing an explicit timeout are unaffected" — and inventory row
`services/*/bootstrap` shows `connect({ timeout: 20_000 })` with the detail
"Every `connect()` call site passes an explicit timeout." The explicit 20 s
value overrides the default in all call sites. Verdict: no-finding.

**R3 — `Queue.peek()` (changelog 8).**
Why it looks suspicious: the inventory names it at `services/billing/exporter`,
so a scan keyed on identifiers matches it as an "affected" site.
Why it is not a finding: `Queue.peek()` is a **new** API ("New `Queue.peek()`
API"), i.e. additive, not breaking; and the inventory row is explicitly
prospective — "`Queue.peek()` (planned)" with the detail "Not yet using it;
listed from the design doc." There is no existing call site to migrate.
Verdict: no-finding.

## Review

Two independent reviewers were spawned in parallel (perspectives:
evidence-fidelity and completeness/validator-conformance) and their
`mission-review/1` payloads were imported via `mission-state.py review-import`
and aggregated via `review-finalize`. Reviewer raw output is stored under
`.mission-state/archive/` and is not transcribed here (output-compression
discipline).

Independent verification recorded via `mission-state.py verification record`
(iteration 1): every quoted string in the findings table and prose was
re-checked byte-for-byte against the two fixture files; the findings table row
count and header were checked mechanically; the required heading set was checked
mechanically.

## Score

Computed by `mission-state.py review-finalize` (aggregate → `push-score`),
iteration 1. Source of truth:
`.mission-state/archive/iter-1-fad7ad33-scoring-b88bf92602bea0f4.json`.

| Axis | Score | reviewer min–max | delta |
|---|---|---|---|
| mission_achievement | 5.00 | 5.0–5.0 | 0.00 |
| accuracy | 4.75 | 4.5–5.0 | 0.50 |
| completeness | 5.00 | 5.0–5.0 | 0.00 |
| usability | 4.65 | 4.5–4.8 | 0.30 |

- Composite: **4.85** (threshold 4.0)
- min(scored_items): **4.65** (floor 3.5)
- `open_high`: **0** — the two reviewers raised 2 Low + 1 Low findings, no
  High/Medium (evidence-fidelity: two inline-quote punctuation nits;
  completeness-validator-conformance: this Score section previously carried no
  numeric values, which this revision fixes)
- `max_agreement_delta`: **0.50** (limit 1.5); `review_agreement`: 5.0
- Reviewers: 2, spawned in parallel in a single message
  (window 2026-08-21T03:16:19Z..2026-08-21T03:18:46Z for both perspectives)

No benchmark-superiority claim is made here; this section reports only this
run's own gate outcome.

## Stop Decision

Stop when `mission-state.py closeout` returns exit 0 with
`next_action=report-complete`, i.e. the pass predicate holds:
findings evidence path present, `evidence_high_count == open_high`,
`max_agreement_delta <= 1.5`, composite `>= 4.0`, `min(scored_items) >= 3.5`,
and `open_high == 0`. Iteration cap for this run: `--max-iter 2`. If the gate
had failed twice, the run would have halted via `mark-halt` rather than claiming
completion.

## Evidence

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | ingest_loader_parseconfig | config passed to `parseConfig(raw)` contains no unknown keys under v3 strict parsing | "Config file still contains the deprecated `flush_interval` key kept "for reference"." → `ConfigKeyError` at load | drift |
| usage-inventory.md | dispatch_retry_metrics_onretry | `onRetry` callback uses the v3 single `(context)` signature | "`onRetry((attempt, error) => ...)`" — "Two-argument callback records retry counters." → "two-argument callbacks are no longer invoked" | drift |
| usage-inventory.md | edge_cache_consumer_encoding | consumer decoding matches the publisher wire format, or a codec pin is set before the first `publish()` | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo." while v3 `publish()` defaults to JSON | drift |
| usage-inventory.md | shutdown_hook_queue_drain | caller awaits the Promise returned by the now-async `Queue.drain()` | "`queue.drain()`" — "Called synchronously as the last line before process exit." → no longer blocks until empty | drift |
| usage-inventory.md | logging_warnonce | no `Logger.warnOnce` call sites remain after its removal | "No `warnOnce` call sites found (grep returned zero)." | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | every `connect()` call site passes an explicit timeout so the lowered default does not apply | "`connect({ timeout: 20_000 })`" — "Every `connect()` call site passes an explicit timeout." | no-finding |
| usage-inventory.md | billing_exporter_queue_peek | no existing call site depends on a breaking change (`Queue.peek()` is additive) | "`Queue.peek()` (planned)" — "Not yet using it; listed from the design doc." | no-finding |

### Fixture line references

| Claim | Source | Line |
|---|---|---|
| `parseConfig` strict / `ConfigKeyError` | upgrade-changelog.md | 3–4 |
| `onRetry` signature change; 2-arg callbacks not invoked | upgrade-changelog.md | 5–6 |
| `publish()` msgpack→JSON; codec pin before first `publish()` | upgrade-changelog.md | 7–9 |
| `Queue.drain()` async | upgrade-changelog.md | 10–11 |
| `Logger.warnOnce` removed | upgrade-changelog.md | 12 |
| `connect()` default 30s→10s; explicit timeouts unaffected | upgrade-changelog.md | 13–14 |
| New `Queue.peek()` API | upgrade-changelog.md | 16 |
| `services/ingest/loader` — `flush_interval` retained | usage-inventory.md | 5 |
| `services/dispatch/retry-metrics` — 2-arg `onRetry` | usage-inventory.md | 6 |
| `services/edge-cache/consumer` — msgpack reader, no codec pin | usage-inventory.md | 7 |
| `scripts/shutdown-hook` — synchronous `queue.drain()` | usage-inventory.md | 8 |
| `services/*/logging` — zero `warnOnce` call sites | usage-inventory.md | 9 |
| `services/*/bootstrap` — explicit `connect({ timeout: 20_000 })` | usage-inventory.md | 10 |
| `services/billing/exporter` — `Queue.peek()` planned only | usage-inventory.md | 11 |

### Mission-state evidence

- Session state: `.mission-state/sessions/cc-c516df4d-ae90-4263-960f-1a423e7fc7a3.json`
- Plan (`mission-plan/1`, generation 1): adopted 2026-08-21T03:14:07Z
- Reviewer payloads and aggregate/scoring JSON: `.mission-state/archive/`
- The mission CLI did **not** route this task to the goal contract
  (`init` returned an active mission state, no `route: "goal"` verdict and no
  `routed-goal` halt), so the mission loop was run as implementer.

### Explicitly unmeasured

- No package installation, no network access, no execution of relaykit v2 or v3.
- No inspection of the actual `services/` source; the inventory table is taken
  as an accurate repo-wide grep result without independent confirmation.
- Runtime version vs. changelog entry 10 ("Minimum supported runtime raised to
  LTS"): no fixture data, therefore unmeasured.
- Wall-clock / token cost of this run was not instrumented here.

## Assumptions

1. The two named fixtures are the complete and authoritative source of truth;
   anything absent from them is unmeasured rather than compliant.
2. The inventory's grep results are exhaustive — in particular "No `warnOnce`
   call sites found (grep returned zero)" and "Every `connect()` call site
   passes an explicit timeout" are accepted as complete, since rejecting a
   candidate depends on them.
3. `verdict` is restricted to `drift` (asserted defect) and `no-finding`
   (evaluated and compliant); `drift` is used only where the fixture text
   supports a concrete break at a concrete call site.
4. "Affected call site" means an existing call site, not a planned one — hence
   `services/billing/exporter` is rejected despite naming a v3 API.
5. `services/*/bootstrap` and `services/*/logging` are glob-style rows covering
   all matching services, as the inventory header states it is a "repo-wide grep".

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-21 | 初版作成 (mission arm, iteration 1) |
