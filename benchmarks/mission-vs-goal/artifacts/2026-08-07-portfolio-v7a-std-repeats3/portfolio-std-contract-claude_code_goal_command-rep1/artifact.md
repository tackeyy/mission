# portfolio-std-contract — API Contract Drift Audit (rep1)

- Task id: `portfolio-std-contract`
- Arm: `claude_code_goal_command`
- Date: 2026-08-07
- Fixtures read (exactly these two):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` (source of truth)
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` (audited client)

## Goal

Audit the `client-py` implementation against Transfer API Specification v2, find every breaking drift, and reject spec-permitted differences as non-findings citing the permitting clause. Produce this artifact with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Audit complete. **2 breaking drifts confirmed** and **4 candidates rejected** as spec-permitted or spec-compliant behavior.

### Drift table

| # | Area | Client behavior | Spec requirement | Verdict |
|---|---|---|---|---|
| B1 | `Idempotency-Key` header on POST /v2/transfers | Not sent on single-transfer path | REQUIRED on every POST /v2/transfers request | **Breaking drift** |
| B2 | `status` enum value `cancelled` | Client matches exact string `canceled` (American spelling) | Wire value is British spelling `cancelled` | **Breaking drift** |
| R1 | `X-Sig` authentication header | Sent exactly as specified | `X-Sig` MUST be on every request | Rejected (compliant) |
| R2 | `X-Trace-Id` extension header | Sent on every request | Extension clause (section 7) permits `X-*` headers | Rejected (permitted) |
| R3 | `expires_at` parsing | Parsed as epoch milliseconds | Spec: `expires_at` is epoch_ms | Rejected (compliant) |
| R4 | POST retry behavior | Never retries POSTs | Retry forbidden without `Idempotency-Key`; not retrying is allowed | Rejected (compliant) |

### Breaking drifts (with quoted evidence)

**B1 — Missing required `Idempotency-Key` header on POST /v2/transfers**

- Spec (api-spec.md, "POST /v2/transfers"): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Impact: every single-transfer POST omits a header the spec marks REQUIRED — a per-request contract violation regardless of retry behavior.

**B2 — `status` enum mismatch: client expects `canceled`, wire sends `cancelled`**

- Spec (api-spec.md, GET /v2/transfers/{id} response table): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`", and "The `status` enum uses British spelling `cancelled`."
- Client (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Impact: the wire value `cancelled` never matches the client's `canceled` entry under exact string equality, so every cancelled transfer fails to map. `pending`, `settled`, `failed` are identical in both spellings and are unaffected.

### Rejected candidates (non-findings, with permitting clause)

**R1 — `X-Sig` header**: Client states "Sends the `X-Sig` header exactly as specified." This satisfies the Authentication requirement ("Every request MUST carry the `X-Sig` header containing an HMAC of the body"). Compliant, not a drift. Note the spec also says "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing" — so even a casing difference would be permitted by that clause; here none is reported anyway.

**R2 — `X-Trace-Id` extension header**: Client "Sends an `X-Trace-Id` header on every request for distributed tracing." Rejected under the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."

**R3 — `expires_at` parsing**: Client "Parses `expires_at` as epoch milliseconds." This matches the spec exactly: "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms". Compliant, not a drift.

**R4 — POST retry behavior**: Client "Never retries POSTs." The spec's constraint is "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies this MUST NOT. Compliant, not a drift (the missing header itself is already counted as B1).

## Evidence

All evidence is quoted verbatim from the two fixture files above; each B/R item cites its exact quote inline. No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed. Key quoted identifiers: headers `X-Sig`, `Idempotency-Key`, `X-Trace-Id`; field names `id`, `status`, `expires_at`; enum values `pending`, `settled`, `cancelled` (spec) vs `canceled` (client), `failed`.

## Assumptions

- The two markdown fixtures are complete and accurate descriptions of the spec and the client; behaviors not mentioned in client-py.md are unmeasured and not audited.
- "Breaking drift" means a client behavior that violates a spec MUST/REQUIRED clause or misinterprets a wire value in a way that changes observable behavior.
- The client's internal-state naming (American spelling internally) would be acceptable if the mapping keys matched the wire values; the drift is specifically the exact-string match against the wire value `cancelled`.
- No runtime testing was performed (fixtures are prose descriptions, not executable code); impact statements are derived from the quoted text, not from measurement.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-contract-claude_code_goal_command-rep1.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. No commits, pushes, package installs, or network access were made; edits were limited to this single output file. Task complete.
