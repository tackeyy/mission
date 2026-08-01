# disc-contract-drift — Client vs. API Spec Contract Audit

## Goal
The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-02-discriminating-v3/disc-contract-drift-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: audit three client implementations (`client-go.md`, `client-py.md`, `client-js.md`) against
`api-spec.md` ("Transfer API Specification v2"), find every breaking drift, and reject any
difference that the spec explicitly permits (extension headers, case-insensitive header matching),
citing the permitting clause for each rejection.

## Result

### Per-client drift table

| Client | Spec requirement | Client behavior | Breaking or Permitted |
|---|---|---|---|
| client-go | Auth header must be `X-Sig` (any casing) | Sends `X-Signature-V2` instead of `X-Sig` | **Breaking** |
| client-go | Header casing is matched case-insensitively; clients MAY send any casing | Sends all headers lowercase (`x-signature-v2`, `content-type`) | Permitted |
| client-go | `Idempotency-Key` REQUIRED on every `POST /v2/transfers`; MUST NOT retry a failed POST unless it supplies `Idempotency-Key` | Retries `POST /v2/transfers` up to 3× on 5xx with no `Idempotency-Key` attached to retries | **Breaking** |
| client-go | `expires_at` is epoch_ms | Parses `expires_at` as epoch milliseconds | Fully compliant |
| client-go | `status` enum: `pending`, `settled`, `cancelled`, `failed` (British spelling) | Switches over `pending`, `settled`, `cancelled`, `failed` | Fully compliant |
| client-py | Auth header must be `X-Sig` (any casing) | Sends `X-Sig` exactly as specified | Fully compliant |
| client-py | `Idempotency-Key` REQUIRED on every `POST /v2/transfers` | Fires `POST /v2/transfers` without an `Idempotency-Key` header (only generated for the bulk endpoint) | **Breaking** |
| client-py | MUST NOT retry a failed `POST /v2/transfers` unless `Idempotency-Key` supplied | Never retries POSTs | Fully compliant (vacuously — no retry ever occurs) |
| client-py | `status` enum wire value uses British spelling `cancelled` | Internal mapping table keys on American spelling `canceled`, matched by exact string equality against the wire value | **Breaking** |
| client-py | `expires_at` is epoch_ms | Parses `expires_at` as epoch milliseconds | Fully compliant |
| client-py | Extension clause (section 7): clients MAY send additional `X-*` extension headers; never a violation | Sends `X-Trace-Id` on every request | Permitted |
| client-js | Auth header must be `X-Sig` (any casing) | Sends `X-Sig` exactly as specified | Fully compliant |
| client-js | Extension clause (section 7): clients MAY send additional `X-*` extension headers; never a violation | Sends `X-Trace-Id` for tracing | Permitted |
| client-js | `Idempotency-Key` REQUIRED on every `POST /v2/transfers`; MUST NOT retry without it | Attaches a UUID `Idempotency-Key` on every call and never retries without one | Fully compliant |
| client-js | `status` enum: `pending`, `settled`, `cancelled`, `failed` | Switches over `pending`, `settled`, `cancelled`, `failed` | Fully compliant |
| client-js | `expires_at` is epoch_ms (never seconds) | `new Date(res.expires_at * 1000)` — treats the value as epoch seconds and multiplies by 1000 | **Breaking** |

### Summary of breaking drift by client
- **client-go**: 2 breaking findings (auth header renamed; retries without required idempotency key).
- **client-py**: 2 breaking findings (missing idempotency key on POST; enum spelling mismatch breaks status matching).
- **client-js**: 1 breaking finding (`expires_at` unit misinterpretation).

## Evidence

### Breaking drift

**1. client-go — auth header renamed from `X-Sig` to `X-Signature-V2`**
- Spec (`api-spec.md`, Authentication section): "Every request MUST carry the `X-Sig` header containing an HMAC of the body. Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."
- Client (`client-go.md`): "Signs every request and sends the signature in the `X-Signature-V2` header (renamed from the spec header during the v2 migration; the old name felt ambiguous)."
- Why this is breaking, not a casing/extension variant: the case-insensitivity clause only covers casing of the same header name (`X-Sig` vs `x-sig`), not substituting a different header name. The extension clause (section 7) covers *additional* `X-*` headers the spec does not define — it does not permit *replacing* a required header with a differently named one. Since the client never sends the literal `X-Sig` header (case-insensitively matched or otherwise), the required authentication header is absent from every request. This is a genuine contract violation, not a permitted difference.

**2. client-go — retries `POST /v2/transfers` on 5xx without required `Idempotency-Key`**
- Spec (`api-spec.md`, POST /v2/transfers): "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (`client-go.md`): "Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries because the team understood transfers to be safe to retry on 5xx."
- This directly violates both clauses quoted above: it retries a failed POST without an `Idempotency-Key`, and it fails to include `Idempotency-Key` on every POST /v2/transfers request (at minimum on the retried attempts). The team's stated belief that "transfers [are] safe to retry on 5xx" contradicts the spec's explicit statement that the endpoint "is NOT idempotent by itself."

**3. client-py — omits required `Idempotency-Key` on `POST /v2/transfers`**
- Spec (`api-spec.md`, POST /v2/transfers): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (`client-py.md`): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- The header is required on every request to this endpoint; the client's single-transfer path sends none. Breaking regardless of retry behavior.

**4. client-py — enum spelling mismatch breaks `status` matching**
- Spec (`api-spec.md`, GET /v2/transfers/{id}): "The `status` enum uses British spelling `cancelled`." Enum values: `pending`, `settled`, `cancelled`, `failed`.
- Client (`client-py.md`): "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Because the mapping performs **exact string equality** against the wire value, and the wire sends `cancelled` (British, per spec) while the mapping table's key is `canceled` (American, one fewer `l`), a cancelled transfer's status will never match any entry keyed `canceled`. This is not merely a naming/style difference internal to the client — it is a functional contract break: the client cannot correctly recognize the `cancelled` status the spec mandates the server sends.

**5. client-js — `expires_at` unit misinterpretation (treats epoch_ms as epoch seconds)**
- Spec (`api-spec.md`, GET /v2/transfers/{id}): "`expires_at` field is always epoch_ms (milliseconds since epoch, UTC)... treating it as seconds shifts expiry by three orders of magnitude."
- Client (`client-js.md`): "Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date."
- This is exactly the failure mode the spec warns against by name: multiplying an already-millisecond value by 1000 produces a timestamp roughly 1000× too far in the future, breaking expiry logic.

### Rejected candidates (non-findings)

| Candidate | Why it looked suspicious | Why it is not a finding (permitting clause) |
|---|---|---|
| client-go sends all headers lowercase (`x-signature-v2`, `content-type`) | Header casing differs from the spec's `X-Sig` capitalization style | Spec, Authentication section: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Casing alone is explicitly permitted. (Note: this client's header is still breaking for a *different* reason — see Breaking Drift item 1, it sends the wrong header name entirely, not merely a differently-cased correct name.) |
| client-py sends an extra `X-Trace-Id` header on every request | An undocumented header not listed in the spec's Authentication or Endpoints sections | Spec, Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is exactly the kind of tracing extension header this clause names as an example. |
| client-js sends an extra `X-Trace-Id` header alongside `X-Sig` | Same pattern as above — extra header beyond the documented contract | Same clause: Extension clause (section 7), tracing headers explicitly permitted. |
| client-py never retries `POST /v2/transfers` | Could look like an omission/incomplete implementation relative to client-go's retry logic | The spec does not require clients to retry; it only constrains retries when they do occur ("MUST NOT retry a failed POST ... unless they supply the required `Idempotency-Key` header"). Never retrying trivially satisfies this constraint — it is conservative, not a violation. |
| client-js attaches `Idempotency-Key` and never retries without one | Could look like unnecessary/defensive behavior worth flagging | This is precisely the behavior the spec requires ("REQUIRED on every POST /v2/transfers request"); it is fully compliant, not a drift candidate. |

## Assumptions
- The three fixture files (`client-go.md`, `client-py.md`, `client-js.md`) are complete descriptions of each client's relevant behavior; behavior not mentioned in a client's notes (e.g., whether client-go attaches `Idempotency-Key` on the *initial*, non-retry `POST /v2/transfers` call) is **unmeasured** — the client-go notes describe the retry policy explicitly lacking the header, but do not state one way or the other whether the first attempt includes it. This does not change the finding: the retry-without-key behavior alone is a breaking violation of the spec's explicit "MUST NOT retry ... unless" and "REQUIRED on every" clauses, independent of the first-attempt behavior.
- No runtime/integration testing was performed against a live server or real HTTP traffic; this audit is a static reading of the three fixture documents against the spec document only, per the task's scope restriction (no files outside the four named fixtures and this output file were read).
- The `id` field (string) from the `GET /v2/transfers/{id}` response is not addressed by any client's fixture notes; no drift can be confirmed or rejected for it. This is unmeasured, not "compliant by default" — it is reported here for completeness rather than silently omitted.

## Stop Condition
This artifact is complete: all four required headings-adjacent sections (Goal, Result, Evidence,
Assumptions) are present alongside this Stop Condition heading; the per-client drift table
enumerates every behavior described in the three client fixtures (including fully compliant
items) against every relevant spec clause; every breaking-drift claim is backed by an exact
quote from the spec and the corresponding client fixture; every rejected candidate cites the
specific spec clause (Authentication case-insensitivity clause, or Extension clause section 7)
that permits it. No further reads, edits, or fixture access are needed — the goal condition is
satisfied and work on this task stops here.
