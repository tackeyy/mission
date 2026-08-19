# tail-config-spec-drift — configuration drift audit (mission arm)

- Task id: `tail-config-spec-drift`
- Task category: configuration
- Arm: mission (profile: full), complexity: Complex, `--max-iter 3`
- Mission state session: `cc-f8d09ed2-a453-41ff-a15b-d8e86c477324`, mission id `9f6b0956f4b41430`

## Mission

Audit configuration drift of two implementations and one runbook against the canonical
spec, using exactly these four fixtures:

- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-alpha.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/impl-beta.md`
- `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/runbook.md`

Deliverable: this single artifact, containing (a) a confirmed-drift table with file, key,
spec value, actual value and quoted evidence; (b) a rejected-candidates section that shows
the unit or aggregate conversion (or the reasoning) clearing each candidate; and (c) an
explicit statement of which spec constraints are violated. No commits, no network, no
package installs; writes limited to this artifact plus `.mission-state/`.

### Canonical spec baseline (quoted from `spec.md`)

> `| request_timeout_ms | 3000 | Per-request upstream timeout. |`
> `| max_retries | 3 | Applies to idempotent requests only. |`
> `| retry_backoff | exponential, base 250ms | Jitter enabled. |`
> `| queue_max_depth | 10000 | Requests beyond depth are shed. |`
> `| tls_min_version | 1.3 | Hard floor for all listeners. |`
> `| health_check_interval_s | 15 | Liveness probe cadence. |`
> `| enable_legacy_auth | false | Must stay false; scheduled for removal. |`
> `| idle_timeout_s | 90 | Connection idle close. |`
> `| log_level | info | Production default. |`
> `| db_pool_size_per_replica | 32 | Two replicas run in production. |`

`spec.md` states the authority of this table: "This table is the canonical contract.
Implementations and runbooks must match it."

## Plan

Adopted `mission-plan/1` document: `.mission-state/plan-iter1.json`
(validated by `mission-state.py planning adopt-core`, generation 1,
`validated_at: 2026-08-19T06:57:02Z`). Steps:

| Step | Action | Acceptance |
|---|---|---|
| S1 | Read the four named fixtures verbatim | All 10 spec keys extracted with values |
| S2 | Build a candidate discrepancy list per downstream file | Every non-identical key/value pair listed with quoted evidence |
| S3 | Apply unit / aggregate conversion tests to each candidate | Each candidate either confirmed or rejected with arithmetic shown |
| S4 | Map confirmed drift to the specific violated spec constraint | Each confirmed row names its constraint |
| S5 | Write this artifact with all eight required headings | Headings + drift table + rejected section + constraint statement present |
| S6 | Self-check against the task validator | All validator elements verified before review |

Out of scope by rule: any other file under `benchmarks/mission-vs-goal/` (task
definitions, scoring config, answer keys) was neither opened, listed nor grepped.

## Execution

Method: for each of the 10 spec keys, locate the corresponding entry in `impl-alpha.md`,
`impl-beta.md` and `runbook.md` (matching by normalized key name across the fixtures'
differing naming conventions: mixed `camelCase` / `SCREAMING_SNAKE` in Alpha — e.g.
`requestTimeoutMs` alongside `MAX_QUEUE_DEPTH` — uniform `SCREAMING_SNAKE` in Beta, prose
in the runbook). A pair is a *candidate* when the literal values differ. Each candidate is
then tested for a documented unit or aggregate conversion stated in the same fixture; only
candidates that survive that test are confirmed drift.

### Confirmed drift

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| `impl-alpha.md` | `request_timeout_ms` | `3000` | `27000` | spec: `` `request_timeout_ms` | 3000 `` / alpha: `requestTimeoutMs   = 27000` |
| `impl-alpha.md` | `queue_max_depth` | `10000` | `1250` | spec: `` `queue_max_depth` | 10000 `` / alpha: `MAX_QUEUE_DEPTH    = 1250` |
| `impl-alpha.md` | `enable_legacy_auth` | `false` | `true` | spec: `` `enable_legacy_auth` | false | Must stay false; scheduled for removal. `` / alpha: `enableLegacyAuth   = true` |
| `impl-beta.md` | `retry_backoff` (strategy) | `exponential, base 250ms` | `constant-interval` | spec: `` `retry_backoff` | exponential, base 250ms `` / beta: `RETRY_BACKOFF_STRATEGY=constant-interval` |
| `impl-beta.md` | `health_check_interval_s` | `15` | `75` | spec: `` `health_check_interval_s` | 15 | Liveness probe cadence. `` / beta: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| `runbook.md` | `max_retries` | `3` | `6` | spec: `` `max_retries` | 3 | Applies to idempotent requests only. `` / runbook: "the gateway will retry idempotent requests up to 6 times before shedding" |
| `runbook.md` | `tls_min_version` | `1.3` | `1.2` | spec: `` `tls_min_version` | 1.3 | Hard floor for all listeners. `` / runbook: "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window" |

Per-row conversion tests that were tried and failed (i.e. why each row is *not* a
false positive):

1. **Alpha `requestTimeoutMs = 27000`** — the key name itself carries the unit (`Ms`),
   matching the spec key `request_timeout_ms`, so both sides are already milliseconds:
   27000 ms = 27 s ≠ 3 s. No seconds/milliseconds factor (×1000) or any other stated
   conversion maps 27000 onto 3000. Alpha's own note removes an override escape hatch:
   "values above are read at boot; there is no runtime override layer in Alpha."
2. **Alpha `MAX_QUEUE_DEPTH = 1250`** — 10000 / 1250 = 8, which superficially resembles a
   bits→bytes conversion. Rejected as an explanation: `queue_max_depth` is a count of
   requests ("Requests beyond depth are shed."), not a byte/bit quantity, and
   `impl-alpha.md` documents no unit basis for this key (unlike Beta, which explicitly
   documents its tick basis). An undocumented factor-of-8 reading would be invented, not
   evidenced, so this stays confirmed drift.
3. **Alpha `enableLegacyAuth = true`** — boolean; no unit conversion exists. Alpha's note
   confirms the value is live and intentional-by-neglect: "The legacy auth flag was
   toggled during the March incident bridge and has not been revisited since."
4. **Beta `RETRY_BACKOFF_STRATEGY=constant-interval`** — an enum, not a scalar. Beta names
   the enum domain: "Backoff strategy names follow the retry library's enum
   (`constant-interval`, `exponential`, `decorrelated-jitter`)." `exponential` exists in
   that same enum and was not chosen, so `constant-interval` cannot be read as an alias
   for the spec's `exponential`.
5. **Beta `HEALTH_CHECK_INTERVAL_SECONDS=75`** — 75 / 15 = 5, and 75 is also 4500 ticks at
   60 ticks/s, so a tick-conversion reading is tempting. Rejected: the key is explicitly
   `..._SECONDS`, and Beta's conversion note is scoped to idle time only — "Beta counts
   idle time in scheduler ticks" — so the tick basis does not apply to this key. 75 s ≠ 15 s.
6. **Runbook `max_retries` = 6** — the runbook scopes it to the same population as the
   spec ("retry idempotent requests" vs. spec note "Applies to idempotent requests only"),
   so there is no scope difference to reconcile. No attempts-vs-retries off-by-one
   explains 6 (3 retries = 4 total attempts, not 6), and the runbook forbids raising the
   number further, treating 6 as the current standing value.
7. **Runbook `tls_min_version` = 1.2** — the spec calls 1.3 a "Hard floor for all
   listeners", with no rotation-window carve-out. The runbook instructs lowering the floor
   below the hard floor as normal procedure, so it contradicts the spec regardless of the
   temporary intent.

### Rejected candidates (look contradictory, are consistent)

| File | Key / statement | Apparent conflict | Conversion or reasoning that clears it |
|---|---|---|---|
| `impl-beta.md` | `IDLE_TIMEOUT_TICKS=5400` | spec `idle_timeout_s` = `90`, beta says `5400` — a 60× discrepancy at face value | Beta documents the unit: "Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per second." 5400 ticks ÷ 60 ticks/s = **90 s** = spec value. Consistent. |
| `runbook.md` | "the two replicas hold 64 pooled connections in total" | spec `db_pool_size_per_replica` = `32`, runbook says `64` — looks doubled | Aggregate vs. per-replica. Spec note: "Two replicas run in production." 32 conns/replica × 2 replicas = **64 total**. The runbook explicitly labels it an aggregate ("in total", "Alert thresholds are derived from that aggregate figure"). Consistent. |
| `impl-alpha.md` | `retryBackoff = exponential` + `retryBackoffBaseMs = 250` | spec has one key `retry_backoff` = `exponential, base 250ms`; Alpha has two keys, neither literally equal to the spec string | Representation split, not value drift: the spec's compound value decomposes into strategy = `exponential` (match) and base = 250 ms (match). Consistent. |
| `impl-beta.md` | `RETRY_BACKOFF_BASE_MS=250` | sits next to the confirmed strategy drift, so it can look drifted too | The base value matches the spec's "base 250ms" exactly. Only the strategy field drifts; this field must not be double-counted as a second finding. |
| `runbook.md` | "DEBUG is allowed only on a single canary replica for up to one hour" | spec `log_level` = `info` — DEBUG appears to contradict it | The runbook's standing instruction matches the spec ("Run all services at INFO verbosity in production"), and the spec labels `info` a "Production default" rather than a hard floor (contrast `tls_min_version`'s "Hard floor"). A bounded, single-replica, one-hour canary exception is consistent with a default. This is a judgement call on spec wording, not an arithmetic clearance — flagged as the weakest rejection in this set. |
| `runbook.md` | "Liveness probes are configured centrally; see the spec for cadence." | mentions the same key as Beta's confirmed `health_check_interval_s` drift | The runbook states no competing number; it defers to the spec. A deferral cannot contradict the spec. Not a finding against the runbook. |
| `impl-alpha.md` | `health_check_interval_s`, `idle_timeout_s` absent | two spec keys have no Alpha counterpart | Absence is not a contradiction, and the fixture is labelled an "excerpt from deployed config", so silence is not evidence of a non-conforming value. Alpha's effective values for these two keys are **unmeasured** by these fixtures. |
| `impl-alpha.md` / `impl-beta.md` | key naming style (`requestTimeoutMs`, `REQUEST_TIMEOUT_MS` vs. `request_timeout_ms`) | key strings differ from the spec everywhere | The spec constrains values, not identifier casing; every fixture uses one internally consistent convention. Not drift. |

### Per-key conformance matrix

Legend: ✔ matches spec · ✖ confirmed drift · ≈ matches after documented conversion ·
— not present in that fixture (unmeasured).

| Spec key | Spec value | impl-alpha | impl-beta | runbook |
|---|---|---|---|---|
| `request_timeout_ms` | 3000 | ✖ 27000 | ✔ 3000 | — |
| `max_retries` | 3 | ✔ 3 | ✔ 3 | ✖ 6 |
| `retry_backoff` | exponential, base 250ms | ✔ exponential / 250 | ✖ constant-interval (base 250 ✔) | — |
| `queue_max_depth` | 10000 | ✖ 1250 | ✔ 10000 | — |
| `tls_min_version` | 1.3 | ✔ 1.3 | ✔ 1.3 | ✖ 1.2 |
| `health_check_interval_s` | 15 | — | ✖ 75 | ✔ defers to spec |
| `enable_legacy_auth` | false | ✖ true | ✔ false | — |
| `idle_timeout_s` | 90 | — | ≈ 5400 ticks ÷ 60 = 90 s | — |
| `log_level` | info | ✔ info | ✔ info | ✔ INFO (+ bounded canary exception) |
| `db_pool_size_per_replica` | 32 | ✔ 32 | ✔ 32 | ≈ 64 total ÷ 2 replicas = 32 |

### Violated spec constraints (explicit statement)

The following canonical constraints from `spec.md` are violated:

1. **`request_timeout_ms` = 3000** ("Per-request upstream timeout") — violated by Alpha at
   27000 ms, a 9× longer upstream timeout than the contract allows.
2. **`queue_max_depth` = 10000** ("Requests beyond depth are shed") — violated by Alpha at
   1250; Alpha sheds load at 12.5% of the contracted depth.
3. **`enable_legacy_auth` = false** ("Must stay false; scheduled for removal") — violated
   by Alpha at `true`. This is the only spec row with an imperative "Must stay" qualifier,
   so it is the strongest-worded constraint breached.
4. **`retry_backoff` = exponential** ("Jitter enabled") — violated by Beta's
   `constant-interval` strategy; the base delay conforms but the strategy does not.
5. **`health_check_interval_s` = 15** ("Liveness probe cadence") — violated by Beta at
   75 s, a 5× slower liveness cadence.
6. **`max_retries` = 3** ("Applies to idempotent requests only") — violated by the runbook's
   guidance of up to 6 retries for the same idempotent-request population.
7. **`tls_min_version` = 1.3** ("Hard floor for all listeners") — violated by the runbook's
   instruction to set the load balancer floor to 1.2 during certificate rotation. The spec
   admits no exception window for this floor.
8. **The meta-constraint** — "This table is the canonical contract. Implementations and
   runbooks must match it." — is violated by all three downstream documents; no fixture is
   fully conformant.

Not violated by any fixture: `log_level` and `db_pool_size_per_replica` (both conform,
the latter after the documented aggregate conversion), and `idle_timeout_s` (conforms in
Beta after the documented tick conversion; unmeasured in Alpha).

## Review

Reviewers: 2 independent `mission-reviewer` subagents (Complex, no irreversible/security
signals → `review_tier` per state), spawned in a single message (parallel).
Perspectives: A = evidence fidelity / quote accuracy; B = completeness & validator
compliance. Raw `mission-review/1` JSON is stored under `.mission-state/archive/`;
aggregation was performed by `mission-state.py review-finalize` (no manual transcription).

- Reviewer window (A, `evidence-fidelity`): `2026-08-19T06:58:48Z..2026-08-19T07:01:03Z`
- Reviewer window (B, `completeness`): `2026-08-19T06:58:48Z..2026-08-19T07:01:03Z`
- Both windows overlap fully, confirming parallel (single-message) reviewer dispatch.
- Aggregated review evidence:
  `.mission-state/archive/iter-1-9f6b0956-reviews-e410813564f721fd.json`
  (`sha256:e410813564f721fd…`), generation `e410813564f721fd`.
- Per-reviewer raw `mission-review/1` inputs:
  `.mission-state/archive/iter-1-9f6b0956-review-input-4fe5778ae2c97226.json` (A) and
  `.mission-state/archive/iter-1-9f6b0956-review-input-7cd26f0efa96f3bd.json` (B).
  Full reviewer text is retained there and deliberately not transcribed here (mission
  output-compression rule).

Findings raised (both Low, both applied before pass):

| Finding | Severity | Substance | Disposition |
|---|---|---|---|
| `evidence-fidelity-1` | Low | Execution section described Alpha's naming as uniformly `camelCase`, but `MAX_QUEUE_DEPTH` is `SCREAMING_SNAKE` | Fixed: wording now reads "mixed `camelCase` / `SCREAMING_SNAKE` in Alpha". No drift row changed. |
| `completeness-1` | Low | Score section deferred all numeric gate values to `.mission-state`, so the artifact was not self-contained | Fixed: computed values embedded inline in the Score table below. |

No High or Medium finding was raised, so the M6 differential re-review requirement did not
trigger. Both reviewers independently reported zero false positives in the confirmed table
and zero missed contradictions.

## Score

Tool-computed gate values from `mission-state.py review-finalize` / `closeout`
(iteration 1). Exact numeric values are recorded in state and in
`.mission-state/archive/`; the gate semantics evaluated were:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

Computed values (source: `.mission-state/archive/iter-1-9f6b0956-scoring-45e5bd796d2918ea.json`,
`timestamp 2026-08-19T07:02:54Z`, `score_source: scoring-json`):

| Gate | Required | Actual | Verdict |
|---|---|---|---|
| `composite_score` | ≥ 4.0 (threshold) | **4.84** | ✔ |
| `min(scored_items)` | ≥ 3.5 | **4.8** (`usability` / `accuracy`) | ✔ |
| `open_high` | 0 | **0** | ✔ |
| `max_agreement_delta` | ≤ 1.5 | **0.3** (`completeness`: 4.7–5.0) | ✔ |
| `findings_evidence_path` | present | `.mission-state/archive/iter-1-9f6b0956-reviews-e410813564f721fd.json` | ✔ |
| `evidence_high_count == open_high` | equal | 0 == 0 | ✔ |

Per-axis aggregated item scores (2 scoring reviewers, 0 findings-only reviewers):

| Axis | Score | Reviewer spread (min–max, delta) |
|---|---|---|
| `mission_achievement` | 4.9 | 4.8–5.0, Δ 0.2 |
| `accuracy` | 4.8 | 4.7–4.9, Δ 0.2 |
| `completeness` | 4.85 | 4.7–5.0, Δ 0.3 |
| `usability` | 4.8 | 4.7–4.9, Δ 0.2 |

`review_agreement`: 5.0. Reviewed revision scope (git):
`base_sha = head_sha = f8a4b983b6d0e49e58d8cc530f5eb93bf97ed70e` — the artifact is
uncommitted working-tree content by benchmark rule (no commits allowed), so base and head
are pinned to the same checked-out commit and the reviewed content is identified by the
artifact digest recorded in mission state rather than by a commit range.

This benchmark run makes **no claim of superiority over any other arm**; the scores above
describe only this task artifact's internal review.

## Stop Decision

Stop when `mission-state.py closeout` returns exit 0 with
`next_action=report-complete` (`passes=true`), or a `halt_reason` is recorded. Early-stop
policy applied: iteration 1 pass is accepted when `open_high == 0` and the composite score
clears the threshold. `--max-iter 3` was the ceiling; **1 iteration was used**.

Decision: **stop after iteration 1**. All six gate conditions passed (composite 4.84 ≥ 4.0,
min item 4.8 ≥ 3.5, `open_high` 0, agreement delta 0.3 ≤ 1.5, findings evidence present).
The early-stop continue-criteria (composite in the 4.0–4.3 band, or ≥ 3 Medium findings)
were not met — composite is 4.84 and both findings were Low and already fixed — so
continuing to iteration 2 was not warranted. The authoritative `passes` / `halt_reason`
values are whatever `mission-state.py closeout` recorded in
`.mission-state/sessions/cc-f8d09ed2-a453-41ff-a15b-d8e86c477324.json`; no completion is
claimed beyond what that state records.

## Evidence

Fixture reads (the only files opened under `benchmarks/mission-vs-goal/`, plus this
artifact):

| Path | Lines used | Key evidence quoted |
|---|---|---|
| `.../config-spec-drift/spec.md` | 5–16 | full canonical table (10 keys) |
| `.../config-spec-drift/impl-alpha.md` | 5–13, 16–18 | `requestTimeoutMs   = 27000`, `MAX_QUEUE_DEPTH    = 1250`, `enableLegacyAuth   = true`, "no runtime override layer in Alpha" |
| `.../config-spec-drift/impl-beta.md` | 5–15, 18–20 | `RETRY_BACKOFF_STRATEGY=constant-interval`, `HEALTH_CHECK_INTERVAL_SECONDS=75`, `IDLE_TIMEOUT_TICKS=5400`, "the scheduler runs at 60 ticks per second" |
| `.../config-spec-drift/runbook.md` | 5–7, 11–13, 17–18, 22–23, 27–28 | "retry idempotent requests up to 6 times", "set the load balancer TLS floor to 1.2 first", "the two replicas hold 64 pooled connections in total" |

Mission-state evidence (auditable):

| Artefact | Path / value |
|---|---|
| Session state | `.mission-state/sessions/cc-f8d09ed2-a453-41ff-a15b-d8e86c477324.json` |
| Assumptions | `.mission-state/sessions/cc-f8d09ed2-a453-41ff-a15b-d8e86c477324-assumptions.md` |
| Adopted plan | `.mission-state/plan-iter1.json` (adopt-core generation 1, `validated_at 2026-08-19T06:57:02Z`) |
| Lease | `lease_id b40aaa419b11f4379398becf21ebe4b3`, `fencing_epoch 1` |
| Phase transitions | `init` → `planning adopt-core` → `advance --phase executing` → `advance --phase reviewing` → `review-import`/`review-finalize` → `closeout` |
| Review inputs | `.mission-state/archive/iter-1-9f6b0956-review-input-4fe5778ae2c97226.json` (evidence-fidelity), `...-7cd26f0efa96f3bd.json` (completeness) |
| Review aggregate | `.mission-state/archive/iter-1-9f6b0956-reviews-e410813564f721fd.json` (`sha256:e410813564f721fd…`) |
| Scoring artifact | `.mission-state/archive/iter-1-9f6b0956-scoring-45e5bd796d2918ea.json` (`sha256:45e5bd796d2918ea…`) |

Arithmetic checks performed (all reproducible from the quoted text):

- 5400 ticks ÷ 60 ticks/s = 90 s (Beta idle timeout — clears)
- 32 conns/replica × 2 replicas = 64 total (runbook DB pool — clears)
- 27000 ms ÷ 1000 = 27 s ≠ 3 s (Alpha timeout — does not clear)
- 10000 ÷ 1250 = 8 with no documented factor-of-8 unit in Alpha (does not clear)
- 75 s ÷ 15 s = 5, and Beta's tick basis is scoped to idle time only (does not clear)

Explicitly unmeasured in this run:

- Alpha's effective `health_check_interval_s` and `idle_timeout_s` (absent from the excerpt).
- Beta's `request_timeout_ms` runtime override behaviour (Beta states no override policy).
- Whether any downstream document outside these four fixtures also drifts — not examined,
  because reading anything else under `benchmarks/mission-vs-goal/` is out of bounds.
- Runtime/deployed behaviour of either service: nothing was executed, only documents read.

## Assumptions

| # | Assumption | Basis / validation |
|---|---|---|
| A1 | The `spec.md` table is authoritative over both implementations and the runbook | Stated verbatim: "This table is the canonical contract. Implementations and runbooks must match it." |
| A2 | Keys match across fixtures despite naming-convention differences (`requestTimeoutMs` ↔ `REQUEST_TIMEOUT_MS` ↔ `request_timeout_ms`) | Each fixture uses one consistent convention; semantic mapping is 1:1 with no ambiguity |
| A3 | A unit conversion is only admissible when the fixture itself documents the unit basis | Beta documents its tick basis; Alpha documents no basis for `MAX_QUEUE_DEPTH`, so an inferred ×8 factor would be unevidenced |
| A4 | Beta's tick basis applies only to idle time | Beta's note is explicitly scoped: "Beta counts idle time in scheduler ticks" |
| A5 | The runbook's bounded DEBUG canary allowance does not violate `log_level` | Spec calls `info` a "Production default", not a hard floor; contrast the explicit "Hard floor" on `tls_min_version`. This is the least certain judgement in this artifact and is surfaced as such |
| A6 | Absent keys are unmeasured, not drifted | Alpha's fixture is labelled an "excerpt from deployed config" |
| A7 | The local-authoring bootstrap sync (`mission-local-authoring-sync.sh`) was intentionally skipped | The benchmark prompt forbids network access, and this run executes inside a detached-HEAD benchmark checkout rather than the authoring worktree; skipping is recorded here rather than silently omitted |
| A8 | Writes limited to this artifact and `.mission-state/` | Benchmark rule; no commit, push, install or network call was made |

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-19 | 初版作成（mission arm iteration 1 成果物） |
