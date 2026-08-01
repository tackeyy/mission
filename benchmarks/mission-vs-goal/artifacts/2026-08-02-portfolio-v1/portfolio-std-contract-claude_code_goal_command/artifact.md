# portfolio-std-contract-claude_code_goal_command

## Goal

Audit the client implementation described in `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` against the API specification in `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` (the spec is the source of truth). Identify every breaking drift, and explicitly reject spec-permitted differences as non-findings, citing the permitting clause. Quote exact header names, field names, and enum values as evidence for every claim.

## Result

Two breaking drifts were confirmed in client-py, and three candidate differences were reviewed and rejected as non-findings because the spec explicitly permits them.

### Drift Table

| # | Area | Spec requirement | Client behavior | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on `POST /v2/transfers` | REQUIRED on every request | Missing on the single-transfer path | **Breaking drift** |
| 2 | `status` enum value `cancelled` (British spelling) | Wire value is exactly `cancelled` | Client's internal mapping table uses `canceled` (American spelling) and matches by exact string equality | **Breaking drift** |
| 3 | `X-Sig` header casing | Any casing acceptable (case-insensitive matching per RFC 9110) | Sent "exactly as specified" | Not a drift (compliant) |
| 4 | Retrying `POST /v2/transfers` | MUST NOT retry unless `Idempotency-Key` is supplied | Client never retries POSTs | Not a drift (compliant, more conservative than required) |
| 5 | `X-Trace-Id` extension header | Extension clause (section 7) permits additional `X-*` headers; never a violation | Client sends `X-Trace-Id` on every request | Not a drift (spec-permitted) |
| 6 | `expires_at` field | epoch_ms (milliseconds since epoch, UTC) | Parsed as epoch milliseconds | Not a drift (compliant) |

## Evidence

### Breaking drifts

**1. Missing `Idempotency-Key` header on `POST /v2/transfers`**

- Spec (`api-spec.md`): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." Also: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."
- Client (`client-py.md`): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- This is a breaking drift because the spec states the header is REQUIRED on every request to this endpoint, and the client's single-transfer path omits it entirely.

**2. `status` enum mismatch: `cancelled` vs `canceled`**

- Spec (`api-spec.md`): "`status` | enum | one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Client (`client-py.md`): "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- This is a breaking drift because the wire value is `cancelled` (British spelling), the client's mapping table only contains `canceled` (American spelling), and the match is by exact string equality — meaning any transfer with wire status `cancelled` will fail to map in the client.

### Rejected candidates (non-findings)

**A. `X-Sig` header casing**

- Client (`client-py.md`): "Sends the `X-Sig` header exactly as specified."
- Rejected because the spec (`api-spec.md`) states: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Sending it "exactly as specified" is trivially compliant, and any casing would have been compliant regardless.

**B. Never retrying `POST /v2/transfers`**

- Client (`client-py.md`): "Never retries POSTs."
- Rejected because the spec (`api-spec.md`) only restricts retries: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." It does not require clients to retry. Never retrying satisfies this constraint (a stricter-than-required behavior, not a violation). Note: this is independent of Breaking Drift #1 — the missing `Idempotency-Key` header is a violation on its own regardless of retry behavior.

**C. `X-Trace-Id` extension header**

- Client (`client-py.md`): "Sends an `X-Trace-Id` header on every request for distributed tracing."
- Rejected because the spec's Extension clause (section 7) states: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` matches the `X-*` pattern and the tracing-header example given, so this is explicitly permitted.

### Additional compliant behavior confirmed (not drift, not a rejected candidate — straightforward match)

- `expires_at` parsing: Spec (`api-spec.md`) requires "epoch_ms (milliseconds since epoch, UTC)." Client (`client-py.md`): "Parses `expires_at` as epoch milliseconds." Matches the spec exactly.

## Assumptions

- The two named fixture files (`api-spec.md`, `client-py.md`) are the complete and authoritative description of the spec and client behavior for this audit; no other source code or documentation was consulted, per task instructions.
- "Breaking drift" is interpreted as: a client behavior that would cause a spec-compliant server interaction to fail, be rejected, or be silently mishandled (e.g., missing a required header, or failing to correctly interpret a valid enum value).
- The client-py fixture is a prose description of implementation behavior (not literal source code); all quotes above are taken verbatim from that description.
- No runtime testing was performed — this is a static, textual comparison of the two fixture documents. Any behavior not described in `client-py.md` (e.g., exact retry backoff timing, logging behavior) is unmeasured and not claimed here.

## Stop Condition

This artifact exists at the required path and includes all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. No further action is needed for this task; the audit is complete based on the two named fixture files.
