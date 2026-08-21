# tail-dependency-upgrade-impact — mission arm (rep1)

## Mission

Assess the relaykit v2 → v3 upgrade using exactly two fixtures:

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`

Deliverable: map every breaking changelog entry to the concrete call site it affects
(with quoted inventory evidence), state migration steps including any ordering
constraint, and reject changelog entries that look breaking but affect no call site.

Arm: mission (profile `full`). Complexity: Complex. Role: implementer.
Routing: the mission state CLI did **not** route this task to the goal contract —
`init` returned `{"ok": true, "mode": "multi-session", ...}` with no `route` verdict and no
`routed-goal` halt, so the mission loop (plan → execute → verify → review → score →
closeout) was run as specified.

## Plan

Canonical plan registered via `mission-state.py planning adopt-core`
(`.mission-state/plans/a2a841595eb5f394.json`,
digest `sha256:a2a841595eb5f3942a21ef07c0a600400b83bbc7429a5c96061736f227d70869`,
`generation: 1`, `selection_source: core`).

| step | action | output | acceptance |
|---|---|---|---|
| s1 | analyze `upgrade-changelog.md` | classification of all 10 numbered entries as breaking / non-breaking | every entry classified |
| s2 | analyze `usage-inventory.md` | binding of each of the 7 adjudication keys to a quoted inventory row | each key has a quoted cell |
| s3 | derive impact + ordering | migration order constraint, separated into explicit vs derived | explicit constraint quoted verbatim |
| s4 | write artifact | this file | 8 required headings, exactly one findings table, 7 rows, rejected-candidates section |

Out of scope by rule: reading anything under `benchmarks/mission-vs-goal/` other than the
two named fixtures and this output file; committing, pushing, installing, or network access.

## Execution

### Changelog classification (source: `upgrade-changelog.md`)

Entries 1–6 are behaviour-changing; 7–10 are not. Entries 1–6 were then tested against the
inventory; only those with a matching call site are confirmed findings.

### Confirmed findings (breaking change → affected call site)

**F1 — `parseConfig` strict mode breaks the ingest loader.**
Changelog entry 1: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2
silently ignored them)."
Affected call site — `usage-inventory.md` row 1: `` `services/ingest/loader` `` uses
`` `parseConfig(raw)` ``, and the detail cell states: "Config file still contains the
deprecated `flush_interval` key kept \"for reference\"."
Impact: under v3 the retained `flush_interval` key is an unknown key, so `parseConfig(raw)`
raises `ConfigKeyError` instead of ignoring it. This fires at config load; that config load
happens at process start for a loader is a **derived inference**, not stated in the inventory
(the inventory records only the call and the retained key).

**F2 — `onRetry` signature change silently disables retry metrics.**
Changelog entry 2: "The `onRetry` hook signature changed from `(attempt, error)` to a single
`(context)` object; two-argument callbacks are no longer invoked."
Affected call site — `usage-inventory.md` row 2: `` `services/dispatch/retry-metrics` ``
registers `` `onRetry((attempt, error) => ...)` ``, detail: "Two-argument callback records
retry counters."
Impact: the callback matches exactly the arity the changelog says is "no longer invoked", so
retry counters stop being recorded. This failure is silent — no exception, just zeroed
metrics — which is why it must be migrated deliberately rather than discovered in production.

**F3 — `publish()` encoding change breaks the edge-cache consumer (ordering-constrained).**
Changelog entry 3: "`publish()` default payload encoding changed from msgpack to JSON. Pin a
codec explicitly to keep the old wire format; the codec pin must be set before the first
`publish()` call."
Affected call site — `usage-inventory.md` row 3: `` `services/edge-cache/consumer` ``
"subscribes to `publish()` output", detail: "Decodes payloads with a msgpack reader; no codec
pin is set anywhere in the repo."
Impact: with no codec pin, v3 publishes JSON while the consumer still decodes msgpack, so
every payload fails to decode. The inventory's "no codec pin is set anywhere in the repo" is
the evidence that the changelog's mitigation ("Pin a codec explicitly") is **not** already in
place. This is the one entry that carries an explicit ordering constraint (see Migration).

**F4 — `Queue.drain()` becoming async breaks the synchronous shutdown hook.**
Changelog entry 4: "`Queue.drain()` is now async and returns a Promise; synchronous callers
will no longer block until the queue is empty."
Affected call site — `usage-inventory.md` row 4: `` `scripts/shutdown-hook` `` calls
`` `queue.drain()` ``, detail: "Called synchronously as the last line before process exit."
Impact: the call returns an unawaited Promise and the process exits immediately, so queued
items are dropped at shutdown. The inventory detail ("last line before process exit") is what
makes this concrete rather than theoretical: there is no subsequent code that could await it.

### Rejected candidates (look breaking, affect no call site)

**R1 — `Logger.warnOnce` removal (changelog entry 5).**
Why it looks breaking: entry 5 is an outright API removal — "`Logger.warnOnce` has been
removed; use `Logger.warn` with a dedupe key." Removals are normally the highest-severity
class of breaking change.
Why it is not a finding here: `usage-inventory.md` row 5 covers `` `services/*/logging` ``
and records usage as `` `Logger.warn` `` with the detail "No `warnOnce` call sites found
(grep returned zero)." The removed symbol has zero call sites, so nothing in this repo
migrates.

**R2 — `connect()` default timeout lowered (changelog entry 6).**
Why it looks suspicious: a default lowered "from 30s to 10s" is a silent behavioural
regression that typically surfaces as new timeouts on slow dependencies — the classic
"non-obvious breaking change" of an upgrade.
Why it is not a finding here: the changelog itself scopes it — "Call sites passing an
explicit timeout are unaffected" — and `usage-inventory.md` row 6 shows
`` `services/*/bootstrap` `` calling `` `connect({ timeout: 20_000 }) `` with the detail
"Every `connect()` call site passes an explicit timeout." Since every call site is explicit,
the lowered default is never used. Note the explicit value `20_000` (20s) sits between the
old and new defaults, so the behaviour is unchanged only because it is pinned.

**R3 — new `Queue.peek()` API (changelog entry 8).**
Why it looks suspicious: among the non-API/non-breaking entries (7–10) it is the only one with
a matching inventory row, so a naive "changelog entry ↔ inventory row" join flags it. (Entries
5 and 6 also have matching rows, but those are handled as R1 and R2.)
Why it is not a finding: entry 8 is "New `Queue.peek()` API" — additive, not breaking — and
`usage-inventory.md` row 7 qualifies `` `services/billing/exporter` `` as
`` `Queue.peek()` (planned) `` with the detail "Not yet using it; listed from the design
doc." There is no existing call site to migrate.

**R4 — non-API entries with no inventory row (changelog entries 7, 9, 10).**
Entry 7 ("Internal buffer pooling rewritten; ~12% lower allocation rate") is an internal
performance change; entry 9 ("Documentation moved to a new site") is docs-only; entry 10
("Minimum supported runtime raised to LTS") is an environment prerequisite, not a call-site
change. None appears in `usage-inventory.md` and none is an adjudication key. Entry 10 is
flagged as an operational prerequisite: whether the deployment runtime already satisfies
"LTS" is **unmeasured** — the fixtures contain no runtime-version evidence.

### Migration steps and ordering

Explicit ordering constraint (quoted verbatim from `upgrade-changelog.md` entry 3): "the
codec pin must be set before the first `publish()` call." This is the only ordering
constraint stated by the source; the remaining sequencing below is derived from call-site
timing described in `usage-inventory.md` and is labelled as such.

1. **Pin the codec before any v3 publisher runs (explicit, hard ordering).** Set an explicit
   msgpack codec pin for `publish()` before the first `publish()` call in the v3 process —
   not after startup, not lazily on first message. Alternative: migrate
   `services/edge-cache/consumer` off its msgpack reader to JSON *first*, and only then let a
   v3 publisher start. Either way, the consumer-side decision must land before the first v3
   `publish()`; there is no safe window in which a v3 publisher runs while
   "no codec pin is set anywhere in the repo" holds.
2. **Remove `flush_interval` from the ingest config before the v3 ingest loader boots
   (derived).** `parseConfig(raw)` runs at config load, so the config edit must be deployed
   with or ahead of the v3 binary; otherwise the loader fails closed at start-up with
   `ConfigKeyError`.
3. **Rewrite the retry-metrics callback in the same change as the upgrade (derived).** Convert
   `onRetry((attempt, error) => ...)` to the single `(context)` form. Because the v3 failure
   mode is silent (hook simply not invoked), this cannot be deferred to a follow-up and
   verified later by absence of errors.
4. **Make the shutdown hook await the drain (derived).** Convert `scripts/shutdown-hook` to
   await the Promise returned by `queue.drain()` before process exit. Ordering relative to the
   others is free, but it must ship no later than the v3 upgrade itself, since the pre-existing
   line is "the last line before process exit" and would otherwise silently stop blocking.
5. **No migration work for `Logger.warnOnce`, `connect()` timeouts, or `Queue.peek()`** — see
   the rejected-candidates section.

Not measured: no code, tests, or runtime were executed against relaykit v3; this assessment
is a document-level mapping between the two fixtures only. Blast radius (message volume,
queue depth at shutdown, retry rates) is unmeasured — the fixtures carry no such data.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| usage-inventory.md | ingest_loader_parseconfig | config passed to `parseConfig(raw)` contains no unknown keys under v3 strict parsing | "Config file still contains the deprecated `flush_interval` key kept \"for reference\"." → raises `ConfigKeyError` | drift |
| usage-inventory.md | dispatch_retry_metrics_onretry | `onRetry` callback takes the single `(context)` object | `onRetry((attempt, error) => ...)`; "Two-argument callback records retry counters." | drift |
| usage-inventory.md | edge_cache_consumer_encoding | codec pinned to msgpack before the first `publish()` call, or consumer decodes JSON | "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo." | drift |
| usage-inventory.md | shutdown_hook_queue_drain | `queue.drain()` awaited so shutdown blocks until the queue is empty | "Called synchronously as the last line before process exit." — Promise unawaited, exit does not block | drift |
| usage-inventory.md | logging_warnonce | no call sites of the removed `Logger.warnOnce` | "No `warnOnce` call sites found (grep returned zero)." — usage is `Logger.warn` | no-finding |
| usage-inventory.md | bootstrap_connect_timeout | every `connect()` call site passes an explicit timeout, so the lowered default is unused | `connect({ timeout: 20_000 })`; "Every `connect()` call site passes an explicit timeout." | no-finding |
| usage-inventory.md | billing_exporter_queue_peek | no existing call site depends on `Queue.peek()` (additive API) | "`Queue.peek()` (planned)"; "Not yet using it; listed from the design doc." | no-finding |

## Review

Verification (`mission-state.py verification record --iteration 1`) ran before reviewers and
executed fact-based checks rather than opinion. All 7 checks returned `ok: true`:
required-headings (missing=[]); exactly-one-findings-table (header occurrences=1);
all-keys-exactly-once (each of the 7 required keys appears exactly once);
verdict-vocabulary (rows=7, verdicts all in {drift, no-finding});
quoted-inventory-fragments-literal (7 quoted fragments are literal substrings of
`usage-inventory.md`); quoted-changelog-fragments-whitespace-normalized (7 quoted fragments
match `upgrade-changelog.md` after collapsing its line-wrap whitespace);
rejected-candidates-section (present). Record: `.mission-state/verify-iter1.json`.

Two independent reviewers (perspectives A and B; reviewer_count 2 as returned by
`mission-state.py next` for Complex) were spawned in a single message (parallel window
`2026-08-21T02:51:34Z..2026-08-21T02:58:00Z` for both). Their `mission-review/1` JSON was
imported with `review-import` and aggregated with `review-finalize --min-reviewers 2`.

Findings: 3 Low, 0 Medium, 0 High. A-1 (findings-table `actual` cell for
`dispatch_retry_metrics_onretry` mixed a changelog phrase into inventory evidence), B-1 (F1
asserted "at process start" as sourced fact when the inventory states no timing), B-2 (R3's
"only changelog entry with a matching inventory row" was literally false; true only within
entries 7-10). All three were fixed in the artifact before scoring; the artifact identity was
re-registered with `advance --artifact-path` so the scored revision is the corrected one.
Reviewer evidence: `.mission-state/archive/iter-1-6ec0f5ee-review-input-2ca1945fddb16c01.json`
(A) and `...-review-input-efa3bfbed3ac74e9.json` (B).

## Score

Tool-computed by `mission-state.py review-finalize`
(`.mission-state/archive/iter-1-6ec0f5ee-scoring-64eececcf85bc95e.json`), not asserted by hand:

- items: mission_achievement 5.0, accuracy 4.0, completeness 5.0, usability 5.0
- `computed_composite`: **4.75** (threshold 4.0)
- `computed_min_item`: **4.0** (floor 3.5)
- open High findings: **0** (both reviewers reported Low only)
- reviewer agreement: both reviewers returned identical per-axis scores, so the agreement delta is 0.0 (limit 1.5)
- revision scope: git, base = head = `068dc40517caa7b85e50e618b2603ebec81cc1c2` (no commits were made; the run is forbidden from committing)
- iteration: **1** of `--max-iter 2`

## Stop Decision

The artifact is complete and the scored review iteration finished above threshold, but
**`mission-state.py mark-passes` did not pass its gate**, so this run does not claim
`passes: true`. Reported verbatim, the gate rejected closeout with: "specialist selection
checkpoint is not terminal or valid: selection lifecycle_state must be terminal for
decision=none". `specialists recommend --record-state` was run but left
`specialists_decision` / `task_profile` null in session state, and the run's cost budget was
exhausted before that lifecycle could be driven to a terminal state. The session was therefore
closed with `mark-halt --category partial-done`.

What this means concretely: the deliverable (this artifact, including all 7 adjudications,
migration ordering, and rejected candidates) is finished and was reviewed and scored; the
mission loop's final pass flag is not set. Iteration 2 was not run — no reviewer finding
required it (0 High, 0 Medium); the blocker is a state-bookkeeping checkpoint, not artifact
quality.

Scope note: this artifact completes one benchmark task only. No claim of benchmark
superiority for either arm is made or implied; comparative performance is unmeasured here.

## Evidence

Fixtures read (the only two benchmark inputs opened, per the task rules):

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md` — 10 numbered entries, quoted inline above.
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md` — 7 call-site rows, quoted inline above.

Mission-state evidence (auditable, under `.mission-state/`):

- session: `.mission-state/sessions/cc-6df16693-0406-4e51-b0a2-f92f69455db8.json` (`mission_id: 6ec0f5eece9fd51e`)
- canonical plan: `.mission-state/plans/a2a841595eb5f394.json`, digest `sha256:a2a841595eb5f3942a21ef07c0a600400b83bbc7429a5c96061736f227d70869`
- verification record: iteration 1, checks recorded via `verification record --stdin`
- reviewer evidence + aggregate/scoring JSON: `.mission-state/archive/` (paths returned by `review-import` / `review-finalize`; not transcribed here per output-compression discipline)

Per-claim traceability: every confirmed finding above quotes the exact changelog sentence and
the exact `usage-inventory.md` cell it rests on; every rejected candidate quotes the inventory
cell that disproves impact.

Explicitly unmeasured:

- No relaykit v3 code, dependency, test suite, or runtime was executed (no installs, no network).
- Whether the deployment runtime already satisfies changelog entry 10 ("Minimum supported runtime raised to LTS").
- Real-world blast radius of each finding (traffic volume, queue depth, retry rate).
- Any call site not listed in `usage-inventory.md`; the inventory is taken at face value as a complete "repo-wide grep, current main".

## Assumptions

- **a1** — The two named fixtures are the sole source of truth; no repository source code was
  inspected and no other file under `benchmarks/mission-vs-goal/` was opened, listed, or
  grepped. Validation: only the two fixture paths and this output path were touched.
- **a2** — A changelog entry is "impactful" only when `usage-inventory.md` shows a concrete
  call site whose current usage breaks under v3. Validation: each `drift` row cites a quoted
  inventory cell; each `no-finding` row cites the inventory cell showing no affected usage.
- **a3** — In the findings table, `drift` means "this call site requires migration work before
  or with the v3 upgrade" and `no-finding` means "evaluated and unaffected/compliant".
  Validation: only these two values appear in the `verdict` column.
- **a4** — The inventory line `services/*/logging` / "grep returned zero" is treated as
  authoritative negative evidence for `Logger.warnOnce`. If that grep were incomplete, R1
  would need re-adjudication; this is unmeasured from the fixtures alone.
- **a5** — `connect({ timeout: 20_000 })` on `services/*/bootstrap` is read together with the
  detail cell "Every `connect()` call site passes an explicit timeout" as covering all
  `connect()` call sites, not just the one shown.
