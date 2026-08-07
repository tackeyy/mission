# portfolio-std-contract — API Contract Drift Audit (claude_code_goal_command / rep3)

## Goal

Audit the `client-py` implementation against the Transfer API Specification v2 (source of truth). Find every breaking drift in client-py, and reject spec-permitted differences as non-findings citing the permitting clause. Deliverable: this artifact at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-contract-claude_code_goal_command-rep3.md` with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Audit complete. **2 breaking drifts confirmed** and **4 candidates rejected** as spec-permitted or compliant behavior. Only the two fixture files named in the task prompt were read; no other benchmark files were accessed.

### Drift table

| # | Area | Spec requirement | client-py behavior | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on POST /v2/transfers | REQUIRED on every request | Not sent on single-transfer path | **BREAKING** |
| 2 | `status` enum value `cancelled` | Wire value is British spelling `cancelled` | Maps American `canceled` with exact string equality | **BREAKING** |
| 3 | `X-Sig` header | MUST carry on every request | Sent exactly as specified | Compliant — rejected |
| 4 | `X-Trace-Id` extension header | Extension headers permitted (section 7) | Sent on every request | Permitted — rejected |
| 5 | POST retry behavior | MUST NOT retry without `Idempotency-Key` | Never retries POSTs | Compliant — rejected |
| 6 | `expires_at` units | epoch_ms (milliseconds since epoch, UTC) | Parsed as epoch milliseconds | Compliant — rejected |

### Breaking drifts (confirmed findings)

**B1. Missing required `Idempotency-Key` header on POST /v2/transfers.**
- Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (api-spec.md, POST /v2/transfers)
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." (client-py.md)
- Impact: every single-transfer POST violates a MUST-level requirement of the spec.

**B2. `status` enum mismatch — `cancelled` vs `canceled`.**
- Spec: `status` is "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`." (api-spec.md, GET /v2/transfers/{id})
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." (client-py.md)
- Impact: the wire value `cancelled` will never match the client's `canceled` entry under exact string equality, so cancelled transfers fail to map. The other three values (`pending`, `settled`, `failed`) are spelled identically in both and are unaffected.

### Rejected candidates (non-findings)

**R1. `X-Trace-Id` extension header.** Client "Sends an `X-Trace-Id` header on every request for distributed tracing." Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). … Sending an extension header is never a contract violation."

**R2. `X-Sig` header handling.** Client "Sends the `X-Sig` header exactly as specified." Satisfies the Authentication requirement "Every request MUST carry the `X-Sig` header". Additionally, the spec permits any casing: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." — so even a casing difference would not be a drift; here there is none.

**R3. Never retrying POSTs.** Client "Never retries POSTs." The spec's constraint is "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Not retrying at all satisfies this constraint; the retry policy itself is not a drift. (The missing header is already counted as B1.)

**R4. `expires_at` parsing.** Client "Parses `expires_at` as epoch milliseconds." Matches the spec: "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)". No drift.

## Evidence

All quotes above are verbatim from the two permitted fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — quoted: `X-Sig`, "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing", Extension clause section 7 ("Sending an extension header is never a contract violation"), "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request", enum values `pending`, `settled`, `cancelled`, `failed`, "The `status` enum uses British spelling `cancelled`", `expires_at` = "epoch_ms (milliseconds since epoch, UTC)".
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — quoted: "fires the request without an `Idempotency-Key` header", "the single transfer path was never updated", American spelling list `pending`, `settled`, `canceled`, `failed`, "matches on exact string equality against the wire value", "Parses `expires_at` as epoch milliseconds", "Sends an `X-Trace-Id` header on every request", "Never retries POSTs", "Sends the `X-Sig` header exactly as specified".

Runtime impact (e.g. actual server rejection rates, real cancelled-status frequency) is **unmeasured** — this audit is a static document comparison of the two fixture files only.

## Assumptions

- The fixture `client-py.md` implementation notes are an accurate and complete description of the client's behavior; no source code beyond these notes was available or read.
- "Breaking drift" means a client behavior that violates a MUST/REQUIRED spec clause or that mishandles a spec-defined wire value (as with the enum mismatch).
- The bulk endpoint mentioned in client-py.md is out of scope: the spec fixture defines only POST /v2/transfers and GET /v2/transfers/{id}, so only those are audited.

## Stop Condition

This artifact exists at the required path and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, plus the validator-required drift table, breaking-drift section with quoted evidence, and rejected-candidates section. No commits, pushes, package installs, or network access were performed; no other files under `benchmarks/mission-vs-goal/` were opened. Task complete — stopping.
