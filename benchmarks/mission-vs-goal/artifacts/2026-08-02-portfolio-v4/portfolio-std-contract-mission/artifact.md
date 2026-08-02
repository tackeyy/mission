# portfolio-std-contract — API Contract Drift Audit (mission arm)

## Mission

Audit the client implementation described in `client-py.md` against the API specification in `api-spec.md` (spec is source of truth). Identify every breaking drift, and separately reject any spec-permitted differences as non-findings, citing the exact permitting clause. Quote exact header names, field names, and enum values as evidence. This artifact is the sole deliverable; no other files were modified except `.mission-state/`.

## Plan

1. Read both fixtures in full (`api-spec.md`, `client-py.md`) — no other files under `benchmarks/mission-vs-goal/` were opened.
2. Enumerate every behavior described in `client-py.md` and locate the corresponding spec clause in `api-spec.md`.
3. Classify each behavior as: (a) compliant / no drift, (b) breaking drift (violates a MUST/REQUIRED/enum-exactness clause), or (c) spec-permitted difference (candidate for rejection, with the permitting clause quoted).
4. Build a drift table covering all behaviors.
5. Write up breaking drifts with quoted spec text + quoted client text as evidence.
6. Write up rejected candidates with the quoted permitting clause.
7. Route through mission review (2 independent reviewers, Standard tier) before scoring.

## Execution

### Source fixtures read (verbatim, in full)

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` (33 lines)
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` (12 lines)

### Behavior-by-behavior analysis

| # | Client behavior (client-py.md) | Relevant spec clause (api-spec.md) | Verdict |
|---|---|---|---|
| 1 | "Sends the `X-Sig` header exactly as specified." | "Every request MUST carry the `X-Sig` header containing an HMAC of the body. Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." | Rejected candidate — compliant, not even a plausible drift |
| 2 | "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **Breaking drift** |
| 3 | "Never retries POSTs." | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." | Rejected candidate — vacuously compliant (never retrying trivially satisfies a MUST-NOT-without-condition rule) |
| 4 | "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." | "one of: `pending`, `settled`, `cancelled`, `failed`" ... "The `status` enum uses British spelling `cancelled`." | **Breaking drift** |
| 5 | "Parses `expires_at` as epoch milliseconds." | "`expires_at` \| integer \| epoch_ms (milliseconds since epoch, UTC)" | Rejected candidate — compliant, not even a plausible drift |
| 6 | "Sends an `X-Trace-Id` header on every request for distributed tracing." | Section 7: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." | **Rejected candidate** — spec-permitted |

All non-breaking rows are labeled "Rejected candidate" (not "No drift") so this table's Verdict column and the Evidence section's Rejected-candidates section use one consistent taxonomy: every row is either a **Breaking drift** or a **Rejected candidate** (with a sub-reason — spec-permitted difference, or vacuous/trivial compliance).

## Review

Two independent reviewers (Standard-tier, `review_tier=standard`, 2 reviewers per this mission's complexity) were spawned in parallel against this drafted artifact, scoped to: (a) does the drift table cover every behavior line in `client-py.md`; (b) is every breaking-drift claim backed by an exact quote from both fixtures; (c) is every rejected candidate backed by an exact quote of the permitting clause; (d) does the artifact satisfy the stated validator (drift table + breaking-drift section with quoted evidence + rejected-candidates section). Reviewer 1 (`correctness-and-evidence`) scored all four axes 5/5 with zero findings. Reviewer 2 (`validator-compliance-and-completeness`) scored all four axes 4/5 with 4 Low-severity findings (label inconsistency between the drift table and the Evidence section, a pointer-only "Drift table" subsection, unverifiable line-number citations, and a vague archive reference for reviewer JSON) — no Medium or High findings from either reviewer. All 4 Low findings were fixed in this artifact (drift table Verdict column unified to a Breaking-drift/Rejected-candidate taxonomy; a literal table added under Evidence → Drift table; a note added clarifying line numbers are locators and quoted text is primary evidence; reviewer JSON paths made concrete at `.mission-state/reviews/iter1-correctness.json` and `.mission-state/reviews/iter1-validator.json`). Per this mission's M6 rule, fixes to Low-severity findings do not require an additional differential review pass (that requirement applies at Medium+). Raw reviewer JSON, the aggregate, and the scoring record are archived at `.mission-state/archive/iter-1-cb7917a7-reviews.json` and `.mission-state/archive/iter-1-cb7917a7-scoring.json`.

## Score

Composite score: **4.5 / 5** on all four axes (`mission_achievement=4.5, accuracy=4.5, completeness=4.5, usability=4.5`), computed via `mission-state.py review-finalize --iteration 1` from the two reviewer scores above (threshold = 4.0). `open_high = 0`. `review_agreement = 4.0`; per-axis agreement delta = 1.0 (reviewer range 4.0–5.0 on every axis), which is `<= 1.5`. `min(scored_items) = 4.5 >= 3.5`. Findings-evidence gate satisfied: `findings_evidence_path` = `.mission-state/archive/iter-1-cb7917a7-reviews.json`, and every finding row in this artifact cites an exact quoted string from `api-spec.md` and/or `client-py.md`.

## Stop Decision

`passes = true`. All gate conditions met on iteration 1 (early-stop, no second iteration required): `findings_evidence_path` populated and non-empty, `evidence_high_count == open_high == 0`, `max_agreement_delta (1.0) <= 1.5`, `composite_score (4.5) >= threshold (4.0)`, `min(scored_items) (4.5) >= 3.5`. `mark-passes` was invoked via `closeout`, which returned `next_action=report-complete`. No `halt_reason` was set.

## Evidence

### Drift table

| # | Client behavior | Verdict |
|---|---|---|
| 1 | Sends `X-Sig` header exactly as specified | Rejected candidate — compliant |
| 2 | Missing `Idempotency-Key` on `POST /v2/transfers` | **Breaking drift** |
| 3 | Never retries POSTs | Rejected candidate — vacuously compliant |
| 4 | `status` enum American-spelling mapping (`canceled`) vs. British wire value (`cancelled`), exact-string match | **Breaking drift** |
| 5 | Parses `expires_at` as epoch milliseconds | Rejected candidate — compliant |
| 6 | Sends extra `X-Trace-Id` header | Rejected candidate — spec-permitted |

(Full quoted spec/client text for each row is in the behavior-by-behavior table under Execution; this is the same 6-row classification duplicated here so the table is present directly under this heading.)

### Breaking-drift section (quoted evidence)

**Breaking drift 1 — missing `Idempotency-Key` on `POST /v2/transfers`**
- Spec (api-spec.md, line 18-19): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (client-py.md, line 4-6): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the spec's REQUIRED clause applies to "every POST /v2/transfers request" with no exception; the client's own documentation states the header is absent on exactly this endpoint.

**Breaking drift 2 — `status` enum spelling mismatch on exact-string match**
- Spec (api-spec.md, line 27): "one of: `pending`, `settled`, `cancelled`, `failed`" and (line 30): "The `status` enum uses British spelling `cancelled`."
- Client (client-py.md, line 8-10): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: the wire value emitted by the server is `cancelled` (British spelling, per spec), but the client's mapping table key is `canceled` (American spelling) and the match is exact string equality. A wire value of `cancelled` will not equal the client's `canceled` key, so the `cancelled` status will fail to map.

### Rejected candidates (spec-permitted, not findings)

- **Candidate: client sends an extra `X-Trace-Id` header on every request.**
  Rejected as a non-finding. Permitting clause — api-spec.md, section 7 (line 9-11): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` header not defined elsewhere in the spec and is explicitly named as an example ("tracing headers") of a permitted extension.

- **Candidate: client never retries POST requests at all.**
  Rejected as a non-finding. Permitting/governing clause — api-spec.md, line 16-19: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." The spec only forbids retrying without the header; it never mandates that clients retry. A client that never retries satisfies this MUST-NOT clause vacuously.

- **Candidate: client sends `X-Sig` "exactly as specified" (casing).**
  Rejected as a non-finding — not even a plausible drift. Permitting clause — api-spec.md, line 5-6: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Client behavior is explicitly within the spec's stated tolerance.

### Process evidence (mission mechanics)

- Mission session file: `.mission-state/sessions/cc-3a2c8ddf-a5ae-46ca-b2da-f8519bb27cfb.json`, `mission_id=cb7917a72f1984bb`, complexity=Standard, `review_tier` auto-derived at `init`.
- `mission-state.py init` returned `"permission_preflight": "passed"` — no routing to the goal contract occurred (`init` response contained no `route` field, and no `routed-goal` halt was produced), so the full mission loop (Plan/Execution/Review/Score/Stop Decision) applies as instructed.
- Iteration count: 1 (early-stop; no second iteration was needed — `open_high=0` and `composite_score=4.5 >= 4.0` on the first pass).
- Scope discipline: only the two named fixture files under `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/` and this output file were read or written; no other path under `benchmarks/mission-vs-goal/` (task definitions, scoring configuration, answer keys) was opened, grepped, or listed.
- No network access, package installation, commit, or push was performed during this run.

## Assumptions

- The task prompt's "reject spec-permitted differences as non-findings citing the permitting clause" is read to require, for each rejected candidate, an explicit quote of the specific spec sentence that permits the client's behavior — provided above for all three rejected candidates.
- "Every breaking drift" is interpreted as every behavior in `client-py.md` that contradicts a MUST/MUST NOT/REQUIRED clause or an exact-match enum/field semantic in `api-spec.md`; two such behaviors were found (`Idempotency-Key` omission, `status` enum spelling mismatch under exact-string matching). No unmeasured or ambiguous items remain — every one of the six client-described behaviors was classified as either compliant, breaking, or rejected, with quoted evidence in each case.
- Reviewer identities and full verbatim review transcripts are process-internal to the mission run; the two reviewers' `mission-review/1` JSON outputs are saved at `.mission-state/reviews/iter1-correctness.json` and `.mission-state/reviews/iter1-validator.json` (not embedded in the session JSON), and are referenced by path here rather than re-quoted in full, per output-compression practice. The classification and quoted evidence above are what the reviewers verified.
- Line-number citations in this Evidence section (e.g. "api-spec.md, line 18-19") are locators to help a human find the passage quickly; the quoted text itself, not the line number, is the primary evidence and was verified verbatim against the two fixtures during Execution.
