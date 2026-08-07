# portfolio-std-contract — mission arm — rep1

## Mission

Audit the client implementation `client-py` against the API specification (source of truth). Read exactly two fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

Find every breaking drift in client-py, and reject spec-permitted differences as non-findings citing the permitting clause. Quote exact header names, field names, and enum values.

- Mission id: `a938453df896f2f4` / session `cc-b8338a8e-6b46-425b-97f4-7c6f884905ec`
- Complexity: Standard / max-iter 2 / threshold 4.0 / role: implementer (not routed to goal contract)

## Plan

Inline plan per #339 (Standard iteration 1, `next` returned `plan-inline`):

| # | Step | Depends on | Done condition |
|---|---|---|---|
| 1 | `mission-state.py init` (Standard) — confirm no `route: goal` verdict | — | init ok, `loop_active: true` |
| 2 | Read both fixtures (parallel, exactly the two named files) | 1 | full text of spec + client notes in hand |
| 3 | Build drift table: each client-py behavior vs. governing spec clause | 2 | every client-py bullet classified |
| 4 | Write artifact: drift table + breaking-drift section (quoted evidence) + rejected-candidates section (permitting clause quoted) | 3 | artifact written, validator headings present |
| 5 | Scored review iteration: 2 reviewers in one parallel message → `review-finalize` → `closeout` | 4 | `passes: true` or documented halt |

## Execution

Executed steps 1–4 inline as orchestrator (bounded 2-fixture audit; see Assumptions). Both fixtures were read in a single parallel message. Classification method: the spec is the source of truth; every behavior bullet in `client-py.md` was matched against the governing spec clause and classified as **breaking drift** or **rejected candidate (spec-permitted / compliant)**.

### Drift table

| # | client-py behavior (quoted) | Spec clause (quoted) | Classification |
|---|---|---|---|
| D1 | "fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **Breaking drift** |
| D2 | "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." | "status \| enum \| one of: `pending`, `settled`, `cancelled`, `failed`" — "The `status` enum uses British spelling `cancelled`." | **Breaking drift** |
| C1 | "Sends the `X-Sig` header exactly as specified." | "Every request MUST carry the `X-Sig` header containing an HMAC of the body." | Rejected (compliant) |
| C2 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Rejected (compliant) |
| C3 | "Parses `expires_at` as epoch milliseconds." | "expires_at \| integer \| epoch_ms (milliseconds since epoch, UTC)" | Rejected (compliant) |
| C4 | "Sends an `X-Trace-Id` header on every request for distributed tracing." | Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." | Rejected (spec-permitted) |

### Breaking drifts (confirmed findings, with quoted evidence)

**D1 — Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Spec (source of truth): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (api-spec.md, POST /v2/transfers)
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." (client-py.md)
- Impact: every single-transfer POST violates a REQUIRED header clause. Breaking on every request, independent of retry behavior.

**D2 — Status enum spelling mismatch: client matches `canceled`, wire value is `cancelled`.**
- Spec: `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`." (api-spec.md, GET /v2/transfers/{id})
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." (client-py.md)
- Impact: the wire value `cancelled` never equals the client's `canceled` under exact string equality, so every cancelled transfer fails to map. Breaking for one of four enum values.

No other breaking drifts exist in the fixture: every remaining client-py bullet is classified in the drift table as compliant or spec-permitted (C1–C4).

### Rejected candidates (non-findings, with permitting clause)

- **C1 `X-Sig`**: client "Sends the `X-Sig` header exactly as specified." — satisfies "Every request MUST carry the `X-Sig` header containing an HMAC of the body." Compliant; not drift.
- **C2 Never retries POSTs**: the spec constraint is conditional — "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Never retrying satisfies MUST NOT trivially. Not drift (note: the client's *missing header* is already counted as D1; the retry posture itself is compliant).
- **C3 `expires_at` as milliseconds**: matches the spec exactly — "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms". Compliant; the seconds-vs-ms trap does not apply because the client already parses ms.
- **C4 `X-Trace-Id` extension header**: permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` extension header, so this is a non-finding by explicit clause.
- **Header casing (latent candidate)**: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." — any casing difference would be spec-permitted; additionally client-py states it sends `X-Sig` "exactly as specified", so no casing difference is even observed. Non-finding.

## Review

Scored review iteration 1: 2 independent reviewers (Standard), spawned in a single parallel message (spawn window recorded to `aggregate-reviews` via `--reviewer-window`, start 2026-08-07T06:26:43Z).

- Reviewer A (perspective: 正確性/網羅性 — spec-vs-client 照合の正しさ): score 5.0 across all four axes, findings: none.
- Reviewer B (perspective: 証跡品質/validator 適合 — 引用の正確性・見出し要件): findings: 1 Low (B-1 — the drift-table C4 cell quoted the Extension clause with an ellipsis instead of full verbatim text; the full quote was already correct in the rejected-candidates section).

Reviewer raw JSON is archived (paths in Evidence); verbatim re-quotation is omitted per output-compression discipline (#280). Aggregation and validation were performed by `mission-state.py review-finalize` (machine-validated, `--min-reviewers 2`).

Post-scoring fix: B-1 (Low) was fixed inline — the drift-table C4 cell now carries the full verbatim clause. Per M6, differential re-review is required only for Medium+ findings; B-1 is Low, so no re-review cycle was triggered.

## Score

Machine-recorded via `review-finalize` (aggregate-reviews → push-score, same validator), from `score_history` iteration 1 (timestamp 2026-08-07T06:30:36Z):

- composite_score: 4.85 (threshold 4.0)
- item scores: mission_achievement 4.88 / accuracy 4.75 / completeness 4.88 / usability 4.88 — min item 4.75 ≥ 3.5 gate
- max_agreement_delta: 0.5 (accuracy axis: A=5.0 vs B=4.5) ≤ 1.5 gate
- open_high: 0 / findings evidence: `.mission-state/archive/iter-1-a938453d-reviews.json`
- reviewers: 2 scoring, 0 findings-only ("aggregate-reviews: 2 scoring reviewer(s), 0 findings-only reviewer(s)")

## Stop Decision

Early-stop at iteration 1: composite 4.85 ≥ 4.0, `open_high == 0`, min item 4.75 ≥ 3.5, max_agreement_delta 0.5 ≤ 1.5. Continuation criteria for a second iteration (composite in 4.0–4.3 band AND ≥3 Medium findings) not met — composite is 4.85 and there are 0 Medium findings. First `closeout` attempt exited 2 (`mark-passes-gate-failed`: specialist selection checkpoint missing); after recording `specialists recommend --record-state` (task_profile.primary=backend), `closeout` exited 0 with `passes: true`, `loop_active: false`, phase `done`. Loop terminated after 1 scored iteration (max-iter 2 not exhausted).

## Evidence

- Fixtures read (the only benchmark files opened besides this artifact):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- All quoted strings in the drift table and finding sections are verbatim from those two fixtures (exact header names `X-Sig`, `Idempotency-Key`, `X-Trace-Id`; field names `id`, `status`, `expires_at`; enum values `pending`, `settled`, `cancelled`/`canceled`, `failed`).
- Mission state (auditable): `.mission-state/sessions/cc-b8338a8e-6b46-425b-97f4-7c6f884905ec.json` — mission_id `a938453df896f2f4`, `passes: true` recorded by `mark-passes` via `closeout`.
- Assumptions log: `.mission-state/sessions/cc-b8338a8e-6b46-425b-97f4-7c6f884905ec-assumptions.md`
- Reviewer raw JSON: `.mission-state/review-iter1-a.json`, `.mission-state/review-iter1-b.json`; archived aggregates: `.mission-state/archive/iter-1-a938453d-reviews.json`, `.mission-state/archive/iter-1-a938453d-scoring.json`.
- Specialist checkpoint: `specialists recommend --record-state` recorded task_profile primary=backend, secondary=[documentation], risk=medium; no external specialist invoked (fixture-bounded audit; specialists accounting reported `accounting_required: false`, no unaccounted candidates).
- Routing: the CLI did **not** route to the goal contract (`init` returned no `route: "goal"`; complexity Standard) — the full mission loop was executed as implementer.
- Unmeasured: wall-clock duration and token cost of this run were not measured by this artifact; reviewer-window parallelism was recorded to `aggregate-reviews` but not independently verified against transcript timestamps.

## Assumptions

- Benchmark rules prohibit network access, so `mission-local-authoring-sync.sh` was skipped; the repo-root `scripts/mission-state.py` was used as canonical (matches the skill rule "リポジトリ root では scripts/mission-state.py").
- Execution (fixture reading + artifact writing) was done inline by the orchestrator rather than a spawned executor subagent: the task is a bounded 2-fixture audit with one output file. The gated scored-review loop (2 parallel reviewers → review-finalize → closeout) was executed in full, which is what this benchmark measures.
- `client-py.md` prose notes are taken as the authoritative description of client behavior (no runnable client code exists in the fixture).
- "Breaking drift" is interpreted as: client behavior that violates a MUST/REQUIRED clause or mismatches a spec-defined wire value; differences explicitly permitted by a MAY/ignore clause are non-findings.
