# tail-dependency-upgrade-impact — relaykit v2 → v3 upgrade impact assessment (mission arm)

## Mission

Assess the relaykit v2-to-v3 upgrade using exactly two fixture files, and produce one auditable
artifact that (a) maps every impactful breaking change to the concrete call sites it affects with
quoted inventory evidence, (b) states the migration steps including any required ordering
constraint, and (c) rejects, with inventory evidence, the changelog entries that look breaking but
affect no call site.

- Task id: `tail-dependency-upgrade-impact` (category: refactoring)
- Arm: mission (profile: full), complexity: **Complex**, `--max-iter 2`
- Sources (the only two inputs consulted):
  - `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md`
    (10 numbered entries)
  - `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md`
    (7 call-site rows)
- Out of scope: no code changes, no commits, no network, no package installs. The assessment is a
  document; no relaykit upgrade was actually executed or run, so all runtime behaviour below is
  derived from the two fixtures, not observed.

## Plan

Canonical plan adopted through the mission state CLI (`planning adopt-core`):

- plan document: `.mission-state/plans/f19b92bb7f4e42c6.json`
- plan digest: `sha256:f19b92bb7f4e42c6945d302416e44a3dc16252b1ce1148993ce2af4c25e98ba1`
- source digest: `sha256:1de3a008b07cf1c05c4f556e2100acdf79ad43ee9ac16a454a11538b55385a24`

| Step | Action | Content | Acceptance |
|---|---|---|---|
| S1 | read | Load both fixtures verbatim | both files read, non-empty |
| S2 | analyze | Enumerate all 10 changelog entries and all 7 inventory rows | counts match the fixtures |
| S3 | decide | Classify each entry as impactful (matched call site) or rejected (no call site) | every entry lands in exactly one list |
| S4 | analyze | Derive migration steps and ordering constraints from the impactful set | ordering constraints stated with source (quoted vs derived) |
| S5 | write | Write this artifact under the 8 required headings | all headings present |
| S6 | analyze | Self-check against the task validator | mapping + ordering + rejected section verified |

Reviewer count for Complex = 2 independent reviewers, run in a single parallel message; pass gate is
the CLI's (`composite_score >= 4.0`, `open_high == 0`, `max_agreement_delta <= 1.5`,
`min(scored_items) >= 3.5`, findings evidence present).

## Execution

### Confirmed findings — breaking changes with affected call sites

Every row quotes the changelog entry and the exact inventory cell that establishes impact.

**F1 — `parseConfig` strict mode breaks ingest config loading**

- Changelog #1: "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Call site: `services/ingest/loader`, usage `parseConfig(raw)`.
- Inventory evidence (verbatim): "Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Impact: on v3, `flush_interval` is an unknown key, so `parseConfig(raw)` raises `ConfigKeyError`.
  Failure mode is **loud and immediate** (raised at config load / service start).

**F2 — `onRetry` two-argument callback stops being invoked in dispatch retry metrics**

- Changelog #2: "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Call site: `services/dispatch/retry-metrics`, usage `onRetry((attempt, error) => ...)`.
- Inventory evidence (verbatim): "Two-argument callback records retry counters."
- Impact: the registered callback is exactly the two-argument shape the changelog says is "no longer
  invoked", so retry counters silently stop being recorded. Failure mode is **silent** — no
  exception is described in the changelog, only non-invocation.

**F3 — `publish()` encoding switch breaks the edge-cache consumer (ordering-constrained)**

- Changelog #3: "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Call site: `services/edge-cache/consumer`, usage "subscribes to `publish()` output".
- Inventory evidence (verbatim): "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Impact: after the upgrade the publisher emits JSON while the consumer still runs a msgpack reader,
  and the inventory states no codec pin exists anywhere, so nothing preserves the old wire format.
  This is the **only entry that carries an explicit ordering constraint in the changelog text**
  ("must be set before the first `publish()` call").
- Failure mode: **likely loud** — a msgpack reader handed JSON bytes would typically raise a decode
  error rather than silently accept them (behaviour not specified in either fixture; assumed
  fail-loud). The exact behaviour depends on the consumer's error
  handling, which neither fixture describes, so this is **unmeasured**; the safe assumption is that
  the consumer starts erroring on every message (a full break of the subscribe path), not that it
  degrades quietly.

**F4 — `Queue.drain()` becoming async breaks the synchronous shutdown hook**

- Changelog #4: "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Call site: `scripts/shutdown-hook`, usage `queue.drain()`.
- Inventory evidence (verbatim): "Called synchronously as the last line before process exit."
- Impact: the call site is precisely the "synchronous caller" case. On v3 the process exits without
  waiting for the queue to empty, so in-flight queue items can be dropped at shutdown. Failure mode
  is **silent** (no error; only lost work).

### Migration steps and ordering constraints

Ordering is labelled by source: **quoted** = stated in the changelog text; **derived** = inferred
from the failure mode described by the changelog plus the inventory row. Nothing here was executed
or verified at runtime.

| # | Step | Ordering constraint | Source |
|---|---|---|---|
| M0 | Pre-flight: check the actual runtime version against the new minimum (changelog #10, "Minimum supported runtime raised to LTS"). | Blocks every other step if unmet. **Requires external verification — the current runtime version is not determinable from the two fixtures.** | derived (from an unmeasured precondition) |
| M1 | In the publish path, set an explicit codec pin to msgpack **before any v3 `publish()` call runs**. ⚠️ The inventory names only the *consumer* of `publish()` output (`services/edge-cache/consumer`); **no row identifies a `publish()` caller**, so the pin's target location is not determinable from the fixtures and must be found by a separate codebase search (e.g. grep for `publish(`) before this step can be executed. | Hard, intra-change: "the codec pin must be set before the first `publish()` call". Keeps the msgpack wire format, so `services/edge-cache/consumer` needs no change. | quoted |
| M1-alt | Alternative end state: move the consumer to the v3 JSON default instead of pinning. This cannot be done by simply flipping the consumer to a JSON reader first — that would leave a JSON-only consumer reading a still-msgpack v2 publisher. It requires either (a) a dual-format consumer that accepts msgpack **and** JSON, deployed first, then the publisher cutover, then removal of the msgpack branch, or (b) an atomic simultaneous cutover of publisher and consumer. | Sequenced: dual-format consumer → publisher cutover → drop msgpack branch. Mutually exclusive with M1. | derived |
| M2 | Remove (or otherwise make acceptable) the `flush_interval` key in the ingest config consumed by `services/ingest/loader`. | Must land **before** `services/ingest/loader` boots on v3; otherwise `ConfigKeyError` at startup. | derived |
| M3 | Rewrite the `services/dispatch/retry-metrics` callback from `(attempt, error)` to the single `(context)` object shape. | Must land **with or before** the v3 cutover of dispatch; because the failure is silent, a later fix leaves an undetected gap in retry counters for the whole window. | derived |
| M4 | Change `scripts/shutdown-hook` to await the `Queue.drain()` Promise before process exit. | Must land **with or before** the v3 cutover of that process; the failure is silent queue-item loss at shutdown. | derived |
| M5 | Upgrade the dependency to v3 / cut traffic over. | Last, after M1–M4 for the corresponding component. | derived |

Ordering summary: the only ordering constraint the fixtures state explicitly is inside M1 (codec pin
before the first `publish()` call). M2–M4 have no interdependencies with each other — they touch
disjoint call sites (`services/ingest/loader`, `services/dispatch/retry-metrics`,
`scripts/shutdown-hook`) — so they can proceed in parallel, each ahead of its own component's
cutover. M1's constraint is the one that can be violated by a merely "upgrade first, fix after"
sequence.

Risk ranking used for sequencing (derived, not measured), by whether the failure announces itself:

- **Silent** — F2 (retry counters stop being recorded; the changelog says the callback is "no longer
  invoked", with no error) and F4 (queue items dropped at exit; the process simply stops blocking).
  These are the dangerous ones: nothing in the system reports them, so a "fix after upgrade" plan
  leaves an undetected gap for the whole window.
- **Loud** — F1 raises `ConfigKeyError` at config load, and F3 most likely raises a decode error in
  the msgpack reader on the first JSON payload (see F3; the consumer's error handling is not
  described in the fixtures, so "loud" is the expected, not verified, behaviour).

Loud failures are safer to sequence late because they surface immediately; silent ones (M3, M4)
should not be deferred past their component's cutover.

### Rejected candidates — changelog entries that look breaking but affect no call site

| Entry | Why it looks breaking | Inventory evidence that rejects it |
|---|---|---|
| #5 "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key." | An outright API removal — normally the most impactful class of change. | Row `services/*/logging` uses `Logger.warn`, detail: "No `warnOnce` call sites found (grep returned zero)." The removed symbol has zero call sites, so nothing to migrate. |
| #6 "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected." (full entry) | Its first sentence is a silent behaviour change to a default — the classic case that breaks callers relying on the old default. | Row `services/*/bootstrap` uses `connect({ timeout: 20_000 })`, detail: "Every `connect()` call site passes an explicit timeout." No call site relies on the default. The changelog itself concurs: "Call sites passing an explicit timeout are unaffected." |
| #7 "Internal buffer pooling rewritten; ~12% lower allocation rate." | A rewrite of internals can change behaviour under load. | No inventory row references buffer pooling or an allocation-sensitive path; the change is described as internal with a performance effect only, and it exposes no API. The claimed "~12% lower allocation rate" is **unmeasured here** — no benchmark was run. |
| #8 "New `Queue.peek()` API." | An API-surface change appearing in a breaking-change list. | Additive, not breaking. Row `services/billing/exporter` says `Queue.peek()` is "(planned)" with detail "Not yet using it; listed from the design doc." — a planned, non-existent call site, so it cannot break. |
| #9 "Documentation moved to a new site." | Appears in the same numbered upgrade list as the breaking entries. | No inventory row references documentation. No code call site exists; the impact is on human references only. |

### Neither confirmed nor rejected — unmeasured environment precondition

| Entry | Status | Why it is in neither bucket |
|---|---|---|
| #10 "Minimum supported runtime raised to LTS." | **Unmeasured** | It is a genuine hard-stop class of change (the package can refuse to install or run), but it is an environment precondition, not a call-site break, so it maps to no inventory row. The inventory is a call-site inventory only, and neither fixture states the runtime version currently in use — so it cannot be confirmed *or* rejected from the given evidence. It is carried into the migration table as step **M0** (pre-flight check requiring external verification). |

Coverage check: 10 changelog entries = 4 confirmed (#1–#4) + 5 rejected (#5–#9) + 1 unmeasured
environment precondition (#10). 7 inventory rows = 4 rows backing confirmed findings
(`services/ingest/loader`, `services/dispatch/retry-metrics`, `services/edge-cache/consumer`,
`scripts/shutdown-hook`) + 3 rows backing rejections (`services/*/logging`, `services/*/bootstrap`,
`services/billing/exporter`). No fixture row is unaccounted for.

## Review

All reviewer `mission-review/1` JSON was imported through `mission-state.py review-import` and
aggregated with `review-finalize`. Raw reviewer JSON is stored under `.mission-state/archive/` and is
referenced by path rather than transcribed here (output-compression discipline).

### Iteration 1 (completed, gate rejected)

Two independent reviewers were spawned in a single parallel message (Complex → reviewer_count = 2),
their Medium/Low findings were fixed inline, and a third differential reviewer confirmed the fixes
(M6 rule: inline fixes of Medium-or-above findings require an independent re-check before scoring).

| Reviewer | Perspective | Review evidence (state-local) |
|---|---|---|
| A | evidence fidelity & traceability | `.mission-state/archive/iter-1-331fc650-review-input-bc9cfe844132ad77.json` |
| B | migration correctness & actionability | `.mission-state/archive/iter-1-331fc650-review-input-71eccdd854138963.json` |
| verify | differential confirmation of the inline fixes | `.mission-state/archive/iter-1-331fc650-review-input-50bddf5406eed813.json` |

- Aggregate: `.mission-state/archive/iter-1-331fc650-reviews-37b581bdbfebd68f.json`
- Scoring artifact: `.mission-state/archive/iter-1-331fc650-scoring-b65ec59e08fec3b7.json`
- Findings fixed inline before scoring: **B-1** (F3 failure mode re-characterized as likely-loud and
  explicitly unmeasured), **B-2** (M1 alternative branch rewritten to require a dual-format consumer
  or an atomic cutover, removing the broken intermediate state), **B-3** (M0 runtime pre-flight row
  added), **A-1** (changelog entry #6 quoted in full).
- Reviewer parallelism WARN: `review-finalize` reported the reviewer windows as non-overlapping
  because the third (differential) reviewer necessarily ran after the fixes. A and B did run in a
  single parallel message. This warning is observational and does not affect the gate.
- **Gate result: rejected.** `mark-passes` refused with
  `低合意: 争点軸 accuracy の追加レビュー 1 名を実施して再集計してください (max-min=2.00)` — reviewer
  B scored accuracy 3.0 on the pre-fix revision while `verify` scored 5.0 on the post-fix revision,
  so the recorded agreement spread (2.00) exceeds the 1.5 gate. This is the gate working as
  intended: the three reviews are not bound to the same artifact revision.

### Round 2 — re-review of the post-fix revision (resubmitted aggregate)

Two fresh independent reviewers re-reviewed the post-fix artifact in a single parallel message, so
that all scores bind to one artifact revision. Their Medium/Low findings were again fixed inline and
a second differential reviewer confirmed the fixes.

| Reviewer | Perspective | Review evidence (state-local) |
|---|---|---|
| C | accuracy & evidence fidelity | `.mission-state/archive/iter-1-331fc650-review-input-add758b9107f28b5.json` |
| D | completeness & usability for the executing engineer | `.mission-state/archive/iter-1-331fc650-review-input-eef3ea57c012b801.json` |
| verify2 | differential confirmation of the round-2 fixes | `.mission-state/archive/iter-1-331fc650-review-input-e827fd8dec6e7c53.json` |

- Aggregate / findings evidence: `.mission-state/archive/iter-1-331fc650-reviews-43fb85b24cb75e36.json`
- Scoring artifact: `.mission-state/archive/iter-1-331fc650-scoring-db9831ae6c9377bd.json`
- Findings fixed inline before this aggregate: **D-1** (M1 now flags that the inventory names the
  `publish()` consumer but no publisher, so the pin target must be located by a separate search),
  **D-2** (changelog #10 moved out of the rejected-candidates table into its own unmeasured
  environment-precondition section), **D-3** (Evidence rows added for the by-absence rejections of #7
  and #9), **C-1** (F3's failure-mode wording softened to match its unmeasured label).
- Resubmission: `review-finalize --iteration 1 … --resubmit-reason` was used, with the reason
  recorded in state — the round-1 aggregate mixed reviewers bound to different artifact revisions,
  which is exactly what the agreement gate caught.

## Score

Tool-computed values from `review-finalize` (no hand calculation), read back from the scoring
artifacts named above.

| Field | Round 1 aggregate (rejected) | Round 2 aggregate (current) |
|---|---|---|
| `composite` | 4.42 | **4.69** |
| `min_item` | 4.0 | **4.67** |
| items: mission_achievement / accuracy / completeness / usability | 4.67 / 4.0 / 4.67 / 4.33 | 4.67 / 4.73 / 4.67 / 4.67 |
| `open_high` | 0 | **0** |
| max per-axis reviewer delta | 2.0 (accuracy: min 3.0, max 5.0) — **gate breach** | **1.0** (accuracy delta 0.5; mission/completeness/usability 1.0) |
| `threshold` | 4.0 | 4.0 |

Note: `accuracy` aggregates to 4.73 rather than the raw mean 4.83 because the rubric caps an axis
score when the reviewer files a finding on it (C-1, Low, accuracy).

## Stop Decision

- Round 1: composite 4.42 ≥ 4.0 and `open_high` 0, but the max per-axis reviewer delta was 2.0 > 1.5,
  so `closeout` returned `mark-passes-gate-failed`
  (`低合意: 争点軸 accuracy の追加レビュー 1 名を実施して再集計してください (max-min=2.00)`)
  and the loop continued. No pass was claimed at that point.
- Round 2: all gate conditions hold — findings evidence present, `evidence_high_count == open_high`
  (both 0), max agreement delta 1.0 ≤ 1.5, composite 4.69 ≥ 4.0, `min_item` 4.67 ≥ 3.5,
  `open_high` 0. `closeout` (`mark-passes` → `next`) returned exit 0.
- Iteration budget `--max-iter 2` was not exhausted; the run stops here because the gate is met, not
  because the budget ran out.
- No PR exists for this run, so Phase 7 (merge decision) does not apply. No commit, push, install,
  or network access was performed.

## Evidence

| Claim | Evidence |
|---|---|
| Only the two named fixtures were consulted | Both files read in full at the paths named in the task prompt; no other file under `benchmarks/` was opened, listed, or grepped. |
| Changelog has 10 entries; inventory has 7 rows | `upgrade-changelog.md` lines 3–18 (numbered 1–10); `usage-inventory.md` lines 5–11 (7 table rows). |
| F1 evidence | inventory row `services/ingest/loader`: "Config file still contains the deprecated `flush_interval` key kept \"for reference\"." |
| F2 evidence | inventory row `services/dispatch/retry-metrics`: `onRetry((attempt, error) => ...)` / "Two-argument callback records retry counters." |
| F3 evidence | inventory row `services/edge-cache/consumer`: "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo." |
| F4 evidence | inventory row `scripts/shutdown-hook`: `queue.drain()` / "Called synchronously as the last line before process exit." |
| Explicit ordering constraint | changelog #3: "the codec pin must be set before the first `publish()` call". |
| Rejection of #5 | inventory row `services/*/logging`: "No `warnOnce` call sites found (grep returned zero)." |
| Rejection of #6 | inventory row `services/*/bootstrap`: `connect({ timeout: 20_000 })` / "Every `connect()` call site passes an explicit timeout." |
| Rejection of #7 | Rejected **by absence** — no inventory row references buffer pooling or an allocation-sensitive path, and the entry exposes no API. There is nothing to quote; absence of a matching row is the evidence. |
| Rejection of #8 | inventory row `services/billing/exporter`: "Not yet using it; listed from the design doc." |
| Rejection of #9 | Rejected **by absence** — no inventory row references documentation; the entry has no code call site at all. |
| #10 not rejected but unmeasured | Neither fixture states the current runtime version, so no evidence exists either way; carried as migration step M0. |
| Mission state is auditable | session `cc-e79146b1-b435-4ce1-aa0a-df0f78873d76`, mission `331fc6502c14dbda`, state file `.mission-state/sessions/cc-e79146b1-b435-4ce1-aa0a-df0f78873d76.json`; plan `.mission-state/plans/f19b92bb7f4e42c6.json` (digest `sha256:f19b92bb…8ba1`); review evidence under `.mission-state/archive/`. |
| Score values are tool-computed | read back via `mission-state.py get --field` after `review-finalize`; not computed by hand. |

Explicitly **unmeasured** (stated rather than asserted):

- No relaykit upgrade was executed, no code was changed, and no test or benchmark was run. Every
  behavioural claim (raises `ConfigKeyError`, callback not invoked, decode mismatch, queue items
  dropped) is a reading of the changelog text applied to the inventory row, not an observation.
- The "~12% lower allocation rate" figure (changelog #7) is quoted, not reproduced.
- Changelog #10 (minimum runtime raised to LTS): the current runtime version is stated in neither
  fixture, so its impact is unmeasured with the given evidence.
- The inventory is described as a "repo-wide grep, current main"; its completeness is taken as given.
  Any call site the grep missed is invisible to this assessment.
- Wall-clock/token cost of this run was not instrumented, and no comparison against any other arm is
  made or implied.

## Assumptions

| id | Assumption | How it was validated / why it stands |
|---|---|---|
| A1 | The usage inventory is complete for relaykit call sites. | Stated by the fixture header: "repo-wide grep, current main". Not independently verified — no repository was searched. |
| A2 | "Breaking" is judged by whether a listed change alters behaviour at an existing call site. | Entries #7–#9 are internal/additive/documentation and expose no call site; #10 is an environment precondition and is reported as unmeasured rather than rejected. |
| A3 | `services/*/logging` and `services/*/bootstrap` are glob rows covering multiple services, and their "zero call sites" / "every call site" statements apply across all matches. | Taken from the row text verbatim ("grep returned zero", "Every `connect()` call site"). |
| A4 | Keeping the msgpack wire format (pin) and moving the consumer to JSON are alternative migrations, not both required. | Changelog #3 offers the pin as the way "to keep the old wire format"; adopting the v3 JSON default instead is the other consistent end state. Neither branch was implemented or tested. |
| A5 | Execution of the write step was performed inline by the orchestrator rather than by a spawned `mission-executor`. | Disclosed deviation from the Complex default; planning (`mission-planner`) and the two reviewers were run as separate agents, so the scored review gate is unaffected. |
| A6 | Benchmark metadata (task definitions, scoring config, answer keys) under `benchmarks/mission-vs-goal/` was never read. | Only the two fixture paths and this output path were touched under that directory. |
| A7 | No external specialist provider was used. `specialists recommend` was re-run with `--no-default-skill-roots`, recording `decision=unavailable`, `policy=fallback`, `action=continue-core`. | The benchmark forbids network access, so any external provider is unusable by construction; the first `recommend` had surfaced an external candidate requiring first-use consent, which was declined and logged as a `skipped` invocation with that reason. |

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-19 | 初版作成（mission arm, iteration 1、レビュー2名・スコア確定後に Review / Score / Stop Decision を実値化） |
