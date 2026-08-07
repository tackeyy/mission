# portfolio-std-contract — mission arm, rep1

- Task id: `portfolio-std-contract` / Category: api-contract / Arm: mission / Complexity: Standard
- Date: 2026-08-07
- Mission session: `cc-a8bc9945-acd9-4e64-a8c4-0ee1fc0d5e7d` (mission_id `31ffb9527b9cdac6`)

## Mission

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift, reject spec-permitted differences as non-findings citing the permitting clause, and record the result in this artifact with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. Complete at least one scored review iteration through the mission state gate (`review-finalize` → `closeout`).

## Plan

Inline bounded plan (Standard iteration 1, plan-inline per `next`):

| # | Step | Depends on | Done when |
|---|---|---|---|
| 1 | Read exactly the two fixtures: `api-spec.md`, `client-py.md` | — | Both files read in full |
| 2 | Enumerate every client behavior and classify against the spec: breaking drift vs. spec-permitted | 1 | Every bullet in `client-py.md` classified |
| 3 | Write artifact with drift table, breaking-drift evidence (verbatim quotes), rejected-candidates with permitting clauses | 2 | Validator headings present |
| 4 | Run 2 parallel reviewers → `review-finalize --iteration 1 --min-reviewers 2` → `closeout` | 3 | `closeout` exit 0, `passes=true` or halt recorded |

Completion condition: artifact written and validator-complete; mission gate result recorded in Score / Stop Decision.

## Execution

Both fixtures were read in full (no other files under `benchmarks/mission-vs-goal/` were opened). Every statement in `client-py.md` (6 bullets) was classified. Executor work was performed inline by the orchestrator (recorded as inline invocation; see Assumptions).

### Drift table

| # | Client behavior (client-py.md) | Spec requirement (api-spec.md) | Classification |
|---|---|---|---|
| 1 | POST /v2/transfers "fires the request without an `Idempotency-Key` header" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **Breaking drift** |
| 2 | Status mapping uses American spelling `canceled`, "matches on exact string equality against the wire value" | Enum is `pending`, `settled`, `cancelled`, `failed`; "The `status` enum uses British spelling `cancelled`." | **Breaking drift** |
| 3 | "Sends an `X-Trace-Id` header on every request" | Extension clause (section 7): extension `X-*` headers permitted | Rejected (spec-permitted) |
| 4 | "Sends the `X-Sig` header exactly as specified." | `X-Sig` header required; casing case-insensitive per RFC 9110 | Rejected (compliant) |
| 5 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Rejected (compliant) |
| 6 | "Parses `expires_at` as epoch milliseconds." | `expires_at` is "epoch_ms (milliseconds since epoch, UTC)" | Rejected (compliant) |

### Breaking drifts (with quoted evidence)

**B1 — Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Spec (source of truth): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (api-spec.md, POST /v2/transfers)
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." (client-py.md)
- Impact: every single-transfer POST violates a REQUIRED header contract. Breaking.

**B2 — Status enum spelling mismatch: `canceled` (client) vs `cancelled` (spec) under exact string matching.**
- Spec: `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`." (api-spec.md, GET /v2/transfers/{id})
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." (client-py.md)
- Impact: the wire value `cancelled` never matches the client's `canceled` entry under exact string equality, so cancelled transfers cannot be mapped. Breaking.

### Rejected candidates (non-findings, with permitting clause)

**R1 — `X-Trace-Id` header.** Client: "Sends an `X-Trace-Id` header on every request for distributed tracing." Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."

**R2 — `X-Sig` header handling.** Client: "Sends the `X-Sig` header exactly as specified." Matches the Authentication requirement "Every request MUST carry the `X-Sig` header". No drift; additionally "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing", so casing differences would also be non-findings.

**R3 — Never retrying POSTs.** Client: "Never retries POSTs." The spec only forbids retries *without* the key: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies this. (Note: the missing header itself is B1; the retry *behavior* is compliant.)

**R4 — `expires_at` unit.** Client: "Parses `expires_at` as epoch milliseconds." Spec: `expires_at` is "epoch_ms (milliseconds since epoch, UTC)". Exact match; the seconds-vs-ms trap does not apply.

## Review

Iteration 1: 2 independent reviewers (perspectives A=accuracy/evidence, B=completeness/validator) spawned in parallel in a single message (window A=2026-08-07T03:12:48Z..03:14:36Z, B=03:12:48Z..03:14:46Z). `mission-review/1` JSONs saved verbatim (`.mission-state/reviews/iter1-A.json`, `iter1-B.json`) and aggregated via `review-finalize --iteration 1 --min-reviewers 2`. Findings: 4 total, all Low severity, 0 High, 0 Medium (A-1/A-2: minor quote-verbatimness notes; B-1: Score/Stop-Decision placeholders pending gate — resolved by this update; B-2: R2 is "no difference" rather than a permitted difference — noted, R2 kept for exhaustiveness).

## Score

Tool-computed by `review-finalize` (archive: `.mission-state/archive/iter-1-31ffb952-scoring.json`):

| Item | Value |
|---|---|
| mission_achievement | 4.5 |
| accuracy | 4.75 |
| completeness | 4.5 |
| usability | 4.75 |
| composite (review_agreement basis) | ≥ 4.5 (all items ≥ 4.5, threshold 4.0) |
| max_agreement_delta | 1.0 (completeness), ≤ 1.5 gate |
| open_high | 0 |
| findings_evidence_path | `.mission-state/archive/iter-1-31ffb952-reviews.json` |

## Stop Decision

`closeout` exit 0 at iteration 1: `mark-passes` → `passes: true` (not forced), `next_action: report-complete`, `loop_active: false`. Early-stop is the standard pass path: threshold reached, `open_high == 0`, no Medium findings, iteration 1 of max 2. Stop.

## Evidence

- Fixtures read: `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`, `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — the only files under `benchmarks/mission-vs-goal/` accessed besides this artifact.
- All quotes above are verbatim from those fixtures.
- Mission state: `.mission-state/sessions/cc-a8bc9945-acd9-4e64-a8c4-0ee1fc0d5e7d.json`; review JSONs and scoring output under `.mission-state/` (paths listed post-finalize).
- Unmeasured: runtime behavior of client-py (fixtures are prose descriptions, not executable code); server-side enforcement behavior. No claims are made about them.

## Assumptions

- `MISSION_PLUGIN_ROOT` points to a local authoring checkout, but the benchmark forbids network access, so `mission-local-authoring-sync.sh` was not run; the repository-root `scripts/mission-state.py` (this checkout's own CLI) was used as the state authority. No fallback to stale distributed copies occurred — the repo checkout is the source.
- Executor was applied inline by the orchestrator (analysis-only task over two short prose fixtures already read in context); recorded here as an inline invocation instead of a spawned `mission-executor` subagent to keep the run within the benchmark budget. Reviewer gate (2 independent subagents) was NOT inlined.
- "Breaking drift" = client behavior that violates a MUST/REQUIRED clause or produces wrong results against spec-conformant wire data. Prose fixtures are treated as accurate descriptions of the implementation.
- Benchmark metadata (task definitions, scoring config, answer keys) was not read, per run rules.
