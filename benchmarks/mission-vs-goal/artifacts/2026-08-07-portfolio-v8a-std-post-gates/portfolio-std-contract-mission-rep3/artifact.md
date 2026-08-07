# portfolio-std-contract — mission arm (rep3)

## Mission

Audit the `client-py` implementation against `Transfer API Specification v2` (the source of truth). Find every breaking drift, reject spec-permitted differences as non-findings citing the permitting clause, and quote exact header names, field names, and enum values.

- Task id: `portfolio-std-contract` / category: `api-contract` / arm: `mission` / profile: full
- Fixtures (the only benchmark files read): `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`, `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- Mission state: `.mission-state/sessions/cc-50bdad69-1601-429e-aa45-967e6d484115.json` (mission_id `21e678ef3f5811c1`, complexity Standard, threshold 4.0, max-iter 2)

## Plan

Inline bounded plan (iteration 1, Standard → `next_action: plan-inline`, #339):

| # | Step | Depends on | Done when |
|---|---|---|---|
| 1 | Read both fixtures verbatim (no other benchmark files) | — | Both files read; quoted values available |
| 2 | Enumerate every client-py behavior and classify each against the spec clause it touches | 1 | Drift table covers all 6 behaviors listed in client-py.md |
| 3 | Write breaking-drift section with verbatim spec + client quotes | 2 | Every confirmed finding has exact quoted identifiers |
| 4 | Write rejected-candidates section citing the permitting clause per candidate | 2 | Every rejection names its permitting clause |
| 5 | Advance to reviewing; spawn 2 reviewers in parallel (Standard); review-finalize; closeout | 3,4 | `mark-passes` gates satisfied or halt recorded |

Completion condition: artifact contains drift table + breaking-drift section with quoted evidence + rejected-candidates section (task validator), and the mission loop records at least one scored review iteration.

## Execution

Both fixtures were read in full. client-py.md lists 6 observable behaviors; each was checked against the spec clause governing it.

### Drift table

| # | Client behavior (client-py.md) | Governing spec clause (api-spec.md) | Verdict |
|---|---|---|---|
| 1 | "Sends the `X-Sig` header exactly as specified." | Authentication: "Every request MUST carry the `X-Sig` header" | Compliant — not drift |
| 2 | POST /v2/transfers "fires the request without an `Idempotency-Key` header" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **BREAKING (B-1)** |
| 3 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Compliant — not drift (rejected candidate R-3) |
| 4 | Maps status enum with American spelling `canceled`, "exact string equality against the wire value" | status enum "one of: `pending`, `settled`, `cancelled`, `failed`"; "The `status` enum uses British spelling `cancelled`." | **BREAKING (B-2)** |
| 5 | "Parses `expires_at` as epoch milliseconds." | "`expires_at` ... integer ... epoch_ms (milliseconds since epoch, UTC)" | Compliant — not drift (rejected candidate R-2) |
| 6 | "Sends an `X-Trace-Id` header on every request" | Extension clause (section 7): extension headers permitted | Permitted — not drift (rejected candidate R-1) |

### Breaking drifts (confirmed findings)

**B-1: Missing required `Idempotency-Key` header on POST /v2/transfers.**

- Spec (api-spec.md, "POST /v2/transfers"): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the header is REQUIRED on **every** request, unconditionally. The client's no-retry policy does not cure this drift — the requirement is not conditioned on retrying. Every single-transfer POST violates the contract.

**B-2: Status enum spelling mismatch — client matches `canceled`, wire value is `cancelled`.**

- Spec (api-spec.md, "GET /v2/transfers/{id}"): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`", and "The `status` enum uses British spelling `cancelled`."
- Client (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: with exact string equality, the wire value `cancelled` never matches the client's `canceled` entry, so every cancelled transfer falls through the mapping (unmapped/unknown state). `pending`, `settled`, `failed` are spelled identically in both and are unaffected.

### Rejected candidates (spec-permitted differences — non-findings)

**R-1: `X-Trace-Id` extension header.** Client sends "an `X-Trace-Id` header on every request for distributed tracing." Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."

**R-2: `expires_at` parsed as milliseconds.** Client "Parses `expires_at` as epoch milliseconds"; the spec defines `expires_at` as "epoch_ms (milliseconds since epoch, UTC)". This is exact compliance, not drift. (Drift would exist only if the client treated it as seconds: "treating it as seconds shifts expiry by three orders of magnitude.")

**R-3: "Never retries POSTs."** The spec's retry rule is a prohibition ("clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header"); never retrying trivially satisfies it. Not a drift — the violation is the missing header on the initial request, already counted as B-1; the retry behavior itself is compliant.

**R-4: `X-Sig` header presence/casing.** Client "Sends the `X-Sig` header exactly as specified", satisfying "Every request MUST carry the `X-Sig` header". Even a casing difference would be permitted: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."

Conclusion: exactly **2 breaking drifts** (B-1, B-2); all other observed differences are spec-permitted or compliant (R-1..R-4).

## Review

Iteration 1: 2 independent reviewers (Standard complexity) spawned in parallel in a single message (`parallel_execution: true` per aggregate-reviews), perspectives A (correctness of drift classification vs fixtures) and B (completeness/validator conformance).

- Reviewer A: 4 axes all 5.0, findings: none (same_score_note recorded).
- Reviewer B: mission_achievement 4.0 / accuracy 4.7 / completeness 4.0 / usability 4.0; findings: B-1 Medium (Score section was a placeholder at review time), B-2 Low (Stop Decision outcome not yet recorded).
- Raw `mission-review/1` JSON saved verbatim at `.mission-state/reviews/iter1-A.json` / `iter1-B.json`; archived evidence at `.mission-state/archive/iter-1-21e678ef-reviews.json`.
- Post-review inline fix (this section and Score/Stop Decision below populated with tool-computed values) addresses B-1/B-2; per M6, one diff reviewer re-verified the fix before pass judgment.

## Score

Tool-computed by `review-finalize` (aggregate-reviews → push-score), iteration 1, timestamp 2026-08-07T09:50:50Z:

| Item | Score |
|---|---|
| mission_achievement | 4.5 |
| accuracy | 4.85 |
| completeness | 4.5 |
| usability | 4.5 |
| **composite** | **4.59** |
| min_item | 4.5 |
| review_agreement | 4.0 (max axis delta 1.0 ≤ 1.5) |
| open_high | 0 |

Scoring evidence: `.mission-state/archive/iter-1-21e678ef-scoring.json`.

## Stop Decision

Gates at iteration 1: composite 4.59 ≥ threshold 4.0; min_item 4.5 ≥ 3.5; open_high 0; max agreement delta 1.0 ≤ 1.5; findings evidence archived. Early-stop applies (threshold reached with open_high == 0 at iter 1; composite 4.59 is above the 4.0–4.3 continue band). Decision: **stop — pass** via `closeout` (mark-passes → next); the closeout exit status is recorded in Evidence.

## Evidence

- Confirmed finding B-1 verbatim spec quote: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." — api-spec.md, POST /v2/transfers section.
- Confirmed finding B-1 verbatim client quote: "fires the request without an `Idempotency-Key` header" — client-py.md.
- Confirmed finding B-2 verbatim spec quotes: enum "one of: `pending`, `settled`, `cancelled`, `failed`"; "The `status` enum uses British spelling `cancelled`." — api-spec.md, GET /v2/transfers/{id}.
- Confirmed finding B-2 verbatim client quotes: "American spelling: `pending`, `settled`, `canceled`, `failed`"; "matches on exact string equality against the wire value." — client-py.md.
- Rejection R-1 permitting clause: Extension clause (section 7), "Sending an extension header is never a contract violation." — api-spec.md.
- Mission state CLI outputs (init / next / advance / review-finalize / closeout) recorded in `.mission-state/sessions/cc-50bdad69-1601-429e-aa45-967e6d484115.json`; scoring JSON archived at `.mission-state/archive/iter-1-21e678ef-scoring.json`, review findings at `.mission-state/archive/iter-1-21e678ef-reviews.json`.
- review-finalize output (2026-08-07T09:50:50Z): composite 4.59, min_item 4.5, open_high 0, review_agreement 4.0, parallel_execution true.
- M6 diff re-verification (1 reviewer, perspective `verify`, JSON at `.mission-state/reviews/iter1-verify.json`): B-1 and B-2 confirmed resolved; all transcribed numbers match `.mission-state/archive/iter-1-21e678ef-scoring.json` exactly; findings: none.
- Specialist checkpoint: `specialists recommend --record-state` → task_profile.primary `backend`, decision policy `fallback` / action `continue-core` (top preset specialist `backend-provider` not installed); `specialists accounting` → `unaccounted_candidates: []`.
- Closeout: first attempt exit 2 (specialist checkpoint missing — gate worked as designed); after recording the checkpoint, `closeout` exit 0 with `mark_passes.ok: true`, `passes: true`, `forced: false`, `next_action: report-complete`, `loop_active: false`, iteration 1.
- Unmeasured: runtime behavior of the actual client code (only the fixture's implementation notes were audited); server-side enforcement behavior.

## Assumptions

- The two fixture files are the complete and authoritative record of spec and client behavior; no other benchmark files were read (per task rules — benchmark metadata is out of bounds).
- `MISSION_PLUGIN_ROOT` points to `~/dev/mission`, but the local-authoring sync script was **not** run because the benchmark forbids network access; the repo-root `scripts/mission-state.py` (this checkout) was used as the state oracle instead. Fail-closed sync was intentionally skipped under the benchmark's no-network rule and this is recorded here rather than treated as a silent fallback.
- client-py.md's phrase "exactly as specified" for `X-Sig` is taken at face value (HMAC correctness itself is unmeasured).
- "epoch milliseconds" (client) ≡ "epoch_ms" (spec) — treated as the same unit.
- Reviewer count 2 per Standard complexity; no security/irreversibility signals, so no escalation to 3.
