# relaykit v2 → v3 upgrade impact assessment

Task id: `tail-dependency-upgrade-impact` · Category: refactoring · Arm: mission (profile: full) · Complexity: Complex

## Mission

Assess the relaykit v2-to-v3 upgrade using exactly two fixture files, and produce one auditable artifact that:

1. maps every **impactful** breaking change to the concrete call site it affects, with a verbatim quote from the usage inventory as evidence;
2. states the migration steps, including any required ordering constraint;
3. rejects, with inventory evidence, every changelog entry that looks breaking but affects no call site.

Evidence base (the only files read for this assessment):

- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md` — "relaykit v3.0.0 changelog (upstream, verbatim)", 10 numbered entries.
- `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md` — "relaykit usage inventory (repo-wide grep, current main)", 7 table rows.

Out of scope: no repository source code was opened, no dependency was installed, no command was run against a live service, and no other file under `benchmarks/mission-vs-goal/` was read. This artifact makes no claim about the relative merit of any benchmark arm.

## Plan

Adopted plan: `.mission-state/plans/5ec4ca46c905354e.json` (`mission-plan/1`, digest `sha256:5ec4ca46c905354e…`, source `core`, generation 1).

| # | Step | Completion condition |
|---|---|---|
| S1 | Enumerate all 10 changelog entries and all 7 inventory rows verbatim | Both fixtures fully enumerated |
| S2 | Classify each changelog entry as confirmed-impactful / rejected-no-call-site / unmeasured | Every entry classified exactly once; each confirmed entry names one call site with a verbatim quote |
| S3 | Derive the migration sequence and its ordering constraints | The codec-pin-before-first-`publish()` constraint is stated as hard; derived constraints are labelled as derived |
| S4 | Write the single artifact at the required path | File exists with all eight required headings and a rejected-candidates section |
| S5 | Run one scored review iteration and record the stop decision | Two independent reviewers scored; `review-finalize` and `closeout` recorded in mission state |

## Execution

The cross-product of 10 changelog entries × 7 inventory rows was evaluated entry by entry. Each changelog entry was matched against the inventory by the API symbol it names (`parseConfig`, `onRetry`, `publish()`, `Queue.drain()`, `Logger.warnOnce`, `connect()`, `Queue.peek()`), and the inventory row's Detail column was then read to decide whether the change actually reaches that call site.

### Confirmed findings — breaking changes with an affected call site

**C1 — `parseConfig` strict mode rejects the retained `flush_interval` key.**

- Changelog (verbatim): "`parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2 silently ignored them)."
- Affected call site: `services/ingest/loader`, inventory usage `parseConfig(raw)`.
- Inventory evidence (verbatim): "Config file still contains the deprecated `flush_interval` key kept \"for reference\"."
- Impact: the retained key `flush_interval` is exactly the "unknown key" class the changelog describes. Under v3 the same config that parsed in v2 raises `ConfigKeyError`. This is a load-time hard failure, not a degradation.

**C2 — `onRetry` two-argument callback stops being invoked.**

- Changelog (verbatim): "The `onRetry` hook signature changed from `(attempt, error)` to a single `(context)` object; two-argument callbacks are no longer invoked."
- Affected call site: `services/dispatch/retry-metrics`, inventory usage `onRetry((attempt, error) => ...)`.
- Inventory evidence (verbatim): "Two-argument callback records retry counters."
- Impact: the registered callback matches the retired `(attempt, error)` shape verbatim, so it is "no longer invoked". The failure is silent — retry counters simply stop advancing rather than raising — which makes this the finding most likely to survive an upgrade unnoticed.

**C3 — `publish()` default encoding change breaks the msgpack consumer.**

- Changelog (verbatim): "`publish()` default payload encoding changed from msgpack to JSON. Pin a codec explicitly to keep the old wire format; the codec pin must be set before the first `publish()` call."
- Affected call site: `services/edge-cache/consumer`, inventory usage "subscribes to `publish()` output".
- Inventory evidence (verbatim): "Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo."
- Impact: the producer's default flips to JSON while the consumer still runs a msgpack reader, and the inventory states that the escape hatch the changelog offers (an explicit codec pin) is absent repo-wide — "no codec pin is set anywhere in the repo". The wire format therefore diverges on the first publish after the upgrade. This is also the only entry that carries its own ordering constraint (see Migration steps, O1).

**C4 — `Queue.drain()` becoming async breaks the synchronous shutdown hook.**

- Changelog (verbatim): "`Queue.drain()` is now async and returns a Promise; synchronous callers will no longer block until the queue is empty."
- Affected call site: `scripts/shutdown-hook`, inventory usage `queue.drain()`.
- Inventory evidence (verbatim): "Called synchronously as the last line before process exit."
- Impact: the inventory describes precisely the "synchronous caller" the changelog warns about, and the call is the last statement before exit, so nothing downstream awaits the returned Promise. The process exits with the queue potentially non-empty — silent message loss at shutdown rather than a raised error.

### Rejected candidates — changelog entries that look breaking but affect no call site

**R1 — `Logger.warnOnce` removal.**

- Changelog (verbatim): "`Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key."
- Why it looked breaking: it is an outright **API removal**, normally the highest-severity class in a major bump, and the inventory does list a logging row (`services/*/logging`), so a symbol match seemed likely.
- Why it is not a real finding: the inventory row for `services/*/logging` records usage `Logger.warn`, and its Detail states verbatim: "No `warnOnce` call sites found (grep returned zero)." The removed symbol has zero call sites; the surviving usage is the replacement API the changelog itself recommends. No migration work.

**R2 — `connect()` default timeout lowered from 30s to 10s.**

- Changelog (verbatim): "`connect()` default timeout lowered from 30s to 10s. Call sites passing an explicit timeout are unaffected."
- Why it looked breaking: a silent behavioural change to a default is a classic upgrade hazard — it produces new timeout errors under load with no compile-time or load-time signal — and the inventory does contain `connect()` call sites, so the symbol matches.
- Why it is not a real finding: the change only reaches call sites relying on the default. The inventory row for `services/*/bootstrap` records usage `connect({ timeout: 20_000 })` with the Detail: "Every `connect()` call site passes an explicit timeout." Both the changelog's own exemption clause ("Call sites passing an explicit timeout are unaffected") and the inventory's universal quantifier ("Every") are satisfied, so no call site falls back to the lowered default. Note that the explicit value `20_000` sits between the old and new defaults, which is why the surviving behaviour is 20s and not 10s or 30s.

**R3 — `Queue.peek()` new API.**

- Changelog (verbatim): "New `Queue.peek()` API."
- Why it looked breaking: the inventory explicitly names it at `services/billing/exporter` with usage `Queue.peek()`, so a naive symbol join between the two fixtures produces a match — the single most likely false positive in this pair of files.
- Why it is not a real finding: the entry is **additive**, not breaking — it adds a method rather than changing or removing one. And the inventory qualifies the row itself: the usage is annotated "(planned)" with the Detail "Not yet using it; listed from the design doc." There is no executing call site to migrate.

**R4 — Internal buffer pooling rewrite.**

- Changelog (verbatim): "Internal buffer pooling rewritten; ~12% lower allocation rate."
- Why it looked breaking: "rewritten" in a major-version changelog reads as a large structural change, and a quantified performance delta ("~12%") can be mistaken for a measured behavioural change requiring validation.
- Why it is not a real finding: the change is described as **internal**, with no public API surface named, and no inventory row references buffer pooling or allocation behaviour. All 7 inventory rows were examined and none references buffer pooling or allocation behaviour, so the absence is exhaustive over the permitted evidence base rather than assumed. There is no call site to map it to. The ~12% allocation figure is an upstream claim quoted from the changelog; it is **unmeasured** here — nothing in either fixture verifies it in this repository.

**R5 — Documentation site move.**

- Changelog (verbatim): "Documentation moved to a new site."
- Why it looked breaking: it appears in the same numbered list as the genuine breaking changes, so position alone gives it unearned weight.
- Why it is not a real finding: it changes no runtime behaviour and names no API. All 7 inventory rows were examined and none references documentation, so the absence is exhaustive over the permitted evidence base rather than assumed. Only bookmarks and doc links are affected, which is outside the call-site scope of this assessment.

**R6 — Minimum supported runtime raised to LTS. (Rejected as unmappable, not as harmless.)**

- Changelog (verbatim): "Minimum supported runtime raised to LTS."
- Why it looked breaking: a raised minimum runtime genuinely *can* block an upgrade outright, ahead of any code change.
- Why it is not a confirmed finding here: it is an environment-level constraint, not a call-site-level one, and **no inventory row records a runtime version** — the inventory contains only call sites and usages. Its impact is therefore **unmeasured**: this assessment can neither confirm nor clear it from the two fixtures, and it is listed here as unmappable rather than as verified-harmless. Verifying it would require the runtime version of the deploy targets, which is outside the permitted evidence base. It is the one entry where "no affected call site" is a limitation of the inventory rather than an all-clear.

### Migration steps and ordering constraints

Ordering constraint sources are labelled: **quoted** = stated verbatim in the changelog; **derived** = inferred from the semantics of the confirmed findings, not stated in either fixture.

| Order | Step | Addresses | Constraint |
|---|---|---|---|
| M1 | Remove the `flush_interval` key from the `services/ingest/loader` config (or, if it must be retained, move it outside the parsed document). | C1 | **Derived**: must land **before** the v3 upgrade. `parseConfig` is called on the existing config at load, so shipping v3 first turns the first start into a `ConfigKeyError`. Safe under v2, which "silently ignored" unknown keys. |
| M2 | Set an explicit codec pin so `publish()` keeps the msgpack wire format — or, alternatively, cut `services/edge-cache/consumer` over to a JSON reader in the same deploy. | C3 | **Quoted (hard)**: "the codec pin must be set before the first `publish()` call." See O1 below. |
| M3 | Apply the relaykit v2 → v3 upgrade. | — | Gated by M1 and M2. |
| M4 | Rewrite the `services/dispatch/retry-metrics` callback from `(attempt, error)` to the single `(context)` object. | C2 | **Derived**: the `(context)` shape does not exist in v2, so this lands with or immediately after M3. Because the old callback fails silently, treat a non-advancing retry counter as the verification signal. |
| M5 | Make `scripts/shutdown-hook` await the Promise returned by `queue.drain()` (and make its caller async, since the call is the last line before exit). | C4 | **Derived**: `drain()` returns a Promise only under v3, so this lands with or immediately after M3. |

**O1 — the one hard ordering constraint (quoted).** The codec pin must be established **before the first `publish()` call** under v3. This is a stricter requirement than "before the upgrade completes": there is no window in which v3 is live and publishing while the pin is still absent. Combined with the inventory fact that "no codec pin is set anywhere in the repo", M2 must be part of the same deploy as M3 and must execute earlier in process startup than any publish path — a follow-up commit is not sufficient, because messages published in the gap are already JSON-encoded and unreadable by the msgpack consumer at `services/edge-cache/consumer`.

**O2 — M1 before M3 (derived).** Config parsing happens at load, so the `flush_interval` removal must precede the upgrade or the service will not start.

M4 and M5 are mutually independent and may be done in either order relative to each other.

## Review

Reviewed against the task validator, clause by clause:

| Validator clause | Where satisfied | Status |
|---|---|---|
| Each impactful breaking change mapped to its affected call site | C1–C4, each naming one inventory call site | Met |
| Quoted inventory evidence per mapping | C1–C4 each carry a verbatim Detail-column quote | Met |
| Migration steps stated | M1–M5 table | Met |
| Ordering constraints stated | O1 (quoted, hard) and O2 (derived); M4/M5 marked order-independent | Met |
| Rejected-candidates section for breaking changes with no affected call site | R1–R6, each with "why it looked suspicious" and inventory evidence | Met |
| Confirmed vs rejected explicitly separated | Two separate subsections under Execution | Met |
| Unmeasured items labelled | R4 (~12% allocation claim), R6 (runtime), Assumptions A1 | Met |
| Exactly one artifact, no superiority claim | This file only; no arm comparison made | Met |

Coverage check: all 10 changelog entries are accounted for — entries 1–4 confirmed (C1–C4), entries 5–9 rejected (R1, R2, R3, R4, R5), entry 10 rejected-as-unmappable/unmeasured (R6). All 7 inventory rows are cited — `services/ingest/loader` (C1), `services/dispatch/retry-metrics` (C2), `services/edge-cache/consumer` (C3), `scripts/shutdown-hook` (C4), `services/*/logging` (R1), `services/*/bootstrap` (R2), `services/billing/exporter` (R3).

Two independent mission reviewers scored this artifact in iteration 1; their raw `mission-review/1` records are stored under `.mission-state/archive/` and aggregated by `mission-state.py review-finalize`.

## Score

Tool-computed gate values from `mission-state.py` (iteration 1) — see the Evidence table for the recorded figures. The pass predicate applied is the standard mission gate: findings evidence path exists, `evidence_high_count == open_high`, `max_agreement_delta <= 1.5`, `composite_score >= threshold (4.0)`, `min(scored_items) >= 3.5`, and `open_high == 0`.

## Stop Decision

Stopping condition is the mission gate above, evaluated by `mission-state.py closeout` (`mark-passes` → `next`) rather than by self-assessment. `--max-iter 2` was in force.

**Outcome: halted, not passed.** Iteration 1 completed one full scored review cycle and every *quality* gate cleared (`composite_score` 4.94 ≥ 4.0, `min_item` 4.75 ≥ 3.5, `open_high` 0, `max_agreement_delta` 0.5 ≤ 1.5, findings evidence recorded). `closeout` nevertheless refused `mark-passes` on a separate governance gate: the specialist selection checkpoint. `mission-state.py specialists recommend` proposed the external provider `oracle` (role `oracle-reviewer`, `selection_id` `sel_ffc87c17727467d5955491c8d736d6c9`), which sits in `lifecycle_state: candidate` / `reason_code: awaiting-confirmation` pending first-use consent. Recording that consent requires explicit user approval and would entail an external provider call — both outside what this run permits (no network access, and the orchestrator must not self-approve consent). The run was therefore terminated with `mark-halt --category awaiting-approval`; a second iteration was not started, because the blocker is a consent decision rather than a remediable artifact defect.

No pass is claimed. The artifact itself is complete against the task validator; the unmet condition is the mission-loop consent checkpoint, not the analysis.

Two artifact edits were made after the reviewers returned, and the recorded artifact identity was refreshed each time so the gate could re-verify: (1) the inline fix for Low finding B-1, and (2) filling this document's Score table with the tool-computed values, which cannot exist before aggregation runs. Both are disclosed here rather than left implicit.

## Evidence

| Item | Value / reference |
|---|---|
| Fixture: changelog | `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/upgrade-changelog.md` (10 entries, read verbatim) |
| Fixture: inventory | `benchmarks/mission-vs-goal/fixtures/tail/dependency-upgrade-impact/usage-inventory.md` (7 rows, read verbatim) |
| Mission state | `.mission-state/sessions/cc-954b0e5d-332b-4e19-b9e4-4511b1740495.json`, mission id `d74dccd6145581b9` |
| Adopted plan | `.mission-state/plans/5ec4ca46c905354e.json`, digest `sha256:5ec4ca46c905354e1b6616390135d8a8ed61422e156c68ed86bbe42d5d9e9485` |
| Reviewer records (raw) | `.mission-state/archive/` — `mission-review/1` JSON, one per reviewer, imported via `review-import` |
| Score aggregation | `mission-state.py review-finalize --iteration 1 --min-reviewers 2` |
| Gate evaluation | `mission-state.py closeout` |
| Confirmed findings | C1 `services/ingest/loader` · C2 `services/dispatch/retry-metrics` · C3 `services/edge-cache/consumer` · C4 `scripts/shutdown-hook` |
| Rejected candidates | R1 `Logger.warnOnce` · R2 `connect()` timeout · R3 `Queue.peek()` · R4 buffer pooling · R5 docs move · R6 runtime (unmeasured) |
| Hard ordering constraint | O1, quoted from changelog entry 3: "the codec pin must be set before the first `publish()` call" |
| Not executed | No commit, no push, no package install, no network access, no service invocation |
| Unmeasured | Upstream "~12% lower allocation rate" (not verified here); minimum-runtime impact (no runtime data in the inventory); actual repository source code (not opened — all call-site claims are inventory-derived) |

Recorded gate figures for iteration 1:

| Gate field | Value |
|---|---|
| `composite_score` | 4.94 |
| `items` | mission_achievement 5.0 · accuracy 5.0 · completeness 4.75 · usability 5.0 |
| `min_item` | 4.75 (gate: ≥ 3.5) |
| `open_high` | 0 |
| `review_agreement` | 5.0 |
| `max_agreement_delta` | 0.5 (completeness; all other axes 0.0 — gate: ≤ 1.5) |
| `threshold` | 4.0 |
| `reviewers` | 2, spawned in a single parallel message (window `2026-08-19T01:12:03Z..2026-08-19T01:15:25Z`) |
| `findings_evidence_path` | `.mission-state/archive/iter-1-d74dccd6-reviews-e0ed51d699eedb4e.json` |
| `scoring_evidence_path` | `.mission-state/archive/iter-1-d74dccd6-scoring-f192a542ef475134.json` |
| `revision_scope` | git, base = head = `f8a4b983b6d0e49e58d8cc530f5eb93bf97ed70e` |
| `iteration` | 1 of max 2 |

Reviewer findings in iteration 1: one Low finding (B-1, completeness) — the rejections R4 and R5 rested on negative inventory evidence without stating the scan scope. It was fixed inline before finalize by adding an explicit "all 7 inventory rows were examined" statement to both, and the artifact identity was re-recorded in mission state after that edit. No High or Medium findings were raised, so the M6 differential re-review requirement did not apply.

## Assumptions

- **A1 — Inventory is the sole call-site authority.** No repository source was opened, so every call-site claim is inventory-derived rather than code-verified. If the inventory's repo-wide grep is stale or incomplete, findings C1–C4 and rejections R1–R3 inherit that error. In particular, R1's "grep returned zero" and R2's "Every `connect()` call site" are universal claims that this assessment accepts as stated but did not independently reproduce.
- **A2 — Changelog entry 10 is unmeasured, not cleared.** The inventory records no runtime versions, so the raised minimum runtime is unmappable from the permitted evidence base. It is listed in R6 as a limitation, not as verified-harmless.
- **A3 — No network, install, commit or push.** The local-authoring sync step of the mission skill was skipped for that reason, and the repository-root `scripts/mission-state.py` was used for all state operations. This is a deliberate deviation from the skill's bootstrap step, recorded here rather than silently taken.
- **A4 — Ordering-constraint provenance.** Only O1 is stated verbatim in the changelog. O2 and the "with or immediately after M3" placement of M4/M5 are derived from the semantics of the confirmed findings; they are labelled as derived in the migration table and are not attributed to the fixtures.
- **A5 — Severity ranking is judgement, not fixture data.** Neither fixture assigns severities. The observation that C2 and C4 fail silently while C1 fails loudly is inferred from the changelog wording ("no longer invoked", "will no longer block") and is not a quoted severity rating.
- **A6 — No benchmark comparison.** This artifact assesses the upgrade only. It makes no claim about arm-versus-arm performance.
