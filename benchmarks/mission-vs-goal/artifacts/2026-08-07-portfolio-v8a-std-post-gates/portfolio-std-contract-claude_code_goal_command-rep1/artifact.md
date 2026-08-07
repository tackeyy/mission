# portfolio-std-contract — API Contract Drift Audit (rep1)

- Task id: `portfolio-std-contract`
- Arm: `claude_code_goal_command`
- Date: 2026-08-07
- Fixtures read (exactly these two):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` (source of truth)
  - `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` (audited client)

## Goal

Audit the `client-py` implementation against Transfer API Specification v2. Find every breaking drift, and reject spec-permitted differences as non-findings citing the permitting clause. Produce a single artifact at this path with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Audit complete. **2 breaking drifts confirmed** and **4 candidates rejected** as spec-permitted or spec-compliant behavior.

### Drift table

| # | Area | Spec requirement (quoted) | Client behavior (quoted) | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on POST /v2/transfers | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | "fires the request without an `Idempotency-Key` header" | **Breaking drift** |
| 2 | `status` enum value `cancelled` | "one of: `pending`, `settled`, `cancelled`, `failed`" (British spelling) | maps using American spelling "`pending`, `settled`, `canceled`, `failed`" with "exact string equality against the wire value" | **Breaking drift** |
| R1 | `X-Sig` authentication header | "Every request MUST carry the `X-Sig` header" | "Sends the `X-Sig` header exactly as specified." | Rejected (compliant) |
| R2 | `X-Trace-Id` extension header | Extension clause (section 7): clients MAY send additional `X-*` headers | "Sends an `X-Trace-Id` header on every request" | Rejected (spec-permitted) |
| R3 | `expires_at` unit handling | "`expires_at` ... is always epoch_ms" | "Parses `expires_at` as epoch milliseconds." | Rejected (compliant) |
| R4 | POST retry behavior | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header" | "Never retries POSTs." | Rejected (compliant) |

## Breaking drifts (confirmed findings, with quoted evidence)

### Drift 1 — Missing required `Idempotency-Key` header on POST /v2/transfers

- **Spec** (api-spec.md, "POST /v2/transfers"): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- **Client** (client-py.md): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- **Why breaking**: the spec makes the header REQUIRED on every single-transfer POST; the client omits it entirely on that path. Every POST /v2/transfers request from client-py violates a MUST-level requirement.

### Drift 2 — `status` enum mismatch: `canceled` (client) vs `cancelled` (wire)

- **Spec** (api-spec.md, GET /v2/transfers/{id} response fields): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`", and "The `status` enum uses British spelling `cancelled`."
- **Client** (client-py.md): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- **Why breaking**: the wire value is `cancelled` (British). The client's mapping table contains `canceled` (American) and matches on "exact string equality against the wire value", so the wire value `cancelled` will never match any entry. Cancelled transfers cannot be mapped to an internal state.

## Rejected candidates (non-findings, with permitting clause)

### R1 — `X-Sig` header

- Client: "Sends the `X-Sig` header exactly as specified." This satisfies the Authentication requirement "Every request MUST carry the `X-Sig` header containing an HMAC of the body." Additionally, casing could never be a drift here because the spec states "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Compliant — not a finding.

### R2 — `X-Trace-Id` extension header

- Client: "Sends an `X-Trace-Id` header on every request for distributed tracing." Permitted by the **Extension clause (section 7)**: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." Not a finding.

### R3 — `expires_at` parsed as milliseconds

- Client: "Parses `expires_at` as epoch milliseconds." This matches the spec exactly: "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms". The seconds-vs-milliseconds trap does not apply — the client uses the correct unit. Compliant — not a finding.

### R4 — Never retrying POSTs

- Client: "Never retries POSTs." The spec's retry constraint is "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies this MUST NOT. Note this does not excuse Drift 1: the `Idempotency-Key` header is independently "REQUIRED on every POST /v2/transfers request", retry or not. The retry behavior itself is compliant — not a finding.

## Evidence

All evidence above is quoted verbatim from the two named fixture files, which were the only files read for this audit:

- Spec quotes: `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — `X-Sig` (Authentication), extension clause (section 7), `Idempotency-Key` requirement (POST /v2/transfers), status enum `pending`/`settled`/`cancelled`/`failed`, `expires_at` = epoch_ms (GET /v2/transfers/{id}).
- Client quotes: `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — all six bullet points of the implementation notes are accounted for above (2 breaking, 4 rejected/compliant).

Unmeasured: no runtime behavior, HTTP traffic, or actual client code was executed or inspected — the audit is based solely on the two fixture documents. Any behavior not described in `client-py.md` is unmeasured.

## Assumptions

- `api-spec.md` is the sole source of truth, per the task prompt.
- `client-py.md` accurately and completely describes the client's behavior; drifts are judged only against statements present in that file.
- "Breaking drift" means a violation of a MUST/REQUIRED-level spec clause or a behavior that misinterprets wire values (enum/unit mismatches) in a way that breaks interoperability.
- No other fixtures, task metadata, or answer keys were read (per benchmark rules).

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-contract-claude_code_goal_command-rep1.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. All six client behaviors are classified as either confirmed breaking drift (2) or rejected candidate (4). Stop condition met; no further edits or actions (no commit, no push, no network access) are performed.
