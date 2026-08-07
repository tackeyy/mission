# portfolio-std-contract — mission arm (rep3)

- Task id: `portfolio-std-contract` / Category: `api-contract` / Arm: `mission` / Profile: `full`
- Date: 2026-08-07

## Mission

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift, reject spec-permitted differences as non-findings citing the permitting clause, and record the result in this artifact with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. Complexity: Standard. Mission state: `.mission-state/sessions/cc-217ca788-4a78-422f-82e5-d0d5d12ddea7.json` (mission_id `dafd411891c3b02d`).

## Plan

Inline bounded plan (iteration 1, Standard → `plan-inline` per mission-state `next`; no planner subagent):

| # | Step | Depends on | Done criteria |
|---|---|---|---|
| 1 | Read the two named fixtures only (`api-spec.md`, `client-py.md`) | — | Both files read in full; no other benchmark files touched |
| 2 | Enumerate every client behavior and classify against the spec: breaking drift vs spec-permitted | 1 | Every client-py bullet accounted for in the drift table |
| 3 | Write artifact: drift table + breaking-drift section (verbatim quotes) + rejected-candidates section (permitting clause cited) | 2 | Validator headings present; every claim quotes exact identifiers |
| 4 | Scored review iteration: 2 reviewers in parallel → `review-finalize` → `closeout` | 3 | `passes: true` or documented halt; gate values recorded in Score |

Completion condition: artifact written at the required path, validator sections present, and the mission gate (`composite >= 4.0`, `open_high == 0`, agreement delta `<= 1.5`) evaluated via `mission-state.py`.

## Execution

Read both fixtures in full (step 1). Classified all six client-py behaviors (step 2). Results below (step 3).

### Drift table

| # | Client-py behavior | Spec requirement | Classification |
|---|---|---|---|
| 1 | POST /v2/transfers sent "without an `Idempotency-Key` header" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request" | **Breaking drift (B1)** |
| 2 | Status mapping uses American spelling `canceled`, "exact string equality against the wire value" | Enum is `pending`, `settled`, `cancelled`, `failed`; "The `status` enum uses British spelling `cancelled`" | **Breaking drift (B2)** |
| 3 | "Sends the `X-Sig` header exactly as specified" | "Every request MUST carry the `X-Sig` header" | Compliant (rejected candidate R1) |
| 4 | "Sends an `X-Trace-Id` header on every request" | Extension clause (section 7) permits additional `X-*` headers | Spec-permitted (rejected candidate R2) |
| 5 | "Parses `expires_at` as epoch milliseconds" | `expires_at` is "integer / epoch_ms (milliseconds since epoch, UTC)" | Compliant (rejected candidate R3) |
| 6 | "Never retries POSTs" | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Compliant (rejected candidate R4) |

### Breaking drifts (confirmed findings)

**B1 — Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Spec (`api-spec.md`, POST /v2/transfers): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (`client-py.md`): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the spec makes the header unconditionally REQUIRED on every request, not only on retries. Every single-transfer POST from client-py violates a MUST-level requirement. The bulk-endpoint-only wrapper does not satisfy it for this endpoint.

**B2 — Status enum spelling mismatch: `canceled` (client) vs `cancelled` (wire).**
- Spec (`api-spec.md`, GET /v2/transfers/{id}): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Client (`client-py.md`): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: the wire value `cancelled` will never equal the client's `canceled` under exact string equality, so every cancelled transfer fails to map. The other three values (`pending`, `settled`, `failed`) are spelled identically and are unaffected.

### Rejected candidates (non-findings, with permitting clause)

**R1 — `X-Sig` header.** Client "Sends the `X-Sig` header exactly as specified"; spec Authentication section requires "the `X-Sig` header containing an HMAC of the body". No difference exists, so no drift. (Additionally, casing could never be a drift: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing.")

**R2 — `X-Trace-Id` extension header.** Client "Sends an `X-Trace-Id` header on every request for distributed tracing." Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` matches the `X-*` pattern and is exactly the tracing example the clause anticipates.

**R3 — `expires_at` parsed as epoch milliseconds.** Client "Parses `expires_at` as epoch milliseconds"; spec defines `expires_at` as "epoch_ms (milliseconds since epoch, UTC)". The client matches the spec; the seconds-vs-milliseconds trap ("treating it as seconds shifts expiry by three orders of magnitude") does not apply.

**R4 — "Never retries POSTs."** The spec's retry rule is a conditional prohibition: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies the MUST NOT trivially; there is no clause requiring retries. The missing header itself is already counted as B1 and is not double-counted here.

## Review

Iteration 1 scored review per mission contract: 2 independent reviewers (Standard complexity) spawned in parallel in a single message — perspective A (accuracy/evidence verification) and perspective B (completeness/validator conformance). Reviewer outputs saved verbatim as `mission-review/1` JSON at `.mission-state/review-iter1-A.json` and `.mission-state/review-iter1-B.json`, aggregated via `mission-state.py review-finalize --iteration 1 --min-reviewers 2` with `--reviewer-window` timestamps (`parallel_execution: true` confirmed by the aggregator). Reviewer findings: A-1 (Low, accuracy): drift-table row 5 joined the spec table's Type and Semantics cells with a `/` not present in the fixture; B-1 (Low, usability): drift-table Classification labels mix "Compliant" and "Spec-permitted" under the same "rejected candidate" wording. 0 High / 0 Medium — no inline fixes required (M6 not triggered); Low findings left as recorded residuals per absolute-scoring rules.

## Score

Gate values (tool-computed by `mission-state.py review-finalize`, iteration 1, timestamp 2026-08-07T06:58:48Z):

- composite_score: **4.84** (threshold 4.0) — pass
- min(scored_items): 4.5 (gate ≥ 3.5) — pass
- max_agreement_delta: 1.0 (accuracy axis, A=4.0 vs B=5.0; gate ≤ 1.5) — pass
- open_high: 0 — pass
- findings evidence: `.mission-state/archive/iter-1-dafd4118-reviews.json` — present
- Aggregated items: mission_achievement 5.0, accuracy 4.5, completeness 5.0, usability 4.85; review_agreement 4.0; parallel_execution: true
- Scoring archive: `.mission-state/archive/iter-1-dafd4118-scoring.json`

## Stop Decision

Early-stop at iteration 1 (max-iter 2): threshold reached (4.84 ≥ 4.0) and `open_high == 0`, so the pass condition holds and the continuation criteria (composite in 4.0–4.3 band AND ≥3 Medium findings) are not met — actual composite 4.84 with 0 Medium findings. `mission-state.py closeout` result recorded below in Evidence. Loop stopped after 1 scored iteration.

## Evidence

- Fixtures read (the only benchmark files opened): `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`, `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`.
- All quoted strings in the drift table, B1/B2, and R1–R4 are verbatim from those two fixtures (exact header names `X-Sig`, `Idempotency-Key`, `X-Trace-Id`; field names `id`, `status`, `expires_at`; enum values `pending`, `settled`, `cancelled`, `failed` vs client `canceled`).
- Mission state: `.mission-state/sessions/cc-217ca788-4a78-422f-82e5-d0d5d12ddea7.json` (mission_id `dafd411891c3b02d`); reviewer raw JSON at `.mission-state/review-iter1-A.json` / `.mission-state/review-iter1-B.json`; scoring JSON at `.mission-state/scorer-iter1.json`.
- Routing: `init` did NOT route to goal (Standard complexity → full mission loop ran with plan-inline per #339).
- Closeout: first `closeout` failed with exit 2 (specialist selection checkpoint missing); recorded `specialists recommend --record-state` (task_profile.primary=`backend`, decision policy=`fallback`, action=`continue-core`, reason: preset `backend-provider` not installed — no external specialist used, degraded to core). Second `closeout` returned exit 0: `mark_passes.passes=true` (not forced), `next_action=report-complete`, `loop_active=false`, phase `done`, iteration 1.
- Unmeasured: wall-clock duration and token cost of this run are not measured by this artifact; no runtime behavior of any real client was executed (the audit is documentation-based on the two fixtures).

## Assumptions

- The two fixture files are complete and authoritative; no other spec sections or client behaviors exist beyond what they state.
- `client-py.md` prose accurately describes the implementation (no code was available to inspect).
- "Breaking drift" = a client behavior that violates a spec MUST/REQUIRED clause or mis-handles a spec-defined value in a way that changes observable behavior; spec-permitted differences (MAY clauses) are non-findings by definition of the task.
- B2 counts as one drift (the single mismatched enum value `canceled`); the three matching enum values are not findings.
- Benchmark metadata (task definitions, scoring config, answer keys) was not read, per the run rules.
