# tail-config-spec-drift — Mission Artifact (Arm: mission)

## Mission

Audit configuration drift against the canonical spec at
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`. Read exactly
four fixtures — `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md` — and find
every place where an implementation or the runbook contradicts the spec, quoting
key and both values. Entries that look contradictory but reconcile after a unit or
aggregate conversion must be explicitly rejected with the conversion shown.

Task category: `configuration`. Task id: `tail-config-spec-drift`. Arm: `mission`,
profile `full`. Mission complexity: **Complex** (per benchmark harness instruction).

Validator requirement (restated, not read from the out-of-scope scoring config —
this is the validator text given verbatim in the task prompt): the artifact must
include a confirmed-drift table (file, key, spec value, actual value, quoted
evidence), a rejected-candidates section with the conversion/reasoning that clears
each one, and an explicit statement of which spec constraints are violated.

## Plan

1. Initialize `/mission` state (`mission-state.py init`, complexity `Complex`,
   files scoped to this artifact only) and record scope/isolation assumptions
   before reading anything (Phase 0/1).
2. Read exactly the four named fixtures (no other path under
   `benchmarks/mission-vs-goal/`).
3. Build a canonical key inventory from `spec.md`, then walk each key through
   `impl-alpha.md`, `impl-beta.md`, and `runbook.md`, classifying each observation
   as **matches spec**, **confirmed drift**, or **looks-contradictory-but-reconciles**
   (unit/aggregate conversion).
4. Draft the artifact (this file) with the four required evidence sections.
5. Run mission-reviewer sub-skill in parallel (Complex → 3 reviewers, perspectives
   A/mission-achievement+accuracy of confirmed drift, B/accuracy of rejected
   candidates and conversions, C/completeness+usability of the artifact against the
   validator), each briefed with the identical fixture-only read constraint.
6. Aggregate reviews deterministically (`aggregate-reviews`), push the score
   (`push-score --scoring-json`), and check the pass gate
   (`composite ≥ 4.0`, `min(items) ≥ 3.5`, `open_high == 0`,
   `max_agreement_delta ≤ 1.5`).
7. If gate fails, run `mission-critic`, fix, re-review the diff only, re-score.
   If it passes, `mark-passes` and stop.

Deviation from the default Complex profile: per `.mission-state/sessions/<sid>-assumptions.md`,
no external GitHub issue exists (issue-ref recorded as `none`) since this is a
sandboxed benchmark run, not a repo feature request.

## Execution

### Step 2-3: Canonical key inventory (from `spec.md`) and per-file walk

Spec keys read verbatim from `spec.md`:

`request_timeout_ms`=3000, `max_retries`=3, `retry_backoff`=exponential/base 250ms,
`queue_max_depth`=10000, `tls_min_version`=1.3, `health_check_interval_s`=15,
`enable_legacy_auth`=false, `idle_timeout_s`=90, `log_level`=info,
`db_pool_size_per_replica`=32 (note: "Two replicas run in production").

**impl-alpha.md** (`alpha/config/production.conf`):
`requestTimeoutMs=27000`, `maxRetries=3`, `retryBackoff=exponential`,
`retryBackoffBaseMs=250`, `MAX_QUEUE_DEPTH=1250`, `tlsMinVersion=1.3`,
`enableLegacyAuth=true`, `logLevel=info`, `dbPoolSizePerReplica=32`. No key present
for `health_check_interval_s` or `idle_timeout_s` in this excerpt — not enough
information to compare those two keys for Alpha; not counted as a finding either
way (no value to quote).

**impl-beta.md** (`beta/config/production.env`):
`REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`, `RETRY_BACKOFF_STRATEGY=constant-interval`,
`RETRY_BACKOFF_BASE_MS=250`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`,
`HEALTH_CHECK_INTERVAL_SECONDS=75`, `ENABLE_LEGACY_AUTH=false`,
`IDLE_TIMEOUT_TICKS=5400`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`.
Beta's own doc note: "the scheduler runs at 60 ticks per second."

**runbook.md**: retry guidance ("up to 6 times before shedding"), TLS rotation
guidance ("set the load balancer TLS floor to 1.2 first"), logging guidance ("Run
all services at INFO verbosity in production. DEBUG is allowed only on a single
canary replica for up to one hour."), DB note ("the two replicas hold 64 pooled
connections in total"), health note (defers to spec cadence explicitly).

### Confirmed-drift table

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | `3000` | `27000` | `requestTimeoutMs   = 27000` (spec: `request_timeout_ms` \| 3000 \| Per-request upstream timeout.) |
| impl-alpha.md | `queue_max_depth` | `10000` | `1250` | `MAX_QUEUE_DEPTH    = 1250` (spec: `queue_max_depth` \| 10000 \| Requests beyond depth are shed.) |
| impl-alpha.md | `enable_legacy_auth` | `false` | `true` | `enableLegacyAuth   = true` plus "The legacy auth flag was toggled during the March incident bridge and has not been revisited since." (spec: `enable_legacy_auth` \| false \| Must stay false; scheduled for removal.) |
| impl-beta.md | `retry_backoff` (strategy component) | `exponential, base 250ms` | `constant-interval` strategy (base ms matches at 250) | `RETRY_BACKOFF_STRATEGY=constant-interval` (spec: `retry_backoff` \| exponential, base 250ms \| Jitter enabled.) |
| impl-beta.md | `health_check_interval_s` | `15` | `75` | `HEALTH_CHECK_INTERVAL_SECONDS=75` (spec: `health_check_interval_s` \| 15 \| Liveness probe cadence.) — both values are in seconds (key name states `SECONDS`), so there is no unit conversion that reconciles 75 with 15; this is a direct 5x drift. |
| runbook.md | `max_retries` | `3` (idempotent requests only) | effectively `6` | "the gateway will retry idempotent requests up to 6 times before shedding" (spec: `max_retries` \| 3 \| Applies to idempotent requests only.) |
| runbook.md | `tls_min_version` | `1.3` (hard floor) | temporarily `1.2` | "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window, then proceed with the rotation" (spec: `tls_min_version` \| 1.3 \| Hard floor for all listeners.) |

7 confirmed drift findings across 2 implementations + the runbook.

### Rejected candidates (look contradictory, but are not real drift)

| # | Candidate | Why it looks suspicious | Why it's rejected (conversion/reasoning shown) |
|---|---|---|---|
| R1 | impl-beta.md `IDLE_TIMEOUT_TICKS=5400` vs spec `idle_timeout_s`=90 | 5400 ≠ 90 at face value, and the unit suffix (`TICKS` vs `_s`) differs from every other Beta key, which otherwise mirror the spec's seconds/ms units | Beta's own note: "the scheduler runs at 60 ticks per second." `5400 ticks ÷ 60 ticks/s = 90s`, which matches the spec's `idle_timeout_s = 90` exactly. Confirmed consistent after unit conversion — **not a finding**. |
| R2 | runbook.md "the two replicas hold 64 pooled connections in total" vs spec `db_pool_size_per_replica`=32 | 64 ≠ 32 at face value | Spec's own note on that key says "Two replicas run in production." `32 per replica × 2 replicas = 64` aggregate, which matches the runbook's total exactly. Confirmed consistent after aggregate conversion — **not a finding**. |
| R3 | runbook.md logging guidance ("DEBUG is allowed only on a single canary replica for up to one hour") vs spec `log_level`=info | Introduces a different log level than the spec value, in production | Spec's note on `log_level` reads "Production default," not a hard floor/ceiling (contrast with `tls_min_version`'s explicit "Hard floor for all listeners" wording). In this spec's vocabulary, "Production default" denotes the value applied absent explicit override, unlike "Hard floor for all listeners," which absolutely forecloses exceptions — so a narrow, time-boxed operational exception (one canary replica, ≤1 hour) does not contradict a *default*, whereas the same kind of exception against `tls_min_version` would contradict a *floor*. The runbook's own sentence immediately preceding it — "Run all services at INFO verbosity in production" — restates the spec default before carving out the canary exception. Reasoning, not a unit conversion, clears this. **Not a finding.** |

Also checked and found **consistent, not candidates at all** (included for completeness, not as rejected-candidate entries since they never looked contradictory): `max_retries`/`retry_backoff` base-ms/`tls_min_version`/`log_level`/`db_pool_size_per_replica` in impl-beta.md all match spec verbatim; `max_retries`/`retry_backoff` (both fields)/`tls_min_version`/`log_level`/`db_pool_size_per_replica` in impl-alpha.md all match spec verbatim; runbook.md's health section explicitly defers to the spec's cadence ("Liveness probes are configured centrally; see the spec for cadence.") rather than contradicting it.

### Explicit statement of spec constraints violated

- **`enable_legacy_auth: false`** ("Must stay false; scheduled for removal") — violated by impl-alpha.md (`enableLegacyAuth = true`).
- **`tls_min_version: 1.3`** ("Hard floor for all listeners") — violated by runbook.md's rotation guidance permitting a temporary floor of `1.2`.
- **`max_retries: 3`** ("Applies to idempotent requests only") — violated in effect by runbook.md's guidance permitting up to 6 retries, double the spec ceiling.
- **`request_timeout_ms: 3000`** — violated by impl-alpha.md (`requestTimeoutMs = 27000`, 9x spec).
- **`queue_max_depth: 10000`** — violated by impl-alpha.md (`MAX_QUEUE_DEPTH = 1250`, an 8x stricter/lower shedding threshold than spec defines).
- **`retry_backoff: exponential, base 250ms`** (strategy component) — violated by impl-beta.md (`RETRY_BACKOFF_STRATEGY=constant-interval`); the base-ms component (250) itself is not violated.
- **`health_check_interval_s: 15`** — violated by impl-beta.md (`HEALTH_CHECK_INTERVAL_SECONDS=75`, 5x spec).

No spec constraint is violated by: `max_retries`/`retry_backoff` fields, `tls_min_version`, `log_level`, or `db_pool_size_per_replica` in impl-beta.md; nor by `max_retries`, `retry_backoff` fields, `tls_min_version`, `log_level`, or `db_pool_size_per_replica` in impl-alpha.md; nor by the runbook's health-cadence guidance.

## Review

**Mechanism deviation (recorded, not silent)**: `Skill(skill="mission-reviewer", ...)`
failed with a generic `Execute skill: mission-reviewer` error on all 3 attempts in
this sandboxed benchmark working directory (root cause not surfaced by the error;
plausibly the packaged `context: fork` sub-skill wiring is unavailable in this
scratchpad clone). Fallback: 3 independent `general-purpose` Agent-tool subagents
were spawned in a single message, each briefed with the mission-reviewer role
instructions, scoring rubric, `mission-review/1` JSON schema, and the identical
fixture-only read constraint (only the 4 named fixtures + this artifact). This
substitutes the invocation *mechanism* only; the *process* (3 independent
reviewers, same rubric, same JSON contract) is unchanged. Recorded in
`.mission-state/sessions/cc-b354c73b-0081-44d4-b693-af44c3a97134-assumptions.md`.

Reviewer count: 3 (Complex profile). Perspectives: **A** = mission achievement +
accuracy of the confirmed-drift table; **B** = completeness + accuracy of the
rejected-candidates conversions; **C** = usability + structural completeness
against the validator. Raw JSON outputs archived at
`.mission-state/archive/mission-reviewer-iter1-f2e47f22-{a,b,c}.json`.

### Perspective A summary (mission achievement / accuracy of confirmed-drift table)

Independently re-derived the 10-key spec inventory and walked all 3 fixtures.
Verdict: all 7 confirmed-drift rows verified accurate (spec value, actual value,
and quoted evidence all matched fixture text); no missed drifts and no false
positives found. One Low finding: the Row 7 (TLS) quote used an ellipsis that
dropped the operational-rationale clause — **fixed** in this artifact (see
Confirmed-drift table above, now full verbatim quote). Scores: mission_achievement
5.0, accuracy 4.7, completeness 4.9, usability 4.9.

### Perspective B summary (completeness / accuracy of rejected candidates)

Independently re-derived R1's arithmetic (5400 ÷ 60 = 90) and R2's aggregate
(32 × 2 = 64) directly from fixture text; confirmed both conversions correct and
self-contained, and found no missed reconcilable candidates after a systematic
pass over every fixture value. One Low finding: R3's rejection relied on an
unstated premise (that "Production default" implies overrideability while "Hard
floor" does not) — **fixed** in this artifact (see Rejected-candidates R3, now
states the premise explicitly). Scores: mission_achievement 5.0, accuracy 5.0,
completeness 5.0, usability 4.7.

### Perspective C summary (usability / structural completeness)

Confirmed all 8 required headings present as markdown headings and all 3
validator requirements explicitly met. Two Low findings: (C-1) the Review/Score/
Stop Decision sections were still placeholders at review time — **fixed** by this
update; (C-2) the Evidence section referenced an unresolved `<session-id>` token —
**fixed** below (Evidence section now states the literal session id). Scores:
mission_achievement 4.8, accuracy 4.9, completeness 4.5, usability 4.6.

### Disposition of findings

No High or Medium findings from any reviewer. 4 Low findings total (A-1, B-1,
C-1, C-2), all addressed above/below in this same iteration (no re-review spawned
for Low-only fixes, consistent with the rubric's absolute-evaluation model where
Low findings cap an axis at 4.3–4.7 rather than blocking pass).

## Score

Deterministic aggregation via `mission-state.py aggregate-reviews` (3 reviewer
JSONs → `.mission-state/archive/mission-scorer-iter1-f2e47f22.json`) and
`mission-state.py push-score --scoring-json` (iteration 1):

| Axis | Score |
|---|---|
| mission_achievement | 4.93 |
| accuracy | 4.87 |
| completeness | 4.80 |
| usability | 4.73 |

- **composite_score** (mean of 4 axes) = **4.83**
- **min(scored items)** = **4.73**
- **open_high** = **0**
- **review_agreement** = **5.0** (agreement_detail max delta = 0.5, on the
  `completeness` axis: min 4.5 / max 5.0)
- `findings_evidence_path`: `.mission-state/archive/iter-1-f2e47f22-reviews.json`
- `scoring_evidence_path`: `.mission-state/archive/iter-1-f2e47f22-scoring.json`

## Stop Decision

Gate (per `/mission` skill, restated from `~/.claude/skills/mission/refs/scoring-rubric.md`):
`composite_score >= threshold(4.0)` AND `min(items) >= 3.5` AND `open_high == 0`
AND `max_agreement_delta <= 1.5`.

- 4.83 ≥ 4.0 ✅
- 4.73 ≥ 3.5 ✅
- open_high 0 == 0 ✅
- max_agreement_delta 0.5 ≤ 1.5 ✅

All 4 conditions met on **iteration 1** → early-stop applies (no Medium findings,
no scope for a guaranteed 1-iteration improvement that would be required to
justify continuing per the early-stop rule). `mission-state.py mark-passes` was
run and returned `passes: true`, `loop_active: false`. Mission session id:
`cc-b354c73b-0081-44d4-b693-af44c3a97134`.

## Evidence

- Fixture evidence quoted inline in the confirmed-drift and rejected-candidates
  tables above is copied verbatim from the four fixtures read in this run:
  `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`,
  `.../impl-alpha.md`, `.../impl-beta.md`, `.../runbook.md`.
- `.mission-state/sessions/cc-b354c73b-0081-44d4-b693-af44c3a97134.json` and
  `.mission-state/sessions/cc-b354c73b-0081-44d4-b693-af44c3a97134-assumptions.md`
  hold the auditable mission state (init → planning → executing → reviewing →
  scored → passes) for this run. Score history is restated in `## Score` /
  `## Stop Decision` above.
- Nothing outside the four named fixtures and this artifact was read, grepped, or
  listed under `benchmarks/mission-vs-goal/`, per the task's isolation constraint.
- Unmeasured / explicitly out of scope: whether Alpha's deployment actually omits
  `health_check_interval_s` / `idle_timeout_s` config (only the excerpt shown in
  impl-alpha.md was read — the excerpt does not include those two keys, so no
  claim is made about Alpha's real runtime value for them, and no finding is
  recorded for them in either direction).

## Assumptions

- Full assumption registry lives in
  `.mission-state/sessions/cc-b354c73b-0081-44d4-b693-af44c3a97134-assumptions.md`
  (path resolved via `mission-state.py get assumptions_path`, not hardcoded) and
  covers: complexity
  classification (Complex, matching the harness-provided value), the strict
  fixture-only read scope, the no-network/no-commit/no-install benchmark
  constraints, and the reviewer-count/issue-ref decisions for this run.
- No implementation or runbook detail was inferred beyond what the four fixtures
  state; where a fixture excerpt is silent on a spec key (impl-alpha.md and
  `health_check_interval_s`/`idle_timeout_s`), that silence is reported as "no
  value to compare," not as a finding in either direction.
