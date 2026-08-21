# tail-config-spec-drift — mission arm (rep2)

## Mission

Audit configuration drift of `impl-alpha.md`, `impl-beta.md`, and `runbook.md` against the
canonical spec (`spec.md`), adjudicating the 10 required items, quoting the exact
identifier and value from each fixture, and separating confirmed drift from
candidates that are cleared by a documented unit or aggregate conversion.

Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`, which
states: "This table is the canonical contract. Implementations and runbooks must match it."

Scope: read only the four named fixtures; write only this artifact (plus `.mission-state/`).
No commits, pushes, installs, or network access. No benchmark metadata (task definitions,
scoring configuration, answer keys) was opened.

## Plan

Adopted plan (`mission-plan/1`, generation 1, validated 2026-08-21T02:53:30Z):

| step | action | acceptance |
|---|---|---|
| s1 | read the 4 named fixtures only | all four read; no other benchmark path opened |
| s2 | adjudicate the 10 required items against the spec table | every item has verdict `drift` or `no-finding` with both values quoted |
| s3 | write the artifact with the required headings and one findings table | 8 headings present; exactly one machine-checkable table; 10 rows |
| s4 | verify: re-grep every quoted string; recompute every conversion | all quotes found verbatim; conversions recomputed numerically |
| s5 | 2 parallel reviewers → review-import → review-finalize → closeout | finalize with `--min-reviewers 2`; closeout exit 0 |

Adjudication rule fixed in advance (assumption a2): a mismatch is cleared **only** when the
fixture itself documents the conversion basis. A numeric ratio that happens to be tidy but has
no stated basis in the fixture is treated as drift, not as an implied conversion.

## Execution

Canonical values extracted from `spec.md`:

| Key | Spec value |
|---|---|
| `request_timeout_ms` | 3000 |
| `max_retries` | 3 |
| `retry_backoff` | exponential, base 250ms |
| `queue_max_depth` | 10000 |
| `tls_min_version` | 1.3 |
| `health_check_interval_s` | 15 |
| `enable_legacy_auth` | false ("Must stay false; scheduled for removal.") |
| `idle_timeout_s` | 90 |
| `log_level` | info |
| `db_pool_size_per_replica` | 32 ("Two replicas run in production.") |

### Confirmed drift

| file | key | spec value | actual value | quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | 3000 | 27000 | spec.md row: `request_timeout_ms` = 3000 ("Per-request upstream timeout."); impl-alpha.md: `requestTimeoutMs   = 27000` |
| impl-alpha.md | `queue_max_depth` | 10000 | 1250 | spec.md row: `queue_max_depth` = 10000 ("Requests beyond depth are shed."); impl-alpha.md: `MAX_QUEUE_DEPTH    = 1250` |
| impl-alpha.md | `enable_legacy_auth` | false | true | spec.md row: `enable_legacy_auth` = false ("Must stay false; scheduled for removal."); impl-alpha.md: `enableLegacyAuth   = true` |
| impl-beta.md | `health_check_interval_s` | 15 | 75 | spec.md row: `health_check_interval_s` = 15 ("Liveness probe cadence."); impl-beta.md: `HEALTH_CHECK_INTERVAL_SECONDS=75` |
| impl-beta.md | `retry_backoff` | exponential, base 250ms | constant-interval (base 250ms) | spec.md row: `retry_backoff` = "exponential, base 250ms" ("Jitter enabled."); impl-beta.md: `RETRY_BACKOFF_STRATEGY=constant-interval` with `RETRY_BACKOFF_BASE_MS=250` |
| runbook.md | `max_retries` | 3 | 6 | spec.md row: `max_retries` = 3 ("Applies to idempotent requests only."); runbook.md: "the gateway will retry idempotent requests up to 6 times before shedding" |
| runbook.md | `tls_min_version` | 1.3 | 1.2 | spec.md row: `tls_min_version` = 1.3 ("Hard floor for all listeners."); runbook.md: "set the load balancer TLS floor to 1.2 first" |

Per-finding notes:

- **impl-alpha `request_timeout_ms` = 27000.** Both sides are already in the same unit
  (milliseconds, per the key name in both spec and `requestTimeoutMs`). 27000 ms is 9× the
  spec's 3000 ms; no fixture text offers any per-attempt/aggregate relationship that would
  make 27000 equivalent to 3000. Alpha explicitly rules out a later correction:
  "values above are read at boot; there is no runtime override layer in Alpha."
- **impl-alpha `queue_max_depth` = 1250.** 10000 / 1250 = 8, which is superficially tempting
  as a per-shard split, but **impl-alpha.md contains no shard, partition, or per-instance
  statement at all** — the only deployment note is the no-runtime-override sentence and the
  legacy-auth history. With no documented basis, this is drift, not a conversion.
- **impl-alpha `enable_legacy_auth` = true.** Directly negates the spec's "Must stay false".
  Alpha itself records the cause: "The legacy auth flag was toggled during the March incident
  bridge and has not been revisited since."
- **impl-beta `health_check_interval_s` = 75.** Beta's key is explicitly in seconds
  (`..._INTERVAL_SECONDS`), the same unit as the spec's `health_check_interval_s`. Beta's tick
  conversion (60 ticks/s) is documented only for idle time ("Beta counts idle time in scheduler
  ticks"); applying it here is not supported by the fixture, and 75 ticks would be 1.25 s, which
  matches neither reading. Drift under either interpretation.
- **impl-beta `retry_backoff` = constant-interval.** The base matches
  (`RETRY_BACKOFF_BASE_MS=250` vs spec "base 250ms"), so only the strategy drifts. Beta confirms
  `exponential` is an available value of the same enum: "Backoff strategy names follow the retry
  library's enum (`constant-interval`, `exponential`, `decorrelated-jitter`)" — so this is a real
  strategy divergence, not a naming difference. Unmeasured: the spec's "Jitter enabled." note has
  no counterpart key in beta's excerpt (nor in alpha's), so jitter state is not evaluated for either implementation.
- **runbook.md `max_retries` = 6.** Same population as the spec ("Applies to idempotent requests
  only." vs "retry idempotent requests up to 6 times"), so the two are directly comparable; 6 > 3.
- **runbook.md `tls_min_version` = 1.2.** The spec calls 1.3 a "Hard floor for all listeners",
  which admits no rotation-window exception; the runbook instructs operators to set the floor to
  1.2 for the rotation window.

### Rejected candidates

Three items looked contradictory on their face but are consistent once the conversion documented
inside the fixture is applied. Each is reported as `no-finding`.

1. **impl-beta.md / `idle_timeout_s` — `IDLE_TIMEOUT_TICKS=5400` vs spec 90.**
   Looked suspicious because 5400 is 60× the spec value and appears in a config block whose other
   values are plain seconds. Cleared by the conversion stated in the same fixture: "Beta counts
   idle time in scheduler ticks; the scheduler runs at 60 ticks per second."
   Conversion: 5400 ticks ÷ 60 ticks/s = **90 s** = spec `idle_timeout_s` 90. Compliant.

2. **runbook.md / `db_pool_size_per_replica` — "64 pooled connections in total" vs spec 32.**
   Looked suspicious because 64 ≠ 32 for a key whose name ends in `_per_replica`. Cleared by
   aggregate conversion: the spec note says "Two replicas run in production." and the runbook
   states "the two replicas hold 64 pooled connections in total."
   Conversion: 64 total ÷ 2 replicas = **32 per replica** = spec 32. The runbook is quoting the
   aggregate, not a per-replica figure, and says so ("Alert thresholds are derived from that
   aggregate figure."). Compliant.

3. **runbook.md / `log_level` — "INFO verbosity" (plus a DEBUG allowance) vs spec `info`.**
   Looked suspicious on two counts: case (`INFO` vs `info`) and the DEBUG carve-out. Cleared by
   reasoning rather than arithmetic: `INFO` and `info` are the same level differing only in
   letter case, and the spec qualifies its value as the "Production default", which a bounded,
   explicitly scoped exception does not contradict — the runbook keeps the fleet default at INFO
   ("Run all services at INFO verbosity in production") and limits the exception to "a single
   canary replica for up to one hour." Unmeasured: whether any replica is actually running at
   DEBUG today is not observable from these fixtures, so this clearance covers the written
   guidance only, not runtime state. Note the asymmetry with assumption 2: candidates 1 and 2 are
   cleared by an arithmetic conversion documented inside the fixture, whereas this one is cleared
   by a semantic reading of the spec's own qualifier "Production default" plus case tolerance — a
   weaker basis, stated here so the difference is visible rather than hidden.

Also examined and found compliant while extracting values (not among the 10 required items, so
not given findings rows): `impl-alpha.md` `maxRetries = 3`, `retryBackoff = exponential` +
`retryBackoffBaseMs = 250`, `tlsMinVersion = 1.3`, `logLevel = info`,
`dbPoolSizePerReplica = 32`; `impl-beta.md` `REQUEST_TIMEOUT_MS=3000`, `MAX_RETRIES=3`,
`QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `ENABLE_LEGACY_AUTH=false`, `LOG_LEVEL=info`,
`DB_POOL_SIZE_PER_REPLICA=32`. `impl-alpha.md` has no `health_check_interval_s` or
`idle_timeout_s` entry in the excerpt (absent, not contradictory — unmeasured).

### Violated spec constraints

The confirmed drift violates these constraints of the canonical contract:

1. `request_timeout_ms` must be 3000 — violated by impl-alpha (27000).
2. `queue_max_depth` must be 10000 — violated by impl-alpha (1250); the shed threshold
   ("Requests beyond depth are shed.") therefore triggers at 12.5% of the contracted depth.
3. `enable_legacy_auth` must be false, and the spec's note "Must stay false; scheduled for
   removal." makes this an explicit invariant rather than a default — violated by impl-alpha (true).
4. `health_check_interval_s` must be 15 — violated by impl-beta (75).
5. `retry_backoff` must be "exponential, base 250ms" — violated by impl-beta on the strategy
   component (`constant-interval`); the base component is compliant.
6. `max_retries` must be 3 for idempotent requests — violated by the runbook's operational
   guidance (6).
7. `tls_min_version` must be 1.3 as a "Hard floor for all listeners" — violated by the runbook's
   instruction to set the floor to 1.2 during certificate rotation.

Not violated: `idle_timeout_s` (90 s via ticks), `db_pool_size_per_replica` (32 per replica via
the two-replica aggregate), `log_level` (info/INFO with a bounded canary exception).

## Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | idle_timeout_s | 90 | `IDLE_TIMEOUT_TICKS=5400` (5400 ticks / 60 ticks per s = 90 s) | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval, base 250ms | drift |
| runbook.md | db_pool_size_per_replica | 32 | "64 pooled connections in total" (64 / 2 replicas = 32 per replica) | no-finding |
| runbook.md | log_level | info | INFO (DEBUG only on one canary, <= 1h) | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |

## Review

Two independent reviewers were run in parallel in a single message (perspectives: evidence
fidelity / adjudication correctness, and validator conformance / completeness). Their
`mission-review/1` payloads were imported with `review-import` and aggregated with
`review-finalize --min-reviewers 2`; raw review JSON and scoring JSON are stored under
`.mission-state/archive/` and are not transcribed here (output-compression discipline).

Reviewer-raised points and disposition:

- Both reviewers independently confirmed the three rejections and the seven drift rows against
  the fixture text; no reviewer proposed moving a row across the drift/no-finding boundary.
- Reviewer note on `queue_max_depth`: the 8× ratio must be argued as *undocumented*, not merely
  as "different" — addressed in the per-finding note by citing the absence of any shard statement
  in impl-alpha.md.
- Reviewer note on `health_check_interval_s`: the tick conversion should be shown as failing too,
  not just as inapplicable — addressed (75 ticks = 1.25 s, matching neither reading).
- Reviewer note on `log_level`: the case-only difference and the DEBUG carve-out should both be
  cleared explicitly — addressed in rejected candidate 3, with the runtime-state limitation
  marked unmeasured.

## Score

Gate values are tool-computed and read back from mission state after `review-finalize`
(no hand-computed pass judgment):

| gate input | value |
|---|---|
| composite_score | **4.75** (threshold 4.0) — `.mission-state/archive/iter-1-41bee2e2-scoring-f2a17226ca7d802f.json` |
| open_high | 0 (both reviewers raised Low-severity findings only) |
| max_agreement_delta | <= 1.5 (enforced by `review-finalize`) |
| findings_evidence_path | recorded by `review-finalize` |
| min(scored_items) | 4.0 (accuracy); mission_achievement 5.0 / completeness 5.0 / usability 5.0 |
| reviewers | 2 (Complex, no irreversible/security signal) |

Independent verification recorded via `mission-state.py verification record --iteration 1`:
every quoted evidence string was re-grepped verbatim in its source fixture, both conversions were
recomputed numerically (5400/60 = 90; 64/2 = 32), the findings table was counted (10 rows, one
per required item, using the mandated `location`/`key` strings), and the eight required headings
were confirmed present. Results of those checks are in the state file.

## Stop Decision

Actual stop: `mark-halt --category awaiting-approval` on iteration 1. The scored review
iteration completed (`review-import` x2 -> `review-finalize --min-reviewers 2`, composite 4.75,
min item 4.0, open_high 0, no High/Medium findings), and all four Low findings were applied to
this artifact. `closeout`/`mark-passes` nevertheless refused to set `passes: true`: the
specialist selection checkpoint recorded by `specialists recommend --record-state` is in
`lifecycle_state: candidate` (action `ask-user`) for an external `oracle` provider that is
unavailable in this offline, no-network benchmark run, and clearing it requires user consent
written outside the allowed edit scope. That gate is therefore reported, not bypassed —
`mark-passes --force` was not used. `passes: true` is NOT claimed.

Intended stop condition was `closeout` exit 0 (`mark-passes` → `next` returning `report-complete`), i.e. the gated
loop passed on iteration 1 of a maximum of 3. If any gate had failed, the loop would have
continued with the critic into iteration 2 rather than reporting completion.

No benchmark-superiority claim is made here; this artifact only completes the
`tail-config-spec-drift` task for the mission arm.

## Evidence

Fixtures read (exactly these four, all under
`benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/`):

- `spec.md` — canonical table, lines 5–16; e.g. the row `tls_min_version` = 1.3 with note "Hard floor for all listeners."
- `impl-alpha.md` — config block lines 4–13; e.g. `requestTimeoutMs   = 27000`,
  `MAX_QUEUE_DEPTH    = 1250`, `enableLegacyAuth   = true`; note lines 16–18
  ("there is no runtime override layer in Alpha").
- `impl-beta.md` — config block lines 4–15; e.g. `HEALTH_CHECK_INTERVAL_SECONDS=75`,
  `RETRY_BACKOFF_STRATEGY=constant-interval`, `IDLE_TIMEOUT_TICKS=5400`; note lines 18–20
  ("the scheduler runs at 60 ticks per second").
- `runbook.md` — "retry idempotent requests up to 6 times before shedding" (Retry guidance),
  "set the load balancer TLS floor to 1.2 first" (TLS), "Run all services at INFO verbosity in
  production. DEBUG is allowed only on a single canary replica for up to one hour." (Logging),
  "the two replicas hold 64 pooled connections in total" (Database connections).

Mission-state evidence (auditable trail, this repository):

- `.mission-state/sessions/cc-4e566cb4-541b-4eb3-9458-f8393bdc6eb1.json` — phases, activity
  segments, plan generation 1 (validated 2026-08-21T02:53:30Z), verification record, review
  aggregate, scoring, gate outcome.
- `.mission-state/archive/` — imported `mission-review/1` payloads and scoring JSON.

Arithmetic evidence:

- `5400 ticks ÷ 60 ticks/s = 90 s` (impl-beta idle timeout) — matches spec 90.
- `64 connections ÷ 2 replicas = 32` (runbook DB pool) — matches spec 32.
- `27000 ms ÷ 3000 ms = 9×` (impl-alpha timeout) — no documented divisor; drift.
- `10000 ÷ 1250 = 8×` (impl-alpha queue depth) — no shard/partition statement anywhere in
  impl-alpha.md; drift.
- `75 s vs 15 s = 5×`, and `75 ticks ÷ 60 = 1.25 s` — neither reading reaches 15; drift.

Not measured: runtime behaviour of any service (no code executed, no deployment inspected); the
jitter setting in impl-alpha or impl-beta (no corresponding key in either excerpt); `health_check_interval_s` and
`idle_timeout_s` in impl-alpha (absent from the excerpt); whether any canary replica is currently
at DEBUG.

## Assumptions

1. `spec.md` is the sole source of truth for all 10 keys — supported by its own line 3: "This
   table is the canonical contract. Implementations and runbooks must match it."
2. A mismatch is cleared only when the conversion basis is documented **inside the fixture**
   (impl-beta's 60 ticks/s; the runbook's two-replica aggregate, corroborated by the spec note
   "Two replicas run in production."). Tidy ratios without a stated basis (alpha's 8×, 9×) are
   reported as drift. If a shard-count document existed outside these fixtures, the
   `queue_max_depth` row could change — such a document was neither provided nor read.
3. Key identity across naming conventions is by normalised name (`requestTimeoutMs` /
   `REQUEST_TIMEOUT_MS` ≡ `request_timeout_ms`); casing and separator style are not treated as
   drift, and the same tolerance is applied to the value `INFO` vs `info`.
4. The runbook is in scope as an artefact that "must match" the spec, so operational guidance
   contradicting a spec value is reported as drift even though it is prose rather than config.
5. Fixture excerpts are treated as complete for the keys they contain; a key absent from an
   excerpt is reported as unmeasured, never as compliant.

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-21 | 初版作成（mission arm rep2、iteration 1） |
