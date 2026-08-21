# tail-config-spec-drift — mission arm (rep3)

## Mission

Audit configuration drift of two implementations and one runbook against the
canonical specification, and produce one auditable artifact that separates
confirmed drift from candidates that only look contradictory.

- Task id: `tail-config-spec-drift` (category: configuration)
- Arm: mission (profile full, complexity Complex, `--max-iter 3`)
- Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/config-spec-drift/spec.md`
  — "This table is the canonical contract. Implementations and runbooks must match it."
- Files read (exactly these four fixtures, nothing else under `benchmarks/mission-vs-goal/`):
  `spec.md`, `impl-alpha.md`, `impl-beta.md`, `runbook.md`.
- Deliverable: this file only.

Canonical values quoted from `spec.md`:

| Key | Spec value | Spec note |
|---|---|---|
| `request_timeout_ms` | 3000 | "Per-request upstream timeout." |
| `max_retries` | 3 | "Applies to idempotent requests only." |
| `retry_backoff` | exponential, base 250ms | "Jitter enabled." |
| `queue_max_depth` | 10000 | "Requests beyond depth are shed." |
| `tls_min_version` | 1.3 | "Hard floor for all listeners." |
| `health_check_interval_s` | 15 | "Liveness probe cadence." |
| `enable_legacy_auth` | false | "Must stay false; scheduled for removal." |
| `idle_timeout_s` | 90 | "Connection idle close." |
| `log_level` | info | "Production default." |
| `db_pool_size_per_replica` | 32 | "Two replicas run in production." |

## Plan

Adopted plan (mission-plan/1, generation 1, validated 2026-08-21T03:14:55Z,
`.mission-state/work/plan-iter1.json`):

1. `s1` — extract the ten canonical keys and values from `spec.md`.
2. `s2` — for each of the ten adjudication items, extract the observed value
   with an exact quote, and test every apparent mismatch against a conversion
   that the fixture itself states (unit or aggregate). Conversions invented by
   the auditor are not admissible.
3. `s3` — write this artifact with the eight required headings, a
   confirmed-drift table, a rejected-candidates section, an explicit statement
   of violated spec constraints, and exactly one machine-checkable findings
   table.
4. `s4` — verify mechanically: heading presence, findings-table header string,
   row count, and verdict vocabulary.

Decision rule used throughout: a mismatch is cleared **only** when the fixture
supplies the conversion factor (impl-beta.md: "the scheduler runs at 60 ticks
per second"; runbook.md: "the two replicas hold 64 pooled connections in
total"). Otherwise the mismatch stands as drift.

## Execution

Observed values, each quoted verbatim from the fixture line.

`impl-alpha.md` (block `# alpha/config/production.conf`):
- `requestTimeoutMs   = 27000`
- `maxRetries         = 3`
- `retryBackoff       = exponential` / `retryBackoffBaseMs = 250`
- `MAX_QUEUE_DEPTH    = 1250`
- `tlsMinVersion      = 1.3`
- `enableLegacyAuth   = true`
- `logLevel           = info`
- `dbPoolSizePerReplica = 32`
- Alpha states: "values above are read at boot; there is no runtime override
  layer in Alpha." No unit convention other than the key names is declared.

`impl-beta.md` (block `# beta/config/production.env`):
- `REQUEST_TIMEOUT_MS=3000`
- `RETRY_BACKOFF_STRATEGY=constant-interval` / `RETRY_BACKOFF_BASE_MS=250`
- `QUEUE_MAX_DEPTH=10000`
- `HEALTH_CHECK_INTERVAL_SECONDS=75`
- `ENABLE_LEGACY_AUTH=false`
- `IDLE_TIMEOUT_TICKS=5400`
- Beta states: "Beta counts idle time in scheduler ticks; the scheduler runs at
  60 ticks per second." and that strategy names follow the enum
  "(`constant-interval`, `exponential`, `decorrelated-jitter`)".

`runbook.md`:
- Retry guidance: "the gateway will retry idempotent requests up to 6 times
  before shedding."
- TLS: "set the load balancer TLS floor to 1.2 first so older internal probes
  keep passing during the rotation window".
- Logging: "Run all services at INFO verbosity in production. DEBUG is allowed
  only on a single canary replica for up to one hour."
- Database connections: "the two replicas hold 64 pooled connections in total."
- Health: "Liveness probes are configured centrally; see the spec for cadence."
  (defers to spec; asserts no competing value).

### Confirmed drift

| File | Key | Spec value | Actual value | Quoted evidence |
|---|---|---|---|---|
| impl-alpha.md | `request_timeout_ms` | 3000 | 27000 | `requestTimeoutMs   = 27000` (spec: "\| `request_timeout_ms` \| 3000 \|") |
| impl-alpha.md | `queue_max_depth` | 10000 | 1250 | `MAX_QUEUE_DEPTH    = 1250` (spec: "\| `queue_max_depth` \| 10000 \|") |
| impl-alpha.md | `enable_legacy_auth` | false | true | `enableLegacyAuth   = true` (spec: "Must stay false; scheduled for removal.") |
| impl-beta.md | `retry_backoff` | exponential, base 250ms | constant-interval, base 250ms | `RETRY_BACKOFF_STRATEGY=constant-interval` (spec: "exponential, base 250ms") |
| impl-beta.md | `health_check_interval_s` | 15 | 75 | `HEALTH_CHECK_INTERVAL_SECONDS=75` (spec: "\| `health_check_interval_s` \| 15 \|") |
| runbook.md | `max_retries` | 3 | 6 | "the gateway will retry idempotent requests up to 6 times before shedding." (spec: "\| `max_retries` \| 3 \| Applies to idempotent requests only.") |
| runbook.md | `tls_min_version` | 1.3 | 1.2 | "set the load balancer TLS floor to 1.2 first so older internal probes keep passing during the rotation window, then proceed with the rotation." (spec: "\| `tls_min_version` \| 1.3 \| Hard floor for all listeners.") |

Per-row rationale for the two rows that most resemble conversion cases:

- `impl-alpha.md / queue_max_depth`: 1250 x 8 = 10000 is arithmetically
  available (a bytes/bits reading), but nothing in `impl-alpha.md` or `spec.md`
  declares a bit- or byte-based unit for this key. The spec note is
  "Requests beyond depth are shed." — the quantity is a request count, which is
  dimensionless. An unstated factor-of-8 reinterpretation is not admissible, so
  this is drift, and Alpha sheds at 12.5% of the contracted depth.
- `impl-beta.md / health_check_interval_s`: Beta's tick convention exists, but
  it is scoped by its own sentence to idle time ("Beta counts **idle time** in
  scheduler ticks"), and the key itself is named
  `HEALTH_CHECK_INTERVAL_SECONDS`. Applying the tick factor anyway would give
  75 / 60 = 1.25 s, which also fails to equal 15. No conversion clears it.

### Rejected candidates

| File | Key | Apparent contradiction | Why it is not drift |
|---|---|---|---|
| impl-beta.md | `idle_timeout_s` | `IDLE_TIMEOUT_TICKS=5400` vs spec 90 | Unit conversion stated by the fixture: "the scheduler runs at 60 ticks per second" → 5400 ticks / 60 ticks per second = 90 s = spec value. Compliant. |
| runbook.md | `db_pool_size_per_replica` | "64 pooled connections in total" vs spec 32 | Aggregate conversion stated by both fixtures: spec note "Two replicas run in production"; runbook "the two replicas hold 64 pooled connections in total" → 64 / 2 replicas = 32 per replica = spec value. Compliant. |
| runbook.md | `log_level` | "INFO verbosity" vs spec `info` | Case-only difference of the same level name; the runbook prescribes it as the production setting ("Run all services at INFO verbosity in production"), matching the spec note "Production default." The DEBUG allowance is explicitly bounded ("only on a single canary replica for up to one hour") and is not a change of the production default. Compliant. |
| impl-alpha.md | `max_retries` (context only, not an adjudication item) | — | `maxRetries         = 3` equals the spec value; listed here only to show the runbook's 6 is not echoed by Alpha. |
| impl-beta.md | `request_timeout_ms`, `queue_max_depth`, `tls_min_version`, `enable_legacy_auth`, `log_level`, `db_pool_size_per_replica` (context only) | — | Literal equality with the spec (`REQUEST_TIMEOUT_MS=3000`, `QUEUE_MAX_DEPTH=10000`, `TLS_MIN_VERSION=1.3`, `ENABLE_LEGACY_AUTH=false`, `LOG_LEVEL=info`, `DB_POOL_SIZE_PER_REPLICA=32`). |

### Violated spec constraints (explicit)

1. **`request_timeout_ms` = 3000 ("Per-request upstream timeout")** — violated by
   `impl-alpha.md` (27000, 9x the contracted timeout).
2. **`queue_max_depth` = 10000 ("Requests beyond depth are shed")** — violated by
   `impl-alpha.md` (1250).
3. **`enable_legacy_auth` = false ("Must stay false; scheduled for removal")** —
   violated by `impl-alpha.md` (`true`). This is the hard-worded constraint in
   the spec and the only one whose note is phrased as an imperative.
4. **`retry_backoff` = "exponential, base 250ms" (jitter enabled)** — violated by
   `impl-beta.md`, which selects `constant-interval` from the library enum. The
   base (250 ms) complies; the strategy does not.
5. **`health_check_interval_s` = 15 ("Liveness probe cadence")** — violated by
   `impl-beta.md` (75 s, 5x slower detection).
6. **`max_retries` = 3 ("Applies to idempotent requests only")** — violated by
   `runbook.md`, which instructs operators to allow 6.
7. **`tls_min_version` = 1.3 ("Hard floor for all listeners")** — violated by
   `runbook.md`, which instructs lowering the load balancer floor to 1.2 during
   certificate rotation. "Hard floor for all listeners" admits no temporary
   exception, so the runbook procedure itself contradicts the spec.

Not violated: `idle_timeout_s`, `log_level`, `db_pool_size_per_replica` (see
rejected candidates).

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| impl-alpha.md | enable_legacy_auth | false | true | drift |
| impl-alpha.md | queue_max_depth | 10000 | 1250 | drift |
| impl-alpha.md | request_timeout_ms | 3000 | 27000 | drift |
| impl-beta.md | health_check_interval_s | 15 | 75 | drift |
| impl-beta.md | idle_timeout_s | 90 | 5400 ticks / 60 ticks-per-second = 90 | no-finding |
| impl-beta.md | retry_backoff | exponential, base 250ms | constant-interval, base 250ms | drift |
| runbook.md | db_pool_size_per_replica | 32 | 64 total / 2 replicas = 32 | no-finding |
| runbook.md | log_level | info | INFO | no-finding |
| runbook.md | max_retries | 3 | 6 | drift |
| runbook.md | tls_min_version | 1.3 | 1.2 | drift |

## Review

Reviewed against the task validator by two independent reviewers (mission
Phase 4, Complex → 2 reviewers, launched in parallel). Reviewer A (fact accuracy / evidence fidelity) independently re-derived all ten
verdicts from the fixtures and matched them (7 drift, 3 no-finding), raising two
Low findings about truncated runbook quotes. Reviewer B (contract conformance)
mechanically checked all eight structural requirements and passed them, raising
one Low finding about the `actual` column of the two cleared rows carrying the
conversion expression rather than the raw value. No High or Medium findings were
raised. The two Low quote-truncation findings were fixed after review by
restoring the full fixture sentences; the `actual`-column finding was
deliberately not changed, because the conversion form is what demonstrates
equivalence to `expected` on a `no-finding` row — this is a knowingly accepted
Low. Full reviewer documents:
`.mission-state/archive/iter-1-b02ef7d1-review-input-f5eb6317f8cad1cc.json` (A)
and `...-review-input-6c37153b8b5c27c0.json` (B).

Pre-review verification (`mission-state.py verification record --iteration 1`,
7 checks, all ok): required headings present; findings-table header appears
exactly once; 10 rows; the item set matches the mandated location/key strings
exactly; verdict vocabulary valid; conversion arithmetic recomputed
(5400/60 = 90.0, 64/2 = 32.0); and all nine quoted evidence strings found
verbatim in their fixtures.

Validator checklist, self-assessed before review:

- Confirmed-drift table with file / key / spec value / actual value / quoted
  evidence — present (7 rows).
- Rejected-candidates section with the conversion or reasoning that clears each
  one — present (3 adjudicated items cleared with arithmetic shown, plus
  context-only rows explicitly labelled as non-items).
- Explicit statement of which spec constraints are violated — present
  (7 numbered constraints, plus the three explicitly not violated).
- Exactly one machine-checkable findings table (under "Machine-checkable
  findings"), with the mandated five-column header, 10 rows — one per required
  item — and verdicts limited to `drift` / `no-finding` — present. The header
  string is deliberately not repeated anywhere else in this file so that the
  "exactly one table" requirement holds under a literal match.

## Score

Tool-computed by `mission-state.py review-finalize` (aggregate-reviews →
push-score) from the two imported `mission-review/1` documents, iteration 1:

| Gate value | Result |
|---|---|
| composite_score | 4.56 (threshold 4.0) |
| items | mission_achievement 4.5 / accuracy 4.5 / completeness 5.0 / usability 4.25 |
| min_item | 4.25 (gate: >= 3.5) |
| open_high | 0 |
| max_agreement_delta | 0.5 (usability; other axes 0.0) — gate: <= 1.5 |
| review_agreement | 5.0 |
| findings_evidence_path | `.mission-state/archive/iter-1-b02ef7d1-reviews-dcf3ab29f3103f47.json` |
| scoring_evidence_path | `.mission-state/archive/iter-1-b02ef7d1-scoring-a4b6284fec80b2b4.json` |
| artifact_digest_status | ok (sha256:56b4b1f2… over the reviewed revision) |
| revision_scope | git 068dc405…068dc405 (no commit was made; benchmark forbids committing) |

No value in this table was hand-computed; each is copied from
`score_history[0]` in the mission session state.

## Stop Decision

Stop when the mission gate passes: `findings_evidence_path` present,
`open_high == 0`, `max_agreement_delta <= 1.5`, `composite_score >= 4.0`,
`min(scored_items) >= 3.5`. `closeout` (mark-passes → next) is the sole
authority for that decision; `--max-iter 3` bounds the loop, and a failing gate
would trigger a critic pass and another iteration rather than a completion
claim.

Outcome: `closeout` returned `mark_passes.passes = true` (`forced: false`) and
`next_action: report-complete` with `loop_active: false` at iteration 1, so the
loop stopped after one scored review iteration. Two earlier `closeout` attempts
failed the gate (`mark-passes-gate-failed`, specialist selection checkpoint not
terminal) and are part of the audit trail; the checkpoint resolved to
`decision: unavailable` / `policy: fallback` because no external specialist
provider is installed and the benchmark forbids network access, so the core
loop ran without one.

## Evidence

- Fixtures read (only these, under `benchmarks/mission-vs-goal/`):
  `fixtures/tail/config-spec-drift/{spec.md,impl-alpha.md,impl-beta.md,runbook.md}`.
  No task definition, scoring configuration, or answer key was opened, listed,
  or grepped.
- Every confirmed finding above quotes the exact fixture line
  (e.g. `enableLegacyAuth   = true`, `HEALTH_CHECK_INTERVAL_SECONDS=75`,
  "retry idempotent requests up to 6 times").
- Mission state: `.mission-state/sessions/cc-004f1f45-0508-4fc2-82b2-5be4feca73d3.json`
  (mission id `b02ef7d1e7537739`); adopted plan
  `.mission-state/work/plan-iter1.json` (mission-plan/1, generation 1);
  reviewer documents and scoring JSON under `.mission-state/archive/`.
- Arithmetic shown for both cleared conversions: 5400 / 60 = 90 and 64 / 2 = 32.
- Unmeasured / out of scope, stated explicitly:
  - `impl-alpha.md` declares no `health_check_interval_s` and no
    `idle_timeout_s` key; whether Alpha's built-in defaults satisfy the spec is
    **unmeasured** (the fixture does not show them). This absence is reported as
    an observation, not as a findings row, because those two items are only
    adjudicated for `impl-beta.md` in this task.
  - `impl-beta.md` does declare `MAX_RETRIES=3`, which matches the spec; no key
    present in either implementation excerpt was left unexamined.
  - Jitter ("Jitter enabled") is not represented by any key in either
    implementation excerpt; its runtime state is **unmeasured**.
  - Runtime/actual behaviour was not executed or observed; this audit is a
    static comparison of the four documents only.
- No commits, pushes, package installs, or network access were performed. No
  claim of benchmark superiority is made or implied by this artifact.

## Assumptions

1. `spec.md` is the sole source of truth for all ten keys; implementation and
   runbook wording never overrides it. Basis: "This table is the canonical
   contract. Implementations and runbooks must match it."
2. A conversion clears a mismatch only when the fixture states the factor.
   Beta's tick rate and the runbook's two-replica aggregate qualify; a
   hypothetical bits-to-bytes factor for `queue_max_depth` does not.
3. Beta's tick convention is scoped to idle time, per its own sentence, and is
   not applied to a key explicitly named `..._SECONDS`.
4. Case differences in enumerated level names (`INFO` vs `info`) are not drift;
   only the level itself matters. If the scoring convention treated case as
   significant, `runbook.md / log_level` would flip to `drift` — flagged here as
   the single most judgment-dependent row.
5. The runbook's TLS instruction is treated as a contradiction of the spec even
   though it is temporary, because the spec calls 1.3 a "Hard floor for all
   listeners" with no exception clause.
6. Where this artifact reports mission gate values, they are whatever the
   `mission-state.py` tooling computed; no gate value was hand-edited.

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-21 | 初版作成（mission arm rep3、iteration 1） |
| 2026-08-21 | レビュー反映（runbook 引用の verbatim 化）、Score/Review/Stop Decision に tool 計測値を記載 |
