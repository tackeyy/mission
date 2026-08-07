# Contract Drift Audit — portfolio-std-contract (rep1, arm: claude_code_goal_command)

## Goal

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift in client-py, and reject spec-permitted differences as non-findings citing the permitting clause. Deliverable: this artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-contract-claude_code_goal_command-rep1.md` with headings Goal, Result, Evidence, Assumptions, and Stop Condition, containing a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Fixtures read (exactly these two, as instructed):

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

**Confirmed breaking drifts: 2. Rejected (spec-permitted) candidates: 4.**

### Drift table

| # | Candidate | Spec requirement | client-py behavior | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on POST /v2/transfers | REQUIRED on every POST /v2/transfers request | Not sent on the single-transfer path (only generated for the bulk endpoint) | **BREAKING** |
| 2 | `status` enum spelling | Wire value is British `cancelled` | Maps American `canceled` with exact string equality → `cancelled` never matches | **BREAKING** |
| 3 | `X-Sig` authentication header | MUST be present on every request | Sent exactly as specified | Rejected (compliant) |
| 4 | `expires_at` unit | epoch_ms (milliseconds since epoch, UTC) | Parsed as epoch milliseconds | Rejected (compliant) |
| 5 | `X-Trace-Id` extra header | Extension clause (section 7) permits additional `X-*` headers | Sends `X-Trace-Id` on every request | Rejected (spec-permitted) |
| 6 | POST retry behavior | MUST NOT retry failed POST without `Idempotency-Key` | Never retries POSTs | Rejected (compliant) |

## Breaking drifts (with quoted evidence)

### Breaking drift 1: Missing required `Idempotency-Key` header on POST /v2/transfers

- Spec (api-spec.md, POST /v2/transfers): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the spec makes the header REQUIRED on every request to this endpoint; the client omits it on the single-transfer path, so every single-transfer POST violates the contract.

### Breaking drift 2: `status` enum spelling mismatch — `canceled` vs `cancelled` under exact string matching

- Spec (api-spec.md, GET /v2/transfers/{id}): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- Client (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: the wire value `cancelled` never equals the client's `canceled` under exact string equality, so cancelled transfers fail to map to any internal state. The other three values (`pending`, `settled`, `failed`) are spelled identically and are unaffected.

## Rejected candidates (spec-permitted or compliant, with permitting clause)

### Rejected 1: `X-Sig` header handling — compliant, not a drift

- Client (client-py.md): "Sends the `X-Sig` header exactly as specified."
- Spec (api-spec.md, Authentication): "Every request MUST carry the `X-Sig` header containing an HMAC of the body." The spec additionally permits any casing: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." — so even a casing difference would be permitted; here there is none.

### Rejected 2: `expires_at` parsed as milliseconds — compliant, not a drift

- Spec (api-spec.md): `expires_at` is "integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms".
- Client (client-py.md): "Parses `expires_at` as epoch milliseconds."
- The client's unit matches the spec exactly; the seconds-vs-milliseconds trap described by the spec ("treating it as seconds shifts expiry by three orders of magnitude") does not apply here.

### Rejected 3: Extra `X-Trace-Id` header — permitted by the Extension clause (section 7)

- Client (client-py.md): "Sends an `X-Trace-Id` header on every request for distributed tracing."
- Permitting clause (api-spec.md, "Extension clause (section 7)"): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."
- `X-Trace-Id` is an `X-*` extension header used for tracing — exactly the case the clause permits.

### Rejected 4: Never retrying POSTs — compliant, not a drift

- Spec (api-spec.md, POST /v2/transfers): "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."
- Client (client-py.md): "Never retries POSTs."
- Not retrying satisfies the MUST NOT constraint; the spec requires the header for retries but does not require retrying. (The missing header itself is Breaking drift 1, counted once there.)

## Evidence

All evidence above is quoted verbatim from the two named fixture files:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — quoted clauses: the `X-Sig` MUST requirement and case-insensitivity permission (Authentication section), the Extension clause (section 7), the `Idempotency-Key` REQUIRED sentence (POST /v2/transfers), the `status` enum list `pending`, `settled`, `cancelled`, `failed` and the British-spelling note, and the `expires_at` epoch_ms definition.
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — quoted behaviors: `X-Sig` sent as specified, no `Idempotency-Key` on the single-transfer path, no POST retries, American-spelling enum mapping (`canceled`) with exact string equality, `expires_at` parsed as milliseconds, and the `X-Trace-Id` tracing header.

No files under `benchmarks/mission-vs-goal/` other than these two fixtures and this output file were opened. No tests or network calls were executed; the audit is a static document-to-document comparison (runtime behavior is unmeasured).

## Assumptions

- The two fixture Markdown documents fully and accurately describe the API contract and the client behavior; no source code was available or consulted beyond them.
- "Breaking drift" means a client behavior that violates a MUST/REQUIRED clause of the spec or mishandles a spec-defined wire value, as observable at the contract boundary.
- The client's "internal states" naming is client-internal and only breaking insofar as the mapping fails on the wire value (`cancelled`); internal naming itself is not part of the contract.
- Runtime impact (e.g., how the client behaves when the enum mapping misses) is unmeasured; the finding is based on the documented "exact string equality" matching rule.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v6-repeats3/portfolio-std-contract-claude_code_goal_command-rep1.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, plus the validator-required drift table, breaking-drift section with quoted evidence, and rejected-candidates section. All confirmed findings quote exact identifiers from the fixtures; no benchmark metadata was accessed; no commits, pushes, package installs, or network access occurred. The task is complete and the run stops here.
