# Goal

Audit the `client-py` implementation notes against the Transfer API Specification v2 (the source of truth), identify every breaking contract drift, and explicitly reject any spec-permitted differences as non-findings, citing the clause that permits them. Quote exact header names, field names, and enum values as evidence for every claim.

## Result

Two breaking drifts were found in `client-py`. Two candidate differences were considered and rejected as non-findings because the spec explicitly permits them.

### Drift table

| # | Area | Spec requirement | Client behavior | Verdict |
|---|------|-------------------|------------------|---------|
| 1 | `Idempotency-Key` header on `POST /v2/transfers` | REQUIRED on every request | Header omitted on the single-transfer path | Breaking |
| 2 | `status` enum value `cancelled` | Wire value uses British spelling `cancelled` | Client's exact-match mapping table only recognizes `canceled` (American spelling) | Breaking |
| 3 | `X-Trace-Id` extension header | Extension headers not defined in spec are permitted | Client sends `X-Trace-Id` on every request | Not a finding (permitted) |
| 4 | No retry of failed POSTs | Spec forbids retry without `Idempotency-Key`; does not require retries | Client never retries POSTs | Not a finding (permitted/compliant) |

### Breaking drifts (confirmed)

**1. Missing `Idempotency-Key` header on `POST /v2/transfers`**

- Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (api-spec.md, `### POST /v2/transfers`)
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." (client-py.md)
- Why it breaks the contract: the spec states this header is REQUIRED (not optional) on every request to this endpoint. The client's single-transfer path sends none. This is a direct violation of a MUST/REQUIRED-level requirement, not a permitted variation.

**2. `status` enum spelling mismatch causes exact-match failure on `cancelled`**

- Spec: "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`." (api-spec.md, GET /v2/transfers/{id} response fields table and note below it)
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." (client-py.md)
- Why it breaks the contract: the spec defines the wire value as `cancelled` (British, double "l"). The client's mapping table is keyed on `canceled` (American, single "l") and matches via exact string equality. Because the wire value from the server will always be `cancelled`, an exact-equality lookup against a table keyed on `canceled` will never match that value — the client cannot correctly interpret the `cancelled` state at all. This is a breaking drift, not a cosmetic naming difference, because the described matching mechanism (exact string equality) has no tolerance for the spelling divergence.

### Rejected candidates (non-findings)

**A. `X-Trace-Id` extension header**

- Candidate concern: client sends a header ("Sends an `X-Trace-Id` header on every request for distributed tracing.", client-py.md) not mentioned anywhere in the spec's endpoint definitions.
- Rejected because: the spec's Extension clause (section 7) states: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` header not defined elsewhere in the spec and matches the spec's own example use case ("tracing headers"). This is explicitly and by-example permitted.

**B. Client never retries POSTs**

- Candidate concern: client behavior around retries could be flagged as under-implementing some expected retry logic.
- Rejected because: the spec states "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" (api-spec.md, `### POST /v2/transfers`). The spec forbids retries under the stated condition and never mandates that clients must retry. "Never retries POSTs" (client-py.md) is consistent with — in fact the safest possible compliant subset of — this constraint. Not a drift.

## Evidence

Direct quotes used as evidence, all taken from the two named fixtures only:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`:
  - "Every request MUST carry the `X-Sig` header containing an HMAC of the body."
  - "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."
  - "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."
  - "This endpoint is NOT idempotent by itself: clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
  - "status | enum | one of: `pending`, `settled`, `cancelled`, `failed`"
  - "The `status` enum uses British spelling `cancelled`."
  - "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)"
  - "The `expires_at` field is always epoch_ms; treating it as seconds shifts expiry by three orders of magnitude."

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`:
  - "Sends the `X-Sig` header exactly as specified."
  - "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
  - "Never retries POSTs."
  - "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
  - "Parses `expires_at` as epoch milliseconds."
  - "Sends an `X-Trace-Id` header on every request for distributed tracing."

No claim in this artifact relies on information outside these two fixtures. Nothing in this task was left unmeasured — both fixtures are short and were read in full.

## Assumptions

- The two fixture files are treated as complete and authoritative for this audit; no other version of the spec or client code was consulted, per the task's scope restriction.
- `client-py.md` is prose describing implementation behavior (not literal source code); findings are based on the behavior as described in that prose, taken at face value.
- "Breaking drift" is defined as: client behavior that violates a MUST/MUST NOT/REQUIRED-level statement in the spec, or that (per the spec's own stated semantics) will cause incorrect handling of a valid server response/request. Both confirmed findings meet this bar; both rejected candidates are explicitly covered by a MAY-level permission or a compliant subset of a MUST NOT constraint.
- The `X-Sig` casing behavior and `expires_at` epoch_ms parsing were checked and found compliant; they are not listed as separate rejected candidates in the required section since they were never plausible drift candidates (client behavior matches the spec directly with no permitting clause needed), but are noted here for completeness since every field in both fixtures was reviewed.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-std-contract-claude_code_goal_command.md` and includes the Goal, Result, Evidence, Assumptions, and Stop Condition headings, a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section, satisfying the task validator. No commit, push, package install, or network access was performed. Only the two named fixture files and this output file were read/written, per the scoping rules.
