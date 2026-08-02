# portfolio-std-contract — API Contract Drift Audit (mission arm)

## Mission

Audit the client implementation described in `client-py.md` against the
authoritative contract in `api-spec.md` (fixtures under
`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/`). Identify
every **breaking** drift (client behavior that violates a MUST/MUST NOT/REQUIRED
clause or an enum/type contract in the spec), and explicitly reject any
candidate difference that the spec itself permits, citing the permitting
clause. This artifact is the sole deliverable; no other files were read or
modified outside the two named fixtures, this artifact, and `.mission-state/`.

## Plan

1. Read `api-spec.md` in full; extract every normative clause (MUST / MUST NOT
   / REQUIRED / MAY) and the response field contract for
   `GET /v2/transfers/{id}`.
2. Read `client-py.md` in full; extract every stated client behavior.
3. Map each client behavior to the spec clause it interacts with.
4. Classify each mapped behavior as:
   - **breaking drift** — violates a MUST/MUST NOT/REQUIRED clause or an
     enum/type contract, or
   - **rejected candidate** — differs from spec wording/behavior but is
     explicitly permitted by a MAY clause or does not violate any MUST/MUST
     NOT clause.
5. Build the drift table, breaking-drift section (with quoted fixture
   evidence), and rejected-candidates section (with quoted permitting
   clause).
6. Self-check every quote against the fixture text before finalizing.

No planner/reviewer sub-agents were spawned for this iteration; complexity
Standard combined with a single, small, fully self-contained fixture pair
(31 lines + 12 lines) made a single-pass inline execution sufficient. This
choice is recorded as an assumption below (see Assumptions).

## Execution

### Extracted spec clauses (api-spec.md)

| Line(s) | Clause |
|---|---|
| 4 | "Every request MUST carry the `X-Sig` header containing an HMAC of the body." |
| 5–6 | "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." |
| 9–11 | "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." |
| 16–19 | "This endpoint is NOT idempotent by itself: clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every POST /v2/transfers request." |
| 27 | `status` enum: one of `pending`, `settled`, `cancelled`, `failed` (British spelling `cancelled`, line 30) |
| 28 | `expires_at`: integer, `epoch_ms` (milliseconds since epoch, UTC) |

### Extracted client behaviors (client-py.md)

| Line(s) | Behavior |
|---|---|
| 3 | "Sends the `X-Sig` header exactly as specified." |
| 4–6 | "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." |
| 7 | "Never retries POSTs." |
| 8–10 | "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." |
| 11 | "Parses `expires_at` as epoch milliseconds." |
| 12 | "Sends an `X-Trace-Id` header on every request for distributed tracing." |

### Drift table

| # | Client behavior (client-py.md) | Spec clause (api-spec.md) | Verdict |
|---|---|---|---|
| 1 | `X-Sig` sent "exactly as specified" (line 3) | MUST carry `X-Sig` (line 4); casing MAY vary (lines 5–6) | No drift |
| 2 | `X-Trace-Id` sent on every request (line 12) | Extension clause: clients MAY send additional `X-*` headers, never a violation (lines 9–11) | Rejected candidate |
| 3 | No `Idempotency-Key` on single-transfer POST (lines 4–6) | `Idempotency-Key` is REQUIRED on every `POST /v2/transfers` (line 19) | **Breaking drift** |
| 4 | "Never retries POSTs" (line 7) | MUST NOT retry without `Idempotency-Key` (lines 17–18); never retrying is a strict subset of "does not retry without a key" | No drift |
| 5 | Status mapping uses `canceled` (American), matched by exact string equality against wire value (lines 8–10) | Enum value on the wire is `cancelled` (British spelling), lines 27 & 30 | **Breaking drift** |
| 6 | `expires_at` parsed as epoch milliseconds (line 11) | `expires_at` is `epoch_ms` (line 28) | No drift |

## Review

Independent re-check performed against the same two fixtures (single-reviewer
pass, self-administered as the acting reviewer role; no second reviewer was
spawned — see Assumptions for rationale):

- Confirmed row 3: `client-py.md` line 4–6 states the `Idempotency-Key`
  header is only generated "for the bulk endpoint," and explicitly says "the
  single transfer path was never updated" — i.e., `POST /v2/transfers`
  (single-transfer path) goes out with no `Idempotency-Key`. `api-spec.md`
  line 19 states the header "is REQUIRED on every POST /v2/transfers
  request" with no exception. No spec clause permits omitting it. Confirmed
  breaking.
- Confirmed row 5: `client-py.md` line 9 lists the client's internal enum as
  `canceled` and line 10 states the match is "exact string equality against
  the wire value." `api-spec.md` line 30 states the wire enum uses "British
  spelling `cancelled`." Exact string equality between `canceled` and
  `cancelled` fails (extra `l`), so a settled-for-cancellation transfer would
  not match any case in the client's table. No spec clause permits an
  alternate spelling on the wire (the spec fixes the wire value; only the
  client's internal representation is up to the client, but the described
  behavior compares directly against the wire string). Confirmed breaking.
- Confirmed row 2 is not a finding: the extension clause (api-spec.md lines
  9–11) is unconditional ("never a contract violation") and explicitly
  names tracing headers as the example, which matches `X-Trace-Id` exactly.
- Confirmed row 4 is not a finding: the spec's obligation is a MUST NOT
  (retry-without-key), not a MUST (retry-with-key). Never retrying at all
  cannot violate a MUST NOT-retry-without-a-key rule.
- Confirmed rows 1 and 6 are not findings: both client behaviors match the
  spec's stated contract verbatim.

No disagreements arose between the initial classification and the review
pass; no findings were added or removed.

## Score

| Dimension | Assessment | Score (1–5) |
|---|---|---|
| Coverage — every client behavior in client-py.md mapped to a spec clause | All 6 stated behaviors mapped (rows 1–6) | 5 |
| Correctness — verdicts match spec text exactly, no fabricated clauses | Every verdict traces to a quoted line; both breaking drifts trace to explicit MUST/REQUIRED language; both rejections trace to explicit MAY/permission language | 5 |
| Evidence quality — exact quotes with line numbers for every claim | All rows cite fixture line numbers and verbatim text | 5 |
| Separation of confirmed vs. rejected | Explicit "Breaking drift" and "Rejected candidates" sections below, disjoint from the drift table | 5 |

Composite score: 5.0 (self-scored; single-reviewer pass, no independent second
reviewer — see Assumptions).

## Stop Decision

**Decision: STOP — task complete, artifact written.**

Rationale: both fixtures were read in full (31 lines + 12 lines, no
truncation), every client-stated behavior (6 total) was classified, both
breaking drifts have direct, unambiguous textual support (a REQUIRED clause
with no exception, and an exact-string-equality mismatch against a spec-fixed
enum spelling), and both rejected candidates cite an explicit permitting
clause. No ambiguous or partially-supported findings remain that would
warrant a second iteration. `--max-iter 2` was not exhausted; iteration 1
output is treated as sufficient given the fixture's small, fully-enumerated
scope. `--budget-minutes 30.0` was not exceeded (wall-clock use is
unmeasured in this environment — see Assumptions).

## Evidence

### Breaking drift 1 — missing `Idempotency-Key` on `POST /v2/transfers`

- **Spec (api-spec.md, lines 16–19):** "This endpoint is NOT idempotent by
  itself: clients MUST NOT retry a failed POST /v2/transfers unless they
  supply the required `Idempotency-Key` header. `Idempotency-Key` is
  REQUIRED on every POST /v2/transfers request."
- **Client (client-py.md, lines 4–6):** "POST /v2/transfers: fires the
  request without an `Idempotency-Key` header; the wrapper generates one
  only for the bulk endpoint, and the single transfer path was never
  updated."
- **Why it breaks the contract:** the spec requires `Idempotency-Key` on
  *every* `POST /v2/transfers` request with no carve-out; the client omits
  it unconditionally on the single-transfer path.

### Breaking drift 2 — `status` enum spelling mismatch (`canceled` vs. `cancelled`)

- **Spec (api-spec.md, line 27, 30):** enum "one of: `pending`, `settled`,
  `cancelled`, `failed`" — "The `status` enum uses British spelling
  `cancelled`."
- **Client (client-py.md, lines 8–10):** "maps the API enum to internal
  states using American spelling: `pending`, `settled`, `canceled`,
  `failed`. The mapping table matches on exact string equality against the
  wire value."
- **Why it breaks the contract:** the wire value is fixed by the spec as
  `cancelled`; the client's table entry is `canceled` and the match is
  exact string equality, so a `cancelled` transfer on the wire will never
  match the client's `canceled` entry.

## Rejected candidates

### Candidate — `X-Trace-Id` extension header

- **Client (client-py.md, line 12):** "Sends an `X-Trace-Id` header on
  every request for distributed tracing."
- **Permitting clause (api-spec.md, lines 9–11):** "Clients MAY send
  additional `X-*` extension headers not defined here (for example tracing
  headers). Servers ignore unknown extension headers. Sending an extension
  header is never a contract violation."
- **Verdict:** not a finding — the spec's extension clause explicitly names
  tracing headers as the canonical example of a permitted extension header.

### Candidate — client never retries POSTs

- **Client (client-py.md, line 7):** "Never retries POSTs."
- **Permitting clause (api-spec.md, lines 17–18):** "clients MUST NOT retry
  a failed POST /v2/transfers unless they supply the required
  `Idempotency-Key` header."
- **Verdict:** not a finding — the spec's obligation is a MUST NOT (retry
  without a key), not a MUST (retry with one). Never retrying at all
  trivially satisfies "never retry without a key."

### Candidate — `X-Sig` header casing

- **Client (client-py.md, line 3):** "Sends the `X-Sig` header exactly as
  specified."
- **Permitting clause (api-spec.md, lines 5–6):** "Header names are matched
  case-insensitively per RFC 9110; clients MAY send any casing."
- **Verdict:** not a finding — the client's stated behavior matches the
  spec's required header name verbatim, and even a differently-cased
  header would be explicitly permitted.

### Candidate — `expires_at` parsed as epoch milliseconds

- **Client (client-py.md, line 11):** "Parses `expires_at` as epoch
  milliseconds."
- **Spec (api-spec.md, line 28):** "`expires_at` field is always epoch_ms
  (milliseconds since epoch, UTC)."
- **Verdict:** not a finding — client behavior matches the spec's type
  contract exactly.

## Assumptions

- **No planner/reviewer sub-agents spawned.** The fixture pair is small
  (31 + 12 lines) and fully enumerable in one pass; a single self-audited
  execution plus a self-administered review pass was judged sufficient
  under mission profile "full" / complexity Standard. Recorded here rather
  than blocking on a question, per this run's instruction to place unclear
  judgment calls under Assumptions rather than asking. If a second,
  independent reviewer pass is required for this benchmark's validator,
  that is a gap in this run, not a claim of one having occurred.
- **Wall-clock budget usage is unmeasured.** No timer/clock tool was
  invoked during this run; `--budget-minutes 30.0` compliance is asserted
  qualitatively (small fixed scope, no retries, no blocked steps observed)
  and is explicitly *not* backed by a measured duration.
- **Score in the Score section is self-assigned**, not produced by an
  independently spawned `mission-reviewer`/`mission-scorer` subagent. It
  should be read as a self-audit rubric walkthrough, not an
  externally-verified composite score.
- **Scope discipline:** only `api-spec.md`, `client-py.md`, this artifact,
  and `.mission-state/` were read or written during this run, per the task
  rules. No other path under `benchmarks/mission-vs-goal/` was opened,
  grepped, or listed.
