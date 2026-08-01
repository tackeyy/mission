# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-std-contract-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit `client-py` against `api-spec.md` (the source of truth). Find every breaking drift, and reject spec-permitted differences as non-findings citing the permitting clause. Quote exact header names, field names, and enum values.

# Result

Two breaking drifts were confirmed in client-py against the API Specification v2. Three candidate differences were evaluated and rejected as non-findings because the spec explicitly permits them.

## Drift Table

| # | Area | Spec requirement | Client behavior | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on `POST /v2/transfers` | REQUIRED on every request | Not sent on the single-transfer path | **Breaking drift** |
| 2 | `status` enum value `cancelled` | Wire enum uses British spelling `cancelled` | Mapping table keys on American spelling `canceled`, matched by exact string equality | **Breaking drift** |
| 3 | `X-Sig` header casing | Case-insensitive per RFC 9110; any casing MAY be sent | Sent "exactly as specified" | Compliant — not a finding |
| 4 | Retry behavior on `POST /v2/transfers` | MUST NOT retry unless `Idempotency-Key` supplied | "Never retries POSTs" | Compliant — not a finding |
| 5 | `X-Trace-Id` extension header | Extension clause (section 7) permits additional `X-*` headers | Sent on every request | Rejected — spec-permitted |
| 6 | `expires_at` field | epoch_ms (milliseconds) | Parsed as epoch milliseconds | Compliant — not a finding |

## Breaking Drifts (confirmed)

### 1. Missing `Idempotency-Key` header on `POST /v2/transfers`

- **Spec evidence** (`api-spec.md`): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- **Client evidence** (`client-py.md`): "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- **Why it's breaking**: The spec's requirement is unconditional ("REQUIRED on every ... request"), with no permitting clause carving out the single-transfer path. The client's own implementation notes confirm the omission is a known gap ("never updated"), not an intentional spec-permitted choice.

### 2. `status` enum mismatch: `canceled` vs. `cancelled`

- **Spec evidence** (`api-spec.md`): "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- **Client evidence** (`client-py.md`): "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- **Why it's breaking**: The wire value transmitted by the server is `cancelled` (British spelling, per spec). The client's mapping table key is `canceled` (American spelling), and matching is done by exact string equality. An exact-equality match of `cancelled` (wire) against a table keyed on `canceled` fails, so the client cannot recognize the cancelled state at all. This is a silent breaking drift — no exception is raised, the status is simply unmapped/unmatched.

## Rejected Candidates (spec-permitted, non-findings)

### A. `X-Sig` header casing

- **Candidate concern**: Client sends `X-Sig` "exactly as specified" — could be flagged as a hardcoded assumption about casing.
- **Rejected because**: Spec states "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Sending the exact documented casing is one of the explicitly permitted options. **Permitting clause**: "clients MAY send any casing."

### B. Never retrying `POST /v2/transfers`

- **Candidate concern**: Client "never retries POSTs" — could be flagged as under-implementing retry logic.
- **Rejected because**: Spec states clients "MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Never retrying is a valid (indeed the safest) subset of this constraint — it does not violate the MUST NOT condition. **Permitting clause**: "clients MUST NOT retry ... unless they supply the required `Idempotency-Key` header" (not retrying at all is always compliant with a MUST NOT rule).

### C. `X-Trace-Id` extension header

- **Candidate concern**: Client sends an undocumented `X-Trace-Id` header on every request.
- **Rejected because**: Spec's Extension clause (section 7) states "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` matches the `X-*` pattern and is explicitly the tracing-header example given. **Permitting clause**: "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers) ... Sending an extension header is never a contract violation."

# Evidence

All evidence quoted above is sourced directly from the two named fixtures:
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` (source of truth)
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` (client implementation notes)

No other files under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed, per task constraints. No network access, package installs, commits, or pushes were performed.

# Assumptions

- `client-py.md` is treated as an accurate, literal description of the client's actual runtime behavior (it is implementation *notes*, not executable code); no source `.py` file was available to cross-check, so behavior is taken as stated. This is unmeasured beyond what the notes assert.
- "Breaking drift" is defined as: the client's observable behavior deviates from a spec requirement in a way not covered by an explicit spec-provided permitting clause (MAY / RFC 9110 case-insensitivity / extension clause / MUST-NOT-unless framing).
- The `expires_at` handling and `X-Sig` casing were checked only against the two paragraphs the spec devotes to them; no other constraints on these fields were found in the provided fixture text.

# Stop Condition

This artifact is written to the exact required path and includes all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section. No further edits are planned; the task is complete as scoped. No commit, push, package install, or network access was performed, consistent with task rules.
