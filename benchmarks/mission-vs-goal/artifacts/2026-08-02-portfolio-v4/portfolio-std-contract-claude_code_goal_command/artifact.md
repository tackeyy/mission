# portfolio-std-contract — API Contract Drift Audit

## Goal

Audit the client implementation described in `client-py.md` against the source-of-truth API specification in `api-spec.md`. Identify every breaking drift, and separately reject any candidate differences that the spec explicitly permits (citing the permitting clause). Quote exact header names, field names, and enum values as evidence for every claim.

## Result

Two breaking drifts were confirmed against the spec. Three candidate differences were evaluated and rejected as spec-permitted (non-findings).

### Drift table

| # | Area | Spec (source of truth) | Client (`client-py.md`) | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on `POST /v2/transfers` | REQUIRED on every request | Not sent on the single-transfer path | **Breaking drift** |
| 2 | `status` enum value for cancellation | `cancelled` (British spelling) | Mapping table uses `canceled` (American spelling), matched by exact string equality | **Breaking drift** |
| 3 | `X-Sig` header casing | Case-insensitive match permitted; clients MAY send any casing | Sent exactly as specified | Rejected (non-finding) |
| 4 | Extension header `X-Trace-Id` | Clients MAY send additional `X-*` extension headers; never a violation | Sends `X-Trace-Id` on every request | Rejected (non-finding) |
| 5 | Retry behavior for `POST /v2/transfers` | MUST NOT retry a failed POST unless `Idempotency-Key` is supplied | "Never retries POSTs" | Rejected (non-finding) |

## Evidence

### Breaking drift 1 — Missing required `Idempotency-Key` header

- Spec (`api-spec.md`): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (line 18-19), and "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" (line 16-18).
- Client (`client-py.md`): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." (line 4-6).
- Analysis: The spec states the header is REQUIRED (not conditionally required) on this endpoint. The client's single-transfer path omits it entirely. This is a breaking contract violation, independent of the retry rule.

### Breaking drift 2 — Enum spelling mismatch on `status`

- Spec (`api-spec.md`): "The `status` enum uses British spelling `cancelled`." (line 30), with the field defined as "one of: `pending`, `settled`, `cancelled`, `failed`" (line 27).
- Client (`client-py.md`): "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." (line 8-10).
- Analysis: The wire value sent by the server is `cancelled` (British spelling per spec). The client's mapping table contains `canceled` (American spelling) and matches by exact string equality — the two strings are not equal, so a transfer with wire status `cancelled` will fail to match any entry in the client's mapping table. This is a breaking drift.

## Rejected candidates (spec-permitted, non-findings)

1. **`X-Sig` header casing** — Client sends "the `X-Sig` header exactly as specified" (client-py.md, line 3). Even if it had used different casing, the spec's Authentication section states: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." (api-spec.md, line 5-6). Rejected under this permitting clause.
2. **`X-Trace-Id` extension header** — Client "Sends an `X-Trace-Id` header on every request for distributed tracing." (client-py.md, line 12). The spec's Extension clause (section 7) states: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." (api-spec.md, line 9-11). Rejected under this permitting clause.
3. **Never retrying POSTs** — Client "Never retries POSTs." (client-py.md, line 7). The spec requires clients "MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" (api-spec.md, line 16-18). Never retrying is a strict subset of "not retrying without the key" and does not violate this MUST NOT rule. Rejected as compliant behavior, not a drift.

### Non-finding also confirmed compliant (not a candidate, included for completeness)

- **`expires_at` parsing** — Client "Parses `expires_at` as epoch milliseconds." (client-py.md, line 11), matching the spec's "`expires_at` field is always epoch_ms (milliseconds since epoch, UTC)" (api-spec.md, line 28, 30-31). No drift.

## Assumptions

- The two fixture files (`api-spec.md`, `client-py.md`) are the complete and only source material for this audit; no other client code, tests, or wire captures were read or available.
- `client-py.md` is a prose description of implementation behavior (not literal source code); the audit takes its stated behavior at face value since no `.py` source was provided.
- "Breaking drift" is defined as: the client's actual behavior would cause an observable failure (rejected request, unmatched status, wrong semantics) when interacting with a server that strictly implements the spec as written.
- No network access, package installation, or execution of the described client code was performed or possible in this environment; findings are based on static reading of the two fixtures only, and are explicitly unmeasured in the sense of "not runtime-verified against a live server."

## Stop Condition

This task artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), includes a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. No further edits are planned for this run.
