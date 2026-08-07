# portfolio-std-contract — API Contract Drift Audit (claude_code_goal_command / rep2)

## Goal

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift in client-py, and reject spec-permitted differences as non-findings citing the permitting clause. Deliverable: this single artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-contract-claude_code_goal_command-rep2.md` containing a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Exactly two fixture files were read, as instructed:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`

**Confirmed breaking drifts: 2. Rejected candidates (spec-permitted or compliant): 4.**

### Drift table

| # | Item | Spec (source of truth) | client-py behavior | Verdict |
|---|------|------------------------|--------------------|---------|
| 1 | `Idempotency-Key` header on POST /v2/transfers | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | "fires the request without an `Idempotency-Key` header; ... the single transfer path was never updated." | **BREAKING** |
| 2 | `status` enum value for the cancelled state | enum is `pending`, `settled`, `cancelled`, `failed` (British spelling `cancelled`) | maps using American spelling `canceled`, "matches on exact string equality against the wire value" | **BREAKING** |
| 3 | `X-Trace-Id` extension header | Extension clause (section 7): extension headers are "never a contract violation" | "Sends an `X-Trace-Id` header on every request" | Rejected (permitted) |
| 4 | `X-Sig` authentication header | "Every request MUST carry the `X-Sig` header" | "Sends the `X-Sig` header exactly as specified." | Rejected (compliant) |
| 5 | `expires_at` parsing | "expires_at ... integer ... epoch_ms (milliseconds since epoch, UTC)" | "Parses `expires_at` as epoch milliseconds." | Rejected (compliant) |
| 6 | POST retry behavior | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | "Never retries POSTs." | Rejected (compliant) |

## Breaking drifts (confirmed findings, with quoted evidence)

### Breaking drift 1: Missing `Idempotency-Key` header on POST /v2/transfers

- **Spec requirement** (api-spec.md, "POST /v2/transfers"): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- **Client behavior** (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- **Why breaking**: The spec marks the header REQUIRED on every request; client-py omits it on the single-transfer path, so every POST /v2/transfers from client-py violates a MUST-level requirement. This is not saved by "Never retries POSTs" — the REQUIRED clause is unconditional ("on every POST /v2/transfers request"), independent of retry behavior.

### Breaking drift 2: `status` enum spelling mismatch — `canceled` vs `cancelled`

- **Spec requirement** (api-spec.md, GET /v2/transfers/{id} response table): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`", and "The `status` enum uses British spelling `cancelled`."
- **Client behavior** (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- **Why breaking**: The wire value for the cancelled state is `cancelled` (British spelling). client-py's mapping table contains `canceled` (American spelling) and "matches on exact string equality against the wire value", so the wire value `cancelled` will never match the client's `canceled` entry — cancelled transfers fail to map to any internal state. The other three values (`pending`, `settled`, `failed`) are identical in both files and are unaffected.

## Rejected candidates (non-findings, with permitting clause)

### Rejected 1: `X-Trace-Id` extension header

- **Client behavior** (client-py.md): "Sends an `X-Trace-Id` header on every request for distributed tracing."
- **Permitting clause** (api-spec.md, "Extension clause (section 7)"): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."
- **Verdict**: `X-Trace-Id` matches the `X-*` pattern and is exactly the "tracing headers" example the clause anticipates. Non-finding.

### Rejected 2: `X-Sig` header handling

- **Client behavior** (client-py.md): "Sends the `X-Sig` header exactly as specified."
- **Governing clause** (api-spec.md, "Authentication"): "Every request MUST carry the `X-Sig` header containing an HMAC of the body." Additionally, "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing" — so even a casing difference would be permitted; here the client is stated to send it exactly as specified anyway.
- **Verdict**: Compliant. Non-finding.

### Rejected 3: `expires_at` parsed as milliseconds

- **Client behavior** (client-py.md): "Parses `expires_at` as epoch milliseconds."
- **Governing clause** (api-spec.md, response table): `expires_at` is "epoch_ms (milliseconds since epoch, UTC)", and "The `expires_at` field is always epoch_ms; treating it as seconds shifts expiry by three orders of magnitude."
- **Verdict**: The client's milliseconds interpretation matches the spec exactly. The seconds/milliseconds trap described in the spec is not triggered. Non-finding.

### Rejected 4: "Never retries POSTs"

- **Client behavior** (client-py.md): "Never retries POSTs."
- **Governing clause** (api-spec.md, "POST /v2/transfers"): "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."
- **Verdict**: Not retrying is the safe, compliant behavior under this clause — the prohibition only bites when retrying without the header, which client-py never does. Non-finding as a *retry* violation. (The unconditional absence of `Idempotency-Key` is still Breaking drift 1, which is a separate REQUIRED-header requirement.)

## Evidence

All quotations above are taken verbatim from the two permitted fixture files:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — quoted: `X-Sig`, "Header names are matched case-insensitively per RFC 9110", Extension clause (section 7) "never a contract violation", "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request", enum `pending` / `settled` / `cancelled` / `failed`, "British spelling `cancelled`", `expires_at` "epoch_ms".
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — quoted: "without an `Idempotency-Key` header", American spelling list `pending`, `settled`, `canceled`, `failed`, "exact string equality against the wire value", "Parses `expires_at` as epoch milliseconds", "Sends an `X-Trace-Id` header on every request", "Never retries POSTs".

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed. Runtime behavior (actual HTTP traffic, server responses) is unmeasured — this audit is a static comparison of the two fixture documents only.

## Assumptions

- The fixture `client-py.md` implementation notes accurately and completely describe the client's behavior; no source code was available or read, so any behavior not stated in the notes is unmeasured.
- `api-spec.md` is the sole source of truth, per the task prompt.
- "Breaking drift" is interpreted as: a client behavior that violates a MUST/REQUIRED clause of the spec or that produces incorrect handling of spec-defined wire values (e.g., an enum value that can never match).
- The bulk endpoint mentioned in client-py.md ("the wrapper generates one only for the bulk endpoint") is not defined in the provided spec, so its conformance is out of scope and unmeasured.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-contract-claude_code_goal_command-rep2.md` and includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. Only the permitted fixture files and this output file were touched; no commits, pushes, package installs, or network access occurred. The task is complete and the goal condition is satisfied.
