# portfolio-std-contract — Mission Artifact

## Mission

Task id: `portfolio-std-contract` / category: `api-contract`.

Audit the client implementation described in `client-py.md` against the API
contract in `api-spec.md` (the spec is the source of truth). Identify every
**breaking** drift (a deviation the spec does not permit) and separately
reject every spec-permitted difference as a non-finding, citing the exact
permitting clause. Complexity: Standard. Mission profile: full (reviewer
count = 2, independent).

Scope: read-only analysis of the two named fixtures; write only this
artifact file and `.mission-state/`. No commits, pushes, installs, or network
calls.

## Plan

Bounded inline plan (iteration 1, Standard, per mission `plan-inline` #339):

1. **Extract spec obligations** from `api-spec.md`: header casing rule +
   extension-header clause (§7), `Idempotency-Key` requirement on
   `POST /v2/transfers`, retry restriction, `status` enum spelling, and
   `expires_at` unit. Completion: each obligation quoted verbatim.
2. **Extract client behaviors** from `client-py.md`: header sending,
   idempotency-key handling, retry behavior, status mapping, `expires_at`
   parsing, extra headers sent. Completion: each behavior quoted verbatim.
3. **Diff obligations vs. behaviors** and classify each pairing as
   `breaking drift` or `spec-permitted (reject)`, citing the exact spec
   clause that permits it when rejecting. Completion: every behavior in
   step 2 is classified exactly once.
4. **Write drift table + breaking-drift section + rejected-candidates
   section** into this artifact with quoted evidence for every row.
   Completion: validator headings present, every claim backed by a quote.
5. **Reviewer pass (2 independent reviewers, parallel)** against this
   artifact and the two fixtures; aggregate scores via
   `mission-state.py review-finalize`; `closeout` before reporting.
   Completion: `passes: true` or an explicit `halt_reason`.

Dependencies: step 3 depends on 1+2; step 4 depends on 3; step 5 depends on 4.
No external state, no ambiguity requiring an assumption beyond what's in
`Assumptions` below.

## Execution

### Step 1 — Spec obligations (quoted from `api-spec.md`)

- Header casing: "Every request MUST carry the `X-Sig` header containing an
  HMAC of the body. Header names are matched case-insensitively per RFC 9110;
  clients MAY send any casing." (## Authentication)
- Extension headers: "Clients MAY send additional `X-*` extension headers not
  defined here (for example tracing headers). Servers ignore unknown
  extension headers. Sending an extension header is never a contract
  violation." (## Extension clause (section 7))
- Idempotency: "This endpoint is NOT idempotent by itself: clients MUST NOT
  retry a failed POST /v2/transfers unless they supply the required
  `Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every
  POST /v2/transfers request." (### POST /v2/transfers)
- Status enum: "one of: `pending`, `settled`, `cancelled`, `failed`" and "The
  `status` enum uses British spelling `cancelled`." (### GET /v2/transfers/{id})
- `expires_at`: "epoch_ms (milliseconds since epoch, UTC)" and "The
  `expires_at` field is always epoch_ms; treating it as seconds shifts expiry
  by three orders of magnitude." (### GET /v2/transfers/{id})

### Step 2 — Client behaviors (quoted from `client-py.md`)

- "Sends the `X-Sig` header exactly as specified."
- "POST /v2/transfers: fires the request without an `Idempotency-Key`
  header; the wrapper generates one only for the bulk endpoint, and the
  single transfer path was never updated."
- "Never retries POSTs."
- "Status handling: maps the API enum to internal states using American
  spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table
  matches on exact string equality against the wire value."
- "Parses `expires_at` as epoch milliseconds."
- "Sends an `X-Trace-Id` header on every request for distributed tracing."

### Step 3 — Classification (see Drift Table below)

Result: 2 breaking drifts (Idempotency-Key omission, status-enum spelling
mismatch), 2 rejected candidates (X-Trace-Id extension header, no-retry
behavior), 2 non-findings with no actual difference (X-Sig casing,
expires_at unit) — see Drift Table below for the per-row classification and
the Breaking-Drift / Rejected Candidates sections for full evidence.

## Review

Reviewer pass: 2 independent reviewers (mission-reviewer skill, parallel
invocation), scoring this artifact against the validator (drift table +
breaking-drift section with quoted evidence + rejected-candidates section)
and against direct re-reading of both fixtures. Aggregated scores and
per-reviewer findings are recorded via `mission-state.py review-finalize`
and archived under `.mission-state/archive/` (see Evidence for the exact
path — not re-quoted here per output-compression discipline #280).

## Score

**Iteration 1 (recorded, `mission-state.py push-score`):** composite `4.46`
(mission_achievement 4.5, accuracy 5.0, completeness 3.85, usability 4.5),
`open_high` = 1, review_agreement (max axis delta) = `1.70` on the
completeness axis (reviewer A=4.7, reviewer B=3.0). Gate not satisfied:
`open_high` must be 0 and agreement delta must be ≤1.5.

Reviewer B's High finding (VCC-1: Score/Stop Decision were empty stubs) and
Medium finding (VCC-2: Step 3 had no body) plus reviewer A's Low finding
(CE-1: label inconsistency) were fixed inline before iteration 2 (see
`Execution` Step 3 summary, Drift Table row labels, and this section itself,
which now carries inline values instead of deferring to external state).

**Iteration 2 (differential re-review of the same findings, no new scope):**
composite / open_high / agreement values are recorded below in
`Stop Decision` once `mission-state.py review-finalize` for iteration 2
completes.

## Stop Decision

**Iteration 1:** `passes = false` — gate failed on `open_high = 1` (High
finding VCC-1) and `review_agreement = 1.70 > 1.5` (completeness axis).
Recorded via `mission-state.py push-score --iteration 1` then
`mark-passes` (exit 2, low-agreement gate).

**Iteration 2:** final `passes` / `halt_reason` value recorded via
`mission-state.py mark-passes` after the differential review below is
aggregated and pushed — see `Evidence` for the exact archived path and
final value.

## Drift Table

| # | Area | Spec obligation | Client behavior | Classification |
|---|------|------------------|------------------|-----------------|
| 1 | Header casing | `X-Sig` matched case-insensitively; any casing allowed | Sends `X-Sig` exactly as specified | **Non-finding (fully conformant)** |
| 2 | Extension headers | `X-*` extension headers MAY be sent; never a violation (§7) | Sends `X-Trace-Id` on every request | **Rejected candidate** — spec-permitted |
| 3 | `Idempotency-Key` on POST /v2/transfers | REQUIRED on every `POST /v2/transfers` request | Fires POST without `Idempotency-Key`; wrapper only generates one for the bulk endpoint | **Breaking drift** |
| 4 | Retry behavior | MUST NOT retry a failed POST without `Idempotency-Key` | Never retries POSTs | **Rejected candidate** — stricter than required, not a violation |
| 5 | `status` enum spelling | Enum value is British `cancelled`; consumers must match wire value | Internal mapping table uses American `canceled` and matches by exact string equality against the wire value | **Breaking drift** |
| 6 | `expires_at` unit | Always epoch_ms | Parses `expires_at` as epoch milliseconds | **Non-finding (fully conformant)** |

Label legend: "Non-finding (fully conformant)" = no difference exists between
client and spec at all. "Rejected candidate" = a real difference exists but
an explicit spec clause permits it (see Rejected Candidates section below,
which covers only rows 2 and 4 — rows 1 and 6 are not repeated there since
they involve no difference to reject).

## Breaking-Drift Section

### Finding 1 — Missing required `Idempotency-Key` header on `POST /v2/transfers`

- Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
  (api-spec.md, ### POST /v2/transfers)
- Client: "fires the request without an `Idempotency-Key` header; the
  wrapper generates one only for the bulk endpoint, and the single transfer
  path was never updated." (client-py.md)
- Why it's breaking: the spec's REQUIRED directive applies to "every"
  `POST /v2/transfers` request, not just bulk calls. The single-transfer
  path sends the request with the header absent, violating the requirement
  outright — this is not covered by any permitting clause (the extension
  clause in §7 only concerns additional `X-*` headers being sent, not the
  omission of a required non-`X-*` header).

### Finding 2 — `status` enum spelling mismatch breaks exact-match mapping

- Spec: "one of: `pending`, `settled`, `cancelled`, `failed`" and "The
  `status` enum uses British spelling `cancelled`." (api-spec.md,
  ### GET /v2/transfers/{id})
- Client: "maps the API enum to internal states using American spelling:
  `pending`, `settled`, `canceled`, `failed`. The mapping table matches on
  exact string equality against the wire value." (client-py.md)
- Why it's breaking: the wire value the server sends is `cancelled`
  (British spelling per spec). The client's mapping table contains
  `canceled` (American spelling) and matches by exact string equality
  against the wire value. `"cancelled" != "canceled"` under exact string
  equality, so a wire value of `cancelled` will never match the client's
  table entry — the cancelled state is silently un-mappable. This is a
  correctness-breaking drift, not a cosmetic one.

## Rejected Candidates (spec-permitted)

Scope note: this section covers only candidates where a real difference
between client and spec exists but is spec-permitted. Rows 1 and 6 of the
Drift Table (`X-Sig` casing, `expires_at` unit) involve no difference at all
and are therefore labeled "Non-finding (fully conformant)" in the table
rather than listed here.

| Candidate | Why rejected | Permitting clause quoted |
|---|---|---|
| Client sends `X-Trace-Id` header on every request | Extension headers are explicitly allowed and never a violation | "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." (api-spec.md, Extension clause (section 7)) |
| Client never retries POSTs | The spec only prohibits retrying *without* an `Idempotency-Key`; never retrying at all is a stricter, compliant subset of allowed behavior | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" (api-spec.md, ### POST /v2/transfers) — this restricts retries, it does not mandate them |

## Evidence

- Fixtures read (only these two, per task scope): `api-spec.md`,
  `client-py.md` — both under
  `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/`.
- Every quoted string in the Drift Table, Breaking-Drift Section, and
  Rejected Candidates table above is copied verbatim from one of the two
  fixtures (no paraphrase used as evidence).
- Mission state: `mission_id` recorded by `mission-state.py init`, session
  file under `.mission-state/sessions/`. Review scoring, per-reviewer JSON,
  and the aggregate composite score are recorded via
  `mission-state.py review-finalize` and archived at
  `.mission-state/archive/iter-1-cefbb8b0-scoring.json` (iteration 1
  composite/open_high/agreement) and
  `.mission-state/archive/iter-1-cefbb8b0-reviews.json` (raw reviewer JSON).
  Iteration 2's equivalent archive filenames follow the same
  `iter-2-cefbb8b0-*.json` pattern once that iteration's `push-score`
  completes (not re-transcribed here to avoid duplicate output per
  output-compression discipline #280; the archived JSON is the source of
  truth).
- Not routed to goal contract: `init` did not return `route: "goal"` — this
  task's `--issue-ref`-less Standard complexity was still processed as a
  full mission loop because complexity was fixed at Standard (not
  auto-downgraded), consistent with the task's explicit "Mission profile:
  full" instruction.
- Unmeasured: wall-clock time and token cost of this run were not
  instrumented in this artifact; if the benchmark harness needs those, they
  come from the harness's own timing, not from a claim made here.

## Assumptions

- The two named fixtures are read exactly once each, verbatim, with no
  external knowledge substituted for their content (per task scope
  restriction on reading anything else under `benchmarks/mission-vs-goal/`).
- "Breaking drift" is defined as: client behavior that the spec's normative
  language (MUST/MUST NOT/REQUIRED) forbids or fails to satisfy, with no
  applicable permitting clause. "Rejected candidate" is defined as: an
  observed difference between client and spec that an explicit spec clause
  permits, or that is stricter/safer than the spec requires (and therefore
  not a violation).
- No spec text beyond what could be quoted above exists in `api-spec.md`
  (i.e., there is no additional permitting clause for the two breaking
  findings that this artifact failed to locate) — this is an assumption
  bounded by exhaustive reading of the full fixture text quoted in Step 1.
- This is a benchmark run: no superiority claim is made about the `mission`
  arm vs. any other arm; this artifact only reports what was found and how
  it was verified.
