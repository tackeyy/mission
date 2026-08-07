# portfolio-std-contract — mission arm (rep2)

## Mission

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth) using exactly two fixtures:
`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` and
`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`.
Find every breaking drift, reject spec-permitted differences as non-findings citing the permitting clause, and record the audit in this artifact with mission-state evidence.

- Arm: mission / profile: full / complexity: Standard / `--max-iter 2`
- Mission state session: `cc-70a6cd6c-ac8e-4d78-98ee-f12bad570d43` (mission_id `4121bc8915170753`), `.mission-state/sessions/cc-70a6cd6c-ac8e-4d78-98ee-f12bad570d43.json`
- Routing: `init` returned a mission session (no `route: "goal"` verdict) — Standard complexity keeps the mission loop.

## Plan

Inline bounded plan (iteration 1, Standard → `next_action: plan-inline`, #339):

| # | Step | Depends on | Done when |
|---|---|---|---|
| 1 | Read both fixtures (spec + client-py) exactly, nothing else under `benchmarks/mission-vs-goal/` | — | Both files read verbatim |
| 2 | Build drift table: every client behavior vs the governing spec clause | 1 | Table covers all 6 client-py behaviors |
| 3 | Classify each row as breaking drift or rejected candidate, quoting exact header names / field names / enum values and the permitting clause for rejections | 2 | Every confirmed finding has a verbatim quote; every rejection cites its clause |
| 4 | Write artifact with all required headings + validator sections | 3 | Artifact saved at the mandated path |
| 5 | Run one scored review iteration: 2 reviewers in parallel → `review-finalize` → `closeout` | 4 | `passes: true` or documented halt |

Completion condition: artifact validator satisfied (drift table + breaking-drift section with quoted evidence + rejected-candidates section) AND mission gates (`composite_score >= 4.0`, `open_high == 0`, agreement ≤ 1.5) met via CLI, not hand computation.

## Execution

Both fixtures were read in full. The client-py notes describe exactly 6 externally observable behaviors; each is audited below.

### Drift table

| # | Client-py behavior (quoted) | Spec clause (quoted) | Verdict |
|---|---|---|---|
| 1 | "Sends the `X-Sig` header exactly as specified." | "Every request MUST carry the `X-Sig` header containing an HMAC of the body." | Compliant — not a drift |
| 2 | "fires the request without an `Idempotency-Key` header" (POST /v2/transfers) | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **BREAKING drift** |
| 3 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Compliant — not a drift (never retrying satisfies MUST NOT retry) |
| 4 | "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." | "status \| enum \| one of: `pending`, `settled`, `cancelled`, `failed`" — "The `status` enum uses British spelling `cancelled`." | **BREAKING drift** |
| 5 | "Parses `expires_at` as epoch milliseconds." | "expires_at \| integer \| epoch_ms (milliseconds since epoch, UTC)" | Compliant — not a drift |
| 6 | "Sends an `X-Trace-Id` header on every request for distributed tracing." | Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here … Sending an extension header is never a contract violation." | Rejected candidate — spec-permitted |

### Breaking drifts (confirmed findings)

**B1 — Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Client evidence (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Spec evidence (api-spec.md, POST /v2/transfers): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Why breaking: an unconditional REQUIRED request header is absent on every single-transfer request; every such request violates the contract. The bulk-endpoint-only generation does not cover this path.

**B2 — Status enum spelling mismatch: client matches `canceled`, wire value is `cancelled`.**
- Client evidence (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Spec evidence (api-spec.md, GET /v2/transfers/{id}): status is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Why breaking: exact string equality against the wire value means the spec value `cancelled` never matches the client key `canceled`; every cancelled transfer fails to map. The other three values (`pending`, `settled`, `failed`) are identical in both spellings and are unaffected.

### Rejected candidates (non-findings, with permitting clause)

**R1 — `X-Trace-Id` header sent on every request.**
Not a drift. Permitting clause — api-spec.md Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` extension header and is even the clause's own example category ("tracing headers").

**R2 — `expires_at` parsed as epoch milliseconds.**
Not a drift. The spec defines `expires_at` as "epoch_ms (milliseconds since epoch, UTC)" and warns "treating it as seconds shifts expiry by three orders of magnitude." The client parses it "as epoch milliseconds" — exactly the specified unit. This is a candidate only because the spec flags it as a common failure; the client got it right.

**R3 — "Never retries POSTs."**
Not a drift. Permitting clause — api-spec.md POST /v2/transfers: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying is the conservative behavior the MUST NOT allows unconditionally; the clause only constrains clients that do retry.

**R4 — `X-Sig` header casing/presence.**
Not a drift. The client "Sends the `X-Sig` header exactly as specified," satisfying "Every request MUST carry the `X-Sig` header." Additionally, "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing," so no casing variant could be a drift either.

## Review

Iteration 1: 2 reviewers (Standard tier) spawned in parallel in a single message (`parallel_execution: true` confirmed by aggregate-reviews).

- Reviewer A (perspective `A`, contract-audit correctness): mission_achievement 5.0 / accuracy 5.0 / completeness 5.0 / usability 4.0. High 0, Medium 0, Low 1 (`A-1`, usability: Assumptions section is Japanese-only in an otherwise English artifact).
- Reviewer B (perspective `verify`, evidence/validator compliance): mission_achievement 5.0 / accuracy 5.0 / completeness 4.2 / usability 4.6 (composite 4.7). High 0, Medium 0, Low 1 (`verify-b-l1`, completeness: R2/R3/R4 sit in rejected-candidates while the drift table labels them "Compliant — not a drift" — a structural-labeling inconsistency, no accuracy impact).
- Schema repairs before aggregation (recorded for audit): reviewer finding ids were normalized to the `<perspective>-` prefix required by the `mission-review/1` validator, one finding `axis` was mapped to the allowed enum (`completeness`), and Reviewer B re-emitted its own `scores` onto the required 4 axes after the orchestrator reported the validator rejection back to that reviewer. Review judgments and severities were not altered by the orchestrator.
- Raw review JSON archived at `.mission-state/archive/iter-1-4121bc89-reviews.json`; scoring JSON at `.mission-state/archive/iter-1-4121bc89-scoring.json`.

## Score

Tool-computed by `mission-state.py review-finalize` (aggregate-reviews → push-score), iteration 1, timestamp 2026-08-07T09:42:36Z:

- composite_score: **4.72** (threshold 4.0)
- items: mission_achievement 5.0 / accuracy 5.0 / completeness 4.6 / usability 4.3 → min_item 4.3 (gate ≥ 3.5)
- max agreement delta: 0.8 on completeness (gate ≤ 1.5); review_agreement 4.0
- open_high: 0; findings_evidence_path: `.mission-state/archive/iter-1-4121bc89-reviews.json`

## Stop Decision

Early-stop at iteration 1: composite 4.72 ≥ threshold 4.0 and `open_high == 0` (`--max-iter 2` not exhausted; both open findings are Low). Continuation criteria for early-stop override (composite 4.0–4.3 or ≥3 Medium findings) do not apply. `closeout` (mark-passes → next) returned `passes=true` / `next_action=report-complete`. No forced pass (`mark-passes --force` not used).

## Evidence

- Mission state: `.mission-state/sessions/cc-70a6cd6c-ac8e-4d78-98ee-f12bad570d43.json` (mission_id `4121bc8915170753`, lease-fenced mutations, `permission_preflight: passed`)
- Review artifacts: `.mission-state/` review JSON + scoring JSON (paths recorded in state; not transcribed here per output-compression rule #280)
- Fixture quotes above are verbatim from `api-spec.md` and `client-py.md`; no other file under `benchmarks/mission-vs-goal/` was opened.
- Closeout (verified from CLI output, not hand-computed): `closeout` → `mark_passes: {passes: true, forced: false}`, `next: {next_action: "report-complete", phase: "done", iteration: 1, loop_active: false, passes: true}`. First `closeout` attempt exited 2 on the missing specialist-selection checkpoint; `specialists recommend --record-state` was run (task_profile.primary `backend`, no external specialist used → selected/used/degraded all empty), after which closeout passed.
- Unmeasured: wall-clock duration and token cost of this run were not instrumented; no runtime execution of client code occurred (the audit is documentary — client-py.md is prose notes, not executable code).

## Assumptions

- `client-py.md` の 6 挙動が client の外部観測可能な契約面のすべてであると仮定（fixture が "implementation notes" として列挙する範囲を全数とみなす）。
- R2 (`expires_at`) と R4 (`X-Sig`) は「仕様どおりの挙動」であり、独立した permitting clause を持たない。却下理由として定義句そのものを引用した。
- mission-local-authoring-sync.sh は local checkout が dirty（かつベンチマーク規則で network 禁止）のため実行不可。fail-closed 規則とベンチマークの「network 禁止・この artifact を完成させよ」が競合するため、後者（明示的なユーザー指示）を優先し、インストール済み skill 版で続行した。この逸脱はここに記録する。
- Benchmark rules honored: no commit/push/network/package install; writes limited to this artifact and `.mission-state/`.
