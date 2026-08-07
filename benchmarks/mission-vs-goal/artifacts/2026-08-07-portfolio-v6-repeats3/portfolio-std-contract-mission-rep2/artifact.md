# portfolio-std-contract — mission arm (rep2)

## Mission

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift, reject spec-permitted differences as non-findings citing the permitting clause, and record auditable mission-state evidence. Fixtures read (exactly as instructed):

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

Task id: `portfolio-std-contract` / category: `api-contract` / complexity: Standard / arm: mission (profile full, `--max-iter 2`).

## Plan

Inline bounded plan (iteration 1, Standard, per `next` verdict `plan-inline` #339):

| # | Step | Depends on | Done condition |
|---|---|---|---|
| 1 | Read both fixtures in full | — | Both files read; no other `benchmarks/mission-vs-goal/` paths touched |
| 2 | Enumerate every client behavior and classify: breaking drift / spec-permitted / compliant | 1 | Each client-py bullet mapped to a spec clause with exact quotes |
| 3 | Write artifact with drift table, breaking-drift section (quoted evidence), rejected-candidates section | 2 | Validator headings all present |
| 4 | Scored review iteration: 2 reviewers in parallel → `review-finalize` → `closeout` | 3 | `passes: true` or documented halt |

## Execution

Both fixtures were read in full (Read tool, whole files: api-spec.md 32 lines, client-py.md 12 lines). Every bullet of client-py.md was mapped against the spec. Results below.

### Drift table

| # | Client behavior (client-py.md) | Spec clause (api-spec.md) | Classification |
|---|---|---|---|
| 1 | POST /v2/transfers "fires the request without an `Idempotency-Key` header" | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request" | **Breaking drift** |
| 2 | Status mapping uses American spelling `canceled`, "matches on exact string equality against the wire value" | status enum is "one of: `pending`, `settled`, `cancelled`, `failed`" — "The `status` enum uses British spelling `cancelled`" | **Breaking drift** |
| 3 | "Sends an `X-Trace-Id` header on every request" | Extension clause (section 7): "Clients MAY send additional `X-*` extension headers … never a contract violation" | Rejected — spec-permitted |
| 4 | "Sends the `X-Sig` header exactly as specified" | "Every request MUST carry the `X-Sig` header"; casing free per RFC 9110 clause | Compliant — no drift |
| 5 | "Parses `expires_at` as epoch milliseconds" | "`expires_at` … epoch_ms (milliseconds since epoch, UTC)" | Compliant — no drift |
| 6 | "Never retries POSTs" | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | Compliant — no drift |

### Breaking drifts (confirmed findings)

**B1 — Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Spec (source of truth): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Impact: every single-transfer POST violates a REQUIRED header contract. Breaking.

**B2 — Status enum spelling mismatch: client `canceled` vs wire `cancelled`.**
- Spec: status is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Impact: the wire value `cancelled` can never equal the client's `canceled` under exact string equality, so every cancelled transfer fails to map. Breaking.

No other breaking drifts exist in client-py.md: all six client behaviors listed in the fixture are accounted for in the drift table above (2 breaking, 1 spec-permitted, 3 compliant).

### Rejected candidates (non-findings, with permitting/compliant clause)

**R1 — `X-Trace-Id` extension header.** Rejected as a finding. Permitting clause: Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` matches the `X-*` pattern.

**R2 — `X-Sig` header casing / presence.** Not a drift: client "Sends the `X-Sig` header exactly as specified", and the spec additionally permits any casing: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."

**R3 — `expires_at` parsed as milliseconds.** Not a drift: spec defines `expires_at` as "epoch_ms (milliseconds since epoch, UTC)" and the client "Parses `expires_at` as epoch milliseconds" — this is the correct behavior, not the seconds-mistake the spec warns about ("treating it as seconds shifts expiry by three orders of magnitude").

**R4 — "Never retries POSTs".** Not a drift: the spec's constraint is "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header". Not retrying at all satisfies this MUST NOT; no clause requires retrying.

## Review

Scored review iteration 1: 2 independent reviewers (Standard tier) spawned in parallel in a single message, each returning `mission-review/1` JSON. Aggregation and gate validation via `mission-state.py review-finalize` (aggregate-reviews → push-score). Details in Evidence.

- Reviewer A (all axes 5.0, findings: 0) / Reviewer B (mission_achievement 4.75, accuracy 5.0, completeness 4.75, usability 4.5; findings: B-1 Low「compliant 挙動が Rejected-Candidates 節に同居」, B-2 Low「'Never retries POSTs' の重複記載」)。High/Medium 指摘 0 件。

## Score

Tool-computed values from `review-finalize --iteration 1` (aggregated scoring JSON archived at `.mission-state/archive/iter-1-1ce8e1a7-scoring.json`):

- Item scores: mission_achievement 4.88, accuracy 5.0, completeness 4.85, usability 4.75 (min item 4.75 ≥ 3.5 gate)
- review_agreement: 5.0; max per-axis agreement delta 0.5 (usability, min 4.5 / max 5.0) ≤ 1.5 gate
- open_high: 0; findings evidence recorded in the archived scoring JSON
- Gate result: `closeout` → `mark-passes` ok (`passes: true`, `forced: false`), `next` returned `next_action: "report-complete"`, `phase: done`, `loop_active: false`

## Stop Decision

Early-stop at iteration 1: all item scores ≥ 4.75 vs threshold 4.0, `open_high == 0`, agreement gate green, `closeout` exit 0 with `report-complete`. `--max-iter 2` allows a second iteration, but the only open findings are 2 Low organisational notes (B-1, B-2) — the early-stop continuation criteria (composite 4.0-4.3 or ≥3 Medium findings) are not met, so the mission stops here. Mission complete.

## Evidence

- Mission state session: `.mission-state/sessions/cc-d5c1e656-0fa1-45e1-a8d7-d5be1a92e61f.json` (mission_id `1ce8e1a7ce1b27b4`, `permission_preflight: passed`).
- Routing: `init` returned a normal multi-session state (no `route: "goal"` verdict; complexity Standard) — mission loop executed, not routed to goal contract.
- Plan mode: `next` returned `next_action: "plan-inline"` (iteration 1, Standard) — plan written inline above.
- Fixture reads: only the two named fixture files and this output file were opened; no other `benchmarks/mission-vs-goal/` paths were listed, read, or grepped.
- All quoted strings in the drift table and findings are verbatim from the two fixture files (header names `X-Sig`, `Idempotency-Key`, `X-Trace-Id`; field names `id`, `status`, `expires_at`; enum values `pending`, `settled`, `cancelled`, `canceled`, `failed`).
- Review artifacts: reviewer JSONs at `.mission-state/review-iter1-a.json`, `.mission-state/review-iter1-b.json`; aggregated scoring archived at `.mission-state/archive/iter-1-1ce8e1a7-scoring.json` via `review-finalize --iteration 1 --min-reviewers 2` with reviewer windows recorded. Two reviewers were spawned in parallel in a single message; schema-conformant re-emission was requested once (initial JSONs lacked the 4-axis `scores` object).
- Specialists: `specialists recommend --record-state` recorded `specialists_decision.policy: "fallback"` (top preset specialist `backend-provider` not installed → continue-core).
- Timing beyond tool-recorded timestamps is unmeasured; no runtime performance claims are made. No benchmark-superiority claim is made.

## Assumptions

- `client-py.md` is an implementation-notes fixture; its statements are treated as a faithful description of the client's behavior (it is the only primary source available).
- "Never retries POSTs" is interpreted as compliant: the spec forbids retrying without `Idempotency-Key` but nowhere requires retrying.
- Standard complexity assumed per the task definition; not downgraded to Simple (multi-step: 2-fixture audit + validator-shaped artifact + gated review loop). Recorded in `.mission-state/sessions/cc-d5c1e656-0fa1-45e1-a8d7-d5be1a92e61f-assumptions.md`.
- Benchmark rules (no commit/push/network, scoped writes) take precedence over global git-worktree conventions for this run.
