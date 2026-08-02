# Portfolio Std Contract — client-py vs API Spec Audit

## Goal
Audit the client-py implementation against `api-spec.md` (source of truth), identify every breaking drift, and reject spec-permitted differences as non-findings citing the permitting clause. Quote exact header names, field names, and enum values as evidence.

## Result

Two breaking drifts confirmed, one candidate rejected as spec-permitted.

### Drift Table

| # | Area | Spec | client-py | Verdict |
|---|---|---|---|---|
| 1 | `Idempotency-Key` header on `POST /v2/transfers` | REQUIRED on every request | Not sent (wrapper only generates it for the bulk endpoint) | **Breaking** |
| 2 | `status` enum spelling | `cancelled` (British) | maps to `canceled` (American) via exact string equality | **Breaking** |
| 3 | `expires_at` parsing | epoch_ms | parsed as epoch milliseconds | Compliant (no drift) |
| 4 | `X-Sig` header | required, case-insensitive | sent exactly as specified | Compliant (no drift) |
| 5 | `X-Trace-Id` extension header | additional `X-*` headers permitted (section 7) | sent on every request | Non-finding (spec-permitted) |
| 6 | Retry behavior on failed POST | MUST NOT retry unless `Idempotency-Key` supplied | client never retries POSTs | Compliant (no drift) |

## Evidence

### Breaking drift 1: Missing required `Idempotency-Key` header

- Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (api-spec.md, section "POST /v2/transfers")
- Spec: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."
- client-py: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Impact: the single-transfer POST path violates a REQUIRED header constraint on every call.

### Breaking drift 2: Status enum spelling mismatch (`cancelled` vs `canceled`)

- Spec: "one of: `pending`, `settled`, `cancelled`, `failed`" and "The `status` enum uses British spelling `cancelled`."
- client-py: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Impact: since the mapping does exact string equality against the wire value, and the wire value is `cancelled` (British) while the client's mapping table entry is `canceled` (American), a `cancelled` transfer from the server will never match any entry in the client's mapping table — this is a functional breaking drift, not merely cosmetic.

## Assumptions

- "Exact string equality against the wire value" (client-py) is read literally: the client's table key `canceled` will never equal the wire string `cancelled`, so the mismatch is treated as a real matching failure rather than an internal-only naming choice. This inference is drawn directly from the client-py text; the actual runtime behavior when no table entry matches (exception vs. silent fallback) is not described in either fixture and is therefore unmeasured.
- No other endpoints, fields, or headers beyond those quoted in the two fixtures were in scope; only `api-spec.md` and `client-py.md` were read, per task constraints.

## Rejected Candidates (non-findings)

- **`X-Trace-Id` header sent on every request** — rejected as a finding. Permitting clause: api-spec.md section 7 ("Extension clause"): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is exactly this kind of extension header.
- **Client never retries POSTs** — rejected as a finding. Spec permits/expects exactly this: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Since the client also lacks the key on this path (see breaking drift 1), never retrying is the spec-compliant fallback behavior, not an independent drift.
- **`expires_at` parsed as epoch milliseconds** — rejected as a finding. Matches spec directly: "`expires_at` field is always epoch_ms"; client-py: "Parses `expires_at` as epoch milliseconds." No drift.
- **`X-Sig` header casing/presence** — rejected as a finding. client-py: "Sends the `X-Sig` header exactly as specified," and spec explicitly allows casing variance: "clients MAY send any casing."

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v5-speed/portfolio-std-contract-claude_code_goal_command.md` and includes the headings Goal, Result, Evidence, Assumptions, and Stop Condition, satisfying the benchmark's completion condition. No commits, pushes, package installs, or network access were performed. Only the two named fixtures under `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/` were read; no other benchmark metadata was accessed.
