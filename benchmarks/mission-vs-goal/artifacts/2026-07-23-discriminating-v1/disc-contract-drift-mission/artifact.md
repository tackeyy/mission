# disc-contract-drift — Mission Artifact

## Mission

Audit three client implementations (`client-go`, `client-py`, `client-js`) against
`api-spec.md` ("Transfer API Specification v2", the source of truth) and identify
every **breaking** drift — client behavior that violates the spec's contract.
Differences the spec explicitly permits (extension headers, case-insensitive header
name matching) must be identified and rejected as non-findings, with the permitting
clause quoted. Coverage must be exhaustive: every spec-governed behavior is checked
against every client, including fully compliant behavior, so nothing is silently
skipped.

Scope fixtures read (exactly these four, per task instructions — no other files
under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed):

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-go.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-js.md`

Validator requirements this artifact must satisfy: a per-client drift table
(client, spec requirement, client behavior, breaking-or-permitted), a breaking-drift
section with quoted evidence, and a rejected-candidates section citing the spec
clause that permits each rejected difference.

## Plan

Mission complexity: Complex (per task instructions). Planned steps:

1. Read `api-spec.md` in full and extract every normative (MUST/MUST NOT/MAY/REQUIRED)
   clause governing client behavior: auth header, extension-header policy,
   `POST /v2/transfers` idempotency rules, `GET /v2/transfers/{id}` response field
   semantics (`status` enum spelling, `expires_at` units).
2. Read each of the three client fixtures (`client-go.md`, `client-py.md`,
   `client-js.md`) and extract every stated behavior.
3. Cross-check every client behavior against every applicable spec clause —
   exhaustively, not just where a drift is suspected — and classify each pairing as
   `compliant`, `breaking`, or `permitted-difference`.
4. For every `permitted-difference` classification, locate and quote the specific
   spec clause that authorizes it (case-insensitive matching clause or the section 7
   extension-header clause). If no such clause exists, the item cannot be classified
   as permitted and must be re-evaluated as breaking or compliant.
5. For every `breaking` classification, quote the exact spec requirement text and
   the exact client fixture text as evidence, and state the concrete failure
   scenario it causes.
6. Build the per-client drift table, breaking-drift section, and rejected-candidates
   section required by the task validator.
7. Independent review pass (Phase 4): re-derive the same cross-check from the raw
   fixture text without reading the draft's conclusions first, then diff against the
   draft to catch missed drifts, mis-scoped rejections, or unsupported claims.
8. Score against the mission rubric; record Stop Decision.

Planner note (process deviation, recorded because it affects auditability): the
`mission-planner` Skill tool call was attempted twice for this step and both times
returned only `<error>Execute skill: mission-planner</error>` with no plan body —
a tool-execution failure in this sandbox, not a skipped step. The plan above was
produced inline by the orchestrator applying the mission-planner instructions in the
same context, per the documented Codex-style fallback in the mission Compact
Instructions. Logged in `Assumptions` and in `.mission-state/sessions/cc-7ad05375-ede5-4d87-99de-b4f743443954-assumptions.md`.

## Execution

### Spec clauses extracted (verbatim, from `api-spec.md`)

| # | Clause (quoted) | Governs |
|---|---|---|
| S1 | "Every request MUST carry the `X-Sig` header containing an HMAC of the body." | Auth header presence/name |
| S2 | "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." | Header name casing |
| S3 | "Clients MAY send additional `X-*` extension headers not defined here ... Sending an extension header is never a contract violation." | Extra headers |
| S4 | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." | Retry safety on POST /v2/transfers |
| S5 | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | Idempotency-Key presence (all requests, not just retries) |
| S6 | "one of: `pending`, `settled`, `cancelled`, `failed`" / "The `status` enum uses British spelling `cancelled`." | GET .../{id} `status` enum values |
| S7 | "`expires_at` ... epoch_ms (milliseconds since epoch, UTC) ... treating it as seconds shifts expiry by three orders of magnitude." | GET .../{id} `expires_at` units |

### Per-client cross-check (exhaustive — every clause × every client)

#### client-go

| Spec clause | client-go behavior (quoted) | Verdict |
|---|---|---|
| S1 (X-Sig header) | "Signs every request and sends the signature in the `X-Signature-V2` header (renamed from the spec header during the v2 migration...)" | **BREAKING** — no `X-Sig` header is ever sent |
| S2 (casing) | "Sends all headers lowercase (`x-signature-v2`, `content-type`)." | Permitted (see Rejected Candidates) — but moot given S1 breach |
| S3 (extension headers) | Sends `X-Signature-V2`, which matches the `X-*` prefix, but as a named-header **replacement** for the required `X-Sig` rather than an *additional* extension header alongside it | N/A as an independent S3 finding — this is covered under BD-1 and RC-5, not a separate extension-header issue |
| S4 (no retry w/o key) | "Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries..." | **BREAKING** |
| S5 (Idempotency-Key required) | Idempotency-Key is never mentioned as sent on any POST /v2/transfers call (initial or retry) | **BREAKING** |
| S6 (status enum) | "Status handling: switch over `pending`, `settled`, `cancelled`, `failed`." | Compliant |
| S7 (expires_at units) | "Parses `expires_at` as epoch milliseconds." | Compliant |

#### client-py

| Spec clause | client-py behavior (quoted) | Verdict |
|---|---|---|
| S1 (X-Sig header) | "Sends the `X-Sig` header exactly as specified." | Compliant |
| S2 (casing) | (sent "exactly as specified" — no casing deviation) | N/A |
| S3 (extension headers) | "Sends an `X-Trace-Id` header on every request for distributed tracing." | Permitted (see Rejected Candidates) |
| S4 (no retry w/o key) | "Never retries POSTs." | Compliant — not retrying trivially satisfies "MUST NOT retry ... unless" |
| S5 (Idempotency-Key required) | "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." | **BREAKING** |
| S6 (status enum) | "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." | **BREAKING** |
| S7 (expires_at units) | "Parses `expires_at` as epoch milliseconds." | Compliant |

#### client-js

| Spec clause | client-js behavior (quoted) | Verdict |
|---|---|---|
| S1 (X-Sig header) | "Sends the `X-Sig` header exactly as specified, plus `X-Trace-Id` for tracing." | Compliant |
| S2 (casing) | (sent "exactly as specified" — no casing deviation) | N/A |
| S3 (extension headers) | "...plus `X-Trace-Id` for tracing." | Permitted (see Rejected Candidates) |
| S4 (no retry w/o key) | "attaches a UUID `Idempotency-Key` on every call and never retries without one." | Compliant |
| S5 (Idempotency-Key required) | "POST /v2/transfers: attaches a UUID `Idempotency-Key` on every call..." | Compliant |
| S6 (status enum) | "Status handling: switch over `pending`, `settled`, `cancelled`, `failed`." | Compliant |
| S7 (expires_at units) | "Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date." | **BREAKING** |

### Consolidated per-client drift table (validator-required format)

| Client | Spec requirement | Client behavior | Breaking or Permitted |
|---|---|---|---|
| client-go | S1: MUST carry `X-Sig` header | Sends `X-Signature-V2` instead of `X-Sig` | **Breaking** |
| client-go | S2: header casing is case-insensitive, any casing allowed | Sends headers lowercase (`x-signature-v2`, `content-type`) | Permitted |
| client-go | S4+S5: `Idempotency-Key` REQUIRED on every POST; MUST NOT retry without it | No idempotency header is attached to retries (fixture-confirmed); retries up to 3× on 5xx anyway — an independently sufficient S4 violation regardless of the unstated initial-request behavior | **Breaking** |
| client-go | S6: status enum `pending/settled/cancelled/failed` | Switches over `pending, settled, cancelled, failed` | Compliant (not a drift) |
| client-go | S7: `expires_at` is epoch_ms | Parses as epoch milliseconds | Compliant (not a drift) |
| client-py | S1: MUST carry `X-Sig` header | Sends `X-Sig` exactly as specified | Compliant (not a drift) |
| client-py | S3: extension headers permitted | Sends `X-Trace-Id` on every request | Permitted |
| client-py | S5: `Idempotency-Key` REQUIRED on every POST | Fires `POST /v2/transfers` without `Idempotency-Key` (only the bulk-endpoint wrapper generates one) | **Breaking** |
| client-py | S4: MUST NOT retry without idempotency key | Never retries POSTs | Compliant (not a drift) |
| client-py | S6: status enum uses British spelling `cancelled`, exact string match expected | Maps via exact string equality against a table using American spelling `canceled` | **Breaking** |
| client-py | S7: `expires_at` is epoch_ms | Parses as epoch milliseconds | Compliant (not a drift) |
| client-js | S1: MUST carry `X-Sig` header | Sends `X-Sig` exactly as specified | Compliant (not a drift) |
| client-js | S3: extension headers permitted | Sends `X-Trace-Id` for tracing | Permitted |
| client-js | S4+S5: `Idempotency-Key` REQUIRED; MUST NOT retry without it | Attaches UUID `Idempotency-Key` on every call; never retries without one | Compliant (not a drift) |
| client-js | S6: status enum `pending/settled/cancelled/failed` | Switches over `pending, settled, cancelled, failed` | Compliant (not a drift) |
| client-js | S7: `expires_at` is epoch_ms | `new Date(res.expires_at * 1000)` — treats value as epoch seconds | **Breaking** |

### Breaking Drift (confirmed findings, with quoted evidence)

**BD-1 — client-go: required `X-Sig` auth header is never sent, a differently-named header is sent instead**
- Spec (S1): `"Every request MUST carry the `X-Sig` header containing an HMAC of the body."`
- client-go: `"Signs every request and sends the signature in the `X-Signature-V2` header (renamed from the spec header during the v2 migration; the old name felt ambiguous)."`
- Why this is breaking, not a permitted casing difference: S2 permits *casing* variation of the same header name ("clients MAY send any casing"); it does not permit renaming to a different header name. `X-Signature-V2` is a distinct string from `X-Sig`, not a case variant of it. A server implementing case-insensitive matching per RFC 9110 would still never see an `X-Sig`/`x-sig`/`X-SIG` header on the wire — it would see only `X-Signature-V2`, which the spec never defines as satisfying the auth requirement. This is a broken contract: request authentication would fail.

**BD-2 — client-go: `Idempotency-Key` confirmed absent on retries, and `POST /v2/transfers` is retried on 5xx without it (independently breaking, regardless of unstated initial-request behavior)**
- Spec (S5): `"`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."`
- Spec (S4): `"clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."`
- client-go: `"Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries because the team understood transfers to be safe to retry on 5xx."`
- Why this is breaking: the fixture never states that client-go attaches `Idempotency-Key` on any `POST /v2/transfers` call, and explicitly states it is absent on retries. This violates S5 unconditionally (header required on every POST) and violates S4's retry-safety rule directly — retries occur on 5xx without the required key, which is exactly the double-transfer risk the spec's MUST NOT clause exists to prevent.

**BD-3 — client-py: `Idempotency-Key` missing on the single-transfer POST path**
- Spec (S5): `"`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."`
- client-py: `"POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."`
- Why this is breaking: S5 has no carve-out for "safe" or non-retried requests — the header is required on *every* `POST /v2/transfers` request, regardless of retry behavior. client-py's own notes confirm the single-transfer path omits it entirely.

**BD-4 — client-py: status enum spelling mismatch breaks exact-match handling of `cancelled`**
- Spec (S6): `"one of: `pending`, `settled`, `cancelled`, `failed`"` and `"The `status` enum uses British spelling `cancelled`."`
- client-py: `"Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."`
- Why this is breaking: this is not merely an internal-naming choice — the fixture states the mapping table "matches on exact string equality against the wire value." The wire value the server sends is `cancelled` (British spelling, per S6). client-py's table key is `canceled` (American spelling, one `l`). Exact string equality between `"cancelled"` (wire) and `"canceled"` (table key) fails, so every transfer that reaches the `cancelled` status will fail to map in client-py — a functional break, not a cosmetic one.

**BD-5 — client-js: `expires_at` double-converted, treated as epoch seconds when the spec defines it as epoch milliseconds**
- Spec (S7): `"`expires_at` | integer | epoch_ms (milliseconds since epoch, UTC)"` and `"treating it as seconds shifts expiry by three orders of magnitude."`
- client-js: `"Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date."`
- Why this is breaking: this is the literal failure mode the spec calls out by name. `res.expires_at` is already epoch_ms per S7; multiplying by 1000 again produces a timestamp roughly 1000× too far in the future, exactly the "shifts expiry by three orders of magnitude" error the spec warns about.

### Rejected Candidates (looked suspicious, but permitted by an explicit spec clause — not findings)

**RC-1 — client-go sends headers in lowercase (`x-signature-v2`, `content-type`)**
- Looked suspicious because: mixed/lowercase casing differs from the `X-Sig`/`Idempotency-Key` casing shown in the spec text, so it could be mistaken for a protocol violation.
- Rejected because: S2 states `"Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."` Casing choice is explicitly permitted. (Note: this rejection covers only the *casing* of client-go's header; the header *name* `X-Signature-V2` itself is a separate, breaking issue — see BD-1. Rejecting the casing candidate does not cure BD-1.)

**RC-2 — client-py sends an `X-Trace-Id` header on every request**
- Looked suspicious because: `X-Trace-Id` is not defined anywhere in `api-spec.md`, so an undefined header could look like non-conformant behavior.
- Rejected because: S3 (section 7, extension clause) states `"Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."` `X-Trace-Id` matches the `X-*` pattern and is explicitly the tracing-header example the spec itself gives.

**RC-3 — client-js sends an `X-Trace-Id` header for tracing**
- Looked suspicious because: same reasoning as RC-2 — an extra, spec-undefined header on every request.
- Rejected because: same clause as RC-2 (S3 / section 7) explicitly permits `X-*` extension headers and names tracing headers as the example.

**RC-4 — client-py never retries `POST /v2/transfers`**
- Looked suspicious because: it diverges from client-go's retry behavior, and "never retries" could be mistaken for missing resiliency / a spec gap.
- Rejected because: the spec only constrains retries negatively — S4 says clients `"MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."` It does not require clients to retry at all. Never retrying trivially satisfies a MUST-NOT-unless clause and does not touch S5 either (S5 governs whether the key is present on requests that are sent, not whether retries happen). No clause is violated.

**RC-5 — could client-go's `X-Signature-V2` header be defended as a permitted `X-*` extension header (S3), making BD-1 a non-finding?**
- Looked suspicious as a possible defense because: `X-Signature-V2` does match the `X-*` prefix pattern that S3 permits.
- Rejected as a defense because: S3 permits *additional* extension headers alongside required ones — it says nothing about a client substituting an extension header *in place of* a required header. S1 independently requires `X-Sig` on every request; client-go's notes describe `X-Signature-V2` as a replacement ("renamed from the spec header"), not an addition. Even if `X-Signature-V2` itself is a permitted extension header to send, that permission does not satisfy S1's separate, unmet requirement to also send `X-Sig`. BD-1 stands.

### Coverage confirmation

All 3 clients × all 7 extracted spec clauses (S1–S7, with S2 and S3 marked N/A where
the client fixture gives no casing/extension-header behavior to evaluate) were
checked above — 21 cells total, all accounted for in the per-client cross-check
tables. No spec clause or client fixture line was left unclassified.

## Review

Independent review was actually executed via a general-purpose sub-agent (Agent
tool, agent id `aca6eca107ffea25b`), used as a fallback because
`Skill(mission-reviewer)` was expected to hit the same tool-execution failure
observed for `mission-planner` (see Assumptions). The reviewer was given the raw
text of all four fixtures and this draft artifact and instructed to (Step A)
independently re-derive breaking/permitted classifications from the four fixtures
alone, before reading the draft's conclusions, then (Step B) diff its own analysis
against the draft and flag anything missed, overclaimed, or mis-scoped, ending in an
explicit verdict line.

Reviewer's independent tally (Step A): the same 5 breaking findings and same 5
permitted/rejected candidates as this draft's BD-1..BD-5 and RC-1..RC-5, derived
without reading the draft first.

Reviewer's diff verdict (Step B): **"VERDICT: draft needs correction"** — one
factual-accuracy fix required, no missed findings, no overclaimed findings, no
mis-scoped rejections:

- The per-client cross-check table's client-go / S3 row originally stated
  `"(no `X-*` extension headers mentioned in client-go.md)"` / `"N/A — client sends
  none"`. The reviewer correctly flagged this as factually wrong: `client-go.md`
  does mention `X-Signature-V2`, which matches the `X-*` prefix. **Fix applied**:
  the row now reads "Sends `X-Signature-V2`, which matches the `X-*` prefix, but as
  a named-header replacement for the required `X-Sig` rather than an additional
  extension header alongside it" / "N/A as an independent S3 finding — covered
  under BD-1 and RC-5, not a separate extension-header issue." This is a wording/
  table-accuracy correction; it does not change any breaking-finding or
  rejected-candidate verdict, both of which the reviewer confirmed independently.
- Reviewer explicitly confirmed BD-2's hedged wording ("the fixture never states
  that client-go attaches `Idempotency-Key` on any call") is correctly scoped to
  what the fixture supports, and confirmed RC-5 (the extension-header defense for
  client-go's renamed header) is correctly rejected, since S3 permits *additional*
  headers, not substitutions for required ones.

No High-severity or unresolved findings remain open after the fix above was
applied.

### Phase 4 — formal 3-reviewer scoring pass (mission-review/1)

Following the exploratory review above, a formal Phase 4 pass was run per the
Complex-tier requirement of 3 independent reviewers, each producing a
`mission-review/1` JSON with per-axis scores and findings (fallback: `Agent` tool
general-purpose sub-agents, used for the same reason documented in Assumptions —
`Skill(mission-reviewer)` was not exercised because the earlier `Skill()` calls in
this run failed with a tool-execution error).

- Reviewer `correctness` (agent `a80cab9abeeb51a07`): 1 Low finding — the
  consolidated drift table's client-go row said "No idempotency header on initial
  or retried POST /v2/transfers," which slightly overstates what the fixture
  confirms about the *initial* request (only the retry-absence is fixture-stated).
  Scores: mission_achievement 5.0, accuracy 4.7, completeness 5.0, usability 5.0.
- Reviewer `completeness` (agent `a650a12a2e8d6d571`): same Low finding,
  independently derived. Scores: mission_achievement 5.0, accuracy 4.7,
  completeness 5.0, usability 5.0.
- Reviewer `usability` (agent `a65ba3e903512ba94`): 2 Low findings — the same
  table-wording issue, plus BD-2's original title ("Idempotency-Key never
  supplied") asserting more certainty about the initial request than the fixture
  or the artifact's own Assumption 3 support. Scores: mission_achievement 5.0,
  accuracy 4.7, completeness 5.0, usability 4.7.

All three reviewers converged on the same underlying wording issue (no reviewer
found a distinct, unrelated defect; no High or Medium findings from any reviewer).
**Fix applied**: the consolidated table row and BD-2's title were both reworded
(see diffs applied above) to state only what the fixture confirms — absence of
`Idempotency-Key` on retries — and to frame the retry-without-key behavior as an
independently sufficient S4 violation, rather than asserting the initial-request
key is confirmed absent. This directly resolves the wording basis for all 4 Low
findings raised across the 3 reviewers.

## Score

Machine-computed via `mission-state.py aggregate-reviews` (3 independent
`mission-review/1` reviewer JSONs, min-reviewers gate satisfied) and recorded by
`mission-state.py push-score --scoring-json` in
`.mission-state/sessions/cc-7ad05375-ede5-4d87-99de-b4f743443954.json`
`score_history` (iteration 1, `score_source: "scoring-json"`):

| Axis | Score (0-5) | Basis |
|---|---|---|
| mission_achievement | 5.0 | All 3 reviewers: full drift audit with correct breaking-vs-permitted separation, no findings on this axis |
| accuracy | 4.7 | All 3 reviewers independently found the same 1 Low finding (client-go Idempotency-Key wording overclaimed initial-request behavior); capped 5.0 → 4.7 per rubric ("1 Low finding" cap = 4.7) |
| completeness | 5.0 | No reviewer found any omitted spec clause or client behavior across the 3×7 cross-check |
| usability | 4.9 | Reviewer `usability` found 2 Low findings (same root wording issue counted twice, once per affected axis) capping its own score to 4.7; averaged with the other two reviewers' 5.0 → aggregate 4.9 |

`review_agreement`: **5.0** (max per-axis delta across reviewers = 0.3, on
usability — well under the 0.5 threshold for the top band).
`open_high`: **0**. `findings_evidence_path`:
`.mission-state/archive/iter-1-ecc1abae-reviews.json`.

**Composite (mean of the 4 axes): 4.9 / 5.0.** Threshold is 4.0; minimum scored
axis (accuracy, 4.7) clears the 3.5 cutoff.

All 4 Low findings raised by the 3 reviewers pointed at the same underlying
wording issue (the consolidated drift table and the BD-2 title overstated what the
fixture confirms about client-go's *initial* `POST /v2/transfers` request). That
wording was corrected in both locations after scoring (see `Review`); the score
above is the pre-fix reviewer score, so it is a conservative (not inflated)
measurement — the corrected artifact should if anything score at or above this.

## Stop Decision

**Decision: complete — `mission-state.py mark-passes` returned `{"ok": true,
"passes": true, "forced": false}`.** Verified via `mission-state.py next`
immediately after: `{"next_action": "report-complete", ..., "phase": "done",
"loop_active": false, "passes": true}`.

Pass gate, all conditions independently confirmed rather than asserted:
- `findings_evidence_path` exists (`.mission-state/archive/iter-1-ecc1abae-reviews.json`).
- `open_high == 0` (0 High findings across all 3 reviewers).
- `max_agreement_delta == 0.3 <= 1.5`.
- `composite_score == 4.9 >= threshold 4.0`.
- `min(scored axes) == 4.7 >= 3.5`.
- All validator-required sections present: per-client drift table, breaking-drift
  section with quoted evidence, rejected-candidates section citing permitting
  clauses (S2 for casing, S3/section 7 for extension headers).
- Coverage exhaustive: 3 clients × 7 spec clauses (21 cells) all classified,
  including fully-compliant cells.
- Specialist checkpoint closed: `dev-performance-reviewer` was auto-selected by
  `task_profile.primary=backend` matching but logged `status: "skipped"` via
  `specialists log-invocation` (out of scope for a contract-correctness audit,
  not a performance review). `specialists accounting` confirms
  `unaccounted_candidates: []`.

Deviation from the nominal `/mission` path, disclosed rather than concealed:
`Skill(mission-planner)` was invoked twice and both times returned the literal
string `<error>Execute skill: mission-planner</error>` with no plan body — a
tool-execution failure in this sandbox (see Assumptions). Planning was applied
inline by the orchestrator instead of via the Skill tool. Independent review
(Phase 4) was likewise run via `Agent`-tool general-purpose sub-agents rather than
`Skill(mission-reviewer)`, pre-emptively, since the same degradation was expected.
Everything downstream of review — `aggregate-reviews`, `push-score
--scoring-json`, `mark-passes`, `next` — executed through the actual
`mission-state.py` CLI exactly as the standard mission pipeline specifies, and
the outputs quoted above are copied verbatim from those command results, not
estimated.

## Evidence

All evidence quotes below are copied verbatim from the four in-scope fixtures.

**From `api-spec.md`:**
- `"Every request MUST carry the `X-Sig` header containing an HMAC of the body."`
- `"Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."`
- `"Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."`
- `"This endpoint is NOT idempotent by itself: clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every POST /v2/transfers request."`
- `"one of: `pending`, `settled`, `cancelled`, `failed`"`
- `"The `status` enum uses British spelling `cancelled`. The `expires_at` field is always epoch_ms; treating it as seconds shifts expiry by three orders of magnitude."`

**From `client-go.md`:**
- `"Signs every request and sends the signature in the `X-Signature-V2` header (renamed from the spec header during the v2 migration; the old name felt ambiguous)."`
- `"Sends all headers lowercase (`x-signature-v2`, `content-type`)."`
- `"Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries because the team understood transfers to be safe to retry on 5xx."`
- `"Parses `expires_at` as epoch milliseconds."`
- `"Status handling: switch over `pending`, `settled`, `cancelled`, `failed`."`

**From `client-py.md`:**
- `"Sends the `X-Sig` header exactly as specified."`
- `"POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."`
- `"Never retries POSTs."`
- `"Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."`
- `"Parses `expires_at` as epoch milliseconds."`
- `"Sends an `X-Trace-Id` header on every request for distributed tracing."`

**From `client-js.md`:**
- `"Sends the `X-Sig` header exactly as specified, plus `X-Trace-Id` for tracing."`
- `"POST /v2/transfers: attaches a UUID `Idempotency-Key` on every call and never retries without one."`
- `"Status handling: switch over `pending`, `settled`, `cancelled`, `failed`."`
- `"Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date."`

## Assumptions

1. **Skill tool degradation (observed twice, not inferred).** `Skill(mission-planner,
   ...)` returned the literal string `<error>Execute skill: mission-planner</error>`
   on both attempts, with no plan content. Per the mission Compact Instructions'
   documented fallback for environments without a working Skill tool ("Codex では
   Skill tool が無い場合、該当 skill 指示を同一コンテキストで適用"), the
   orchestrator applied the mission-planner instructions inline in this same
   context rather than retrying further (2 identical failures were treated as
   sufficient evidence the call path is broken in this sandbox, not as grounds to
   burn budget on more retries). The same degradation was assumed likely for
   `mission-reviewer` and pre-empted by using the general-purpose `Agent` tool for
   the independent review pass instead (see `Review`). Full detail logged in
   `.mission-state/sessions/cc-7ad05375-ede5-4d87-99de-b4f743443954-assumptions.md`.
2. **Scope boundary.** Only the four named fixture files were read. No other file
   under `benchmarks/mission-vs-goal/` (task definitions, scoring configuration,
   answer keys, or any other metadata) was opened, read, grepped, or listed, per the
   task's explicit out-of-bounds rule. This artifact's own conclusions were derived
   solely from the quoted fixture text above, not from any external scoring
   reference.
3. **client-go's first-attempt Idempotency-Key behavior is unstated, not assumed
   present or absent.** The client-go fixture only explicitly states the key is
   missing *on retries*; it never explicitly confirms or denies whether the very
   first `POST /v2/transfers` attempt carries one. BD-2's wording (after the review
   fix) claims only what the fixture supports: the header is absent on retries
   (an independently sufficient S4 violation) and never affirmatively described as
   present anywhere, rather than asserting with certainty that it is omitted on
   every single call.
4. **"Breaking" is scoped to contract-level behavior, not implementation quality.**
   A difference is classified as breaking only when it violates a MUST/MUST NOT/
   REQUIRED clause in the spec (auth header identity, idempotency-key presence/
   retry safety, status enum values, `expires_at` units) — not for style or
   internal-naming choices alone (e.g., a client's internal enum names would not by
   themselves be a finding if its wire-level matching still worked correctly; the
   client-py status finding is breaking specifically because the fixture states the
   mismatched internal name is used for *exact-match comparison against the wire
   value*, not merely as an internal label).
