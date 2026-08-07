# Contract Drift Audit — portfolio-std-contract (rep2, arm: claude_code_goal_command)

## Goal

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift, and reject spec-permitted differences as non-findings, citing the permitting clause. Fixtures read (exactly these two):

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

## Result

**2 breaking drifts confirmed. 4 candidates rejected as spec-permitted or spec-compliant.**

### Drift table

| # | Item | Spec (source of truth) | client-py behavior | Verdict |
|---|------|------------------------|--------------------|---------|
| 1 | `Idempotency-Key` header on POST /v2/transfers | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | "fires the request without an `Idempotency-Key` header" (single transfer path never updated) | **BREAKING** |
| 2 | `status` enum spelling | enum value `cancelled` (British spelling) | maps internal states using American spelling `canceled`, matched by "exact string equality against the wire value" | **BREAKING** |
| 3 | `X-Sig` authentication header | "Every request MUST carry the `X-Sig` header" | "Sends the `X-Sig` header exactly as specified." | Rejected (compliant) |
| 4 | Retry behavior on POST | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | "Never retries POSTs." | Rejected (compliant) |
| 5 | `expires_at` parsing | "always epoch_ms (milliseconds since epoch, UTC)" | "Parses `expires_at` as epoch milliseconds." | Rejected (compliant) |
| 6 | `X-Trace-Id` extension header | Extension clause (section 7) permits additional `X-*` headers | "Sends an `X-Trace-Id` header on every request" | Rejected (spec-permitted) |

### Breaking drifts (confirmed findings, with quoted evidence)

**Breaking drift 1: Missing required `Idempotency-Key` header on POST /v2/transfers**

- Spec (api-spec.md, POST /v2/transfers): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the spec makes the `Idempotency-Key` header mandatory on every request to this endpoint; the client omits it on the single-transfer path. This is a direct violation of a REQUIRED header.

**Breaking drift 2: `status` enum mismatch — `canceled` vs `cancelled`**

- Spec (api-spec.md, GET /v2/transfers/{id}): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Client (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: the wire value is `cancelled` (British spelling). The client's mapping table contains `canceled` (American spelling) and matches on "exact string equality against the wire value", so every `cancelled` transfer will fail to map. `pending`, `settled`, and `failed` are identical in both spellings and are unaffected.

### Rejected candidates (non-findings, with permitting/compliance clause)

**Rejected 1: `X-Sig` header** — Client: "Sends the `X-Sig` header exactly as specified." Spec: "Every request MUST carry the `X-Sig` header containing an HMAC of the body." Compliant; no drift. (Additionally, the Authentication section states "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing", so even a casing difference would be permitted — but the client sends it exactly as specified anyway.)

**Rejected 2: "Never retries POSTs"** — Spec: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies the MUST NOT; the spec never requires retrying. Compliant behavior, not a drift. (The missing `Idempotency-Key` itself is already counted as breaking drift 1; the no-retry policy is separately fine.)

**Rejected 3: `expires_at` as epoch milliseconds** — Client: "Parses `expires_at` as epoch milliseconds." Spec: "`expires_at` | integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms". The client matches the spec exactly; the seconds-vs-milliseconds trap does not apply here.

**Rejected 4: `X-Trace-Id` header on every request** — Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` extension header used for distributed tracing, exactly the example the clause gives. Non-finding.

## Evidence

All quotes above are verbatim from the two fixture files:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — key lines: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."; "one of: `pending`, `settled`, `cancelled`, `failed`"; "The `status` enum uses British spelling `cancelled`."; "epoch_ms (milliseconds since epoch, UTC)"; "Clients MAY send additional `X-*` extension headers not defined here"; "Header names are matched case-insensitively per RFC 9110".
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — key lines: "fires the request without an `Idempotency-Key` header"; "American spelling: `pending`, `settled`, `canceled`, `failed`"; "matches on exact string equality against the wire value"; "Parses `expires_at` as epoch milliseconds."; "Sends an `X-Trace-Id` header on every request"; "Sends the `X-Sig` header exactly as specified."

No runtime behavior was executed or measured; this audit compares the two fixture documents only. Anything not stated in the fixtures (e.g. actual HTTP traffic, server responses) is unmeasured.

## Assumptions

- The fixture documents fully and accurately describe the spec and the client behavior; no source code beyond `client-py.md` was inspected (none was in scope).
- "The spec is the source of truth" per the task prompt; any client deviation from a MUST/REQUIRED clause is treated as breaking, and anything covered by a MAY/permitting clause is a non-finding.
- The client-py notes are exhaustive for contract-relevant behavior; drifts not mentioned in the notes cannot be detected from these fixtures and are unmeasured.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-contract-claude_code_goal_command-rep2.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. Only the two named fixtures and this output file were touched; nothing else under `benchmarks/mission-vs-goal/` was opened. No commits, pushes, package installs, or network access occurred. Task complete — stopping.
