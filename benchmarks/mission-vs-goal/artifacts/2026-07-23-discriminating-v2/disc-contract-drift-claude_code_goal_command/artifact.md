# disc-contract-drift — claude_code_goal_command

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v2/disc-contract-drift-claude_code_goal_command.md`
and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Audit three client implementations (`client-go.md`, `client-py.md`,
`client-js.md`) against the API specification (`api-spec.md`) under
`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/`. Find
every breaking drift (behavior that violates the spec contract). Differences
explicitly permitted by the spec (extension headers, case-insensitive header
matching) must be rejected as non-findings, citing the permitting clause.

## Result

Read all four required fixtures only. Identified **6 breaking drifts** across
the three clients and **3 non-findings** correctly permitted by explicit spec
clauses. Every item present in each client's implementation notes is
enumerated below, including fully compliant behaviors, for exhaustive
coverage.

### Per-client drift table

| Client | Spec requirement | Client behavior | Verdict |
|---|---|---|---|
| client-go | Every request MUST carry the `X-Sig` header | Sends signature in a header renamed to `X-Signature-V2`; `X-Sig` is not sent | **Breaking** |
| client-go | Header names matched case-insensitively per RFC 9110; clients MAY send any casing | Sends headers lowercase (`x-signature-v2`, `content-type`) | Permitted |
| client-go | `Idempotency-Key` is REQUIRED on every `POST /v2/transfers` request | No idempotency header is attached to retries; no idempotency-key usage is described anywhere in the client's implementation notes | **Breaking** |
| client-go | Clients MUST NOT retry a failed `POST /v2/transfers` unless they supply the required `Idempotency-Key` header | Retries up to 3 times on any 5xx with exponential backoff, with no idempotency header attached | **Breaking** |
| client-go | `expires_at` is epoch_ms | Parses `expires_at` as epoch milliseconds | Compliant |
| client-go | `status` enum: `pending`, `settled`, `cancelled`, `failed` | Switches over `pending`, `settled`, `cancelled`, `failed` | Compliant |
| client-py | Every request MUST carry the `X-Sig` header | Sends the `X-Sig` header exactly as specified | Compliant |
| client-py | `Idempotency-Key` is REQUIRED on every `POST /v2/transfers` request | Fires the request without an `Idempotency-Key` header on the single-transfer path | **Breaking** |
| client-py | Clients MUST NOT retry a failed `POST /v2/transfers` unless they supply the required `Idempotency-Key` header | Never retries POSTs | Compliant (rule not triggered) |
| client-py | `status` enum uses British spelling `cancelled` | Maps enum using American spelling `canceled`, matched by exact string equality against the wire value | **Breaking** |
| client-py | `expires_at` is epoch_ms | Parses `expires_at` as epoch milliseconds | Compliant |
| client-py | Extension clause (section 7): clients MAY send additional `X-*` extension headers | Sends an `X-Trace-Id` header on every request | Permitted |
| client-js | Every request MUST carry the `X-Sig` header | Sends the `X-Sig` header exactly as specified | Compliant |
| client-js | Extension clause (section 7): clients MAY send additional `X-*` extension headers | Sends `X-Trace-Id` for tracing | Permitted |
| client-js | `Idempotency-Key` is REQUIRED on every `POST /v2/transfers` request / MUST NOT retry without it | Attaches a UUID `Idempotency-Key` on every call and never retries without one | Compliant |
| client-js | `status` enum: `pending`, `settled`, `cancelled`, `failed` | Switches over `pending`, `settled`, `cancelled`, `failed` | Compliant |
| client-js | `expires_at` is always epoch_ms (milliseconds) | `new Date(res.expires_at * 1000)` — treats the value as epoch seconds and multiplies by 1000 | **Breaking** |

### Breaking-drift section (confirmed findings)

1. **client-go — wrong authentication header name.**
   Spec: `"Every request MUST carry the X-Sig header containing an HMAC of the body."`
   Client: `"Signs every request and sends the signature in the X-Signature-V2 header (renamed from the spec header during the v2 migration; the old name felt ambiguous)."`
   The client never sends `X-Sig` at all — it substitutes a different header name. This is not a casing variation (the case-insensitive clause covers casing of the same header name) and it is not an "additional" header under the extension clause — it *replaces* the required header instead of supplementing it. The server has no way to authenticate the request against the contract as written.

2. **client-go — `Idempotency-Key` never established as sent.**
   Spec: `"Idempotency-Key is REQUIRED on every POST /v2/transfers request."`
   Client: The implementation notes describe signing, header casing, retry policy, `expires_at` parsing, and status handling — `Idempotency-Key` is mentioned only once, in the retry-policy bullet, and only in the negative ("No idempotency header is attached to retries"). No bullet describes the header being attached to the initial (non-retry) request either. Taken together with the retry-policy rationale ("the team understood transfers to be safe to retry on 5xx"), the notes are consistent with `Idempotency-Key` never being implemented in this client at all, which breaks the REQUIRED-on-every-POST rule.

3. **client-go — retries `POST /v2/transfers` without `Idempotency-Key`.**
   Spec: `"clients MUST NOT retry a failed POST /v2/transfers unless they supply the required Idempotency-Key header."`
   Client: `"Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries because the team understood transfers to be safe to retry on 5xx."`
   This is a direct violation: the client retries a non-idempotent endpoint without the one header the spec requires for safe retries, risking duplicate transfers.

4. **client-py — missing `Idempotency-Key` on `POST /v2/transfers`.**
   Spec: `"Idempotency-Key is REQUIRED on every POST /v2/transfers request."`
   Client: `"POST /v2/transfers: fires the request without an Idempotency-Key header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."`
   The single-transfer path explicitly omits a header the spec marks REQUIRED on every request to that endpoint.

5. **client-py — status enum spelling mismatch breaks exact-match mapping.**
   Spec: `"The status enum uses British spelling cancelled."`
   Client: `"Status handling: maps the API enum to internal states using American spelling: pending, settled, canceled, failed. The mapping table matches on exact string equality against the wire value."`
   Because the mapping table performs exact string equality and its key is `canceled` (one "l") while the wire value defined by the spec is `cancelled` (two "l"s), a `cancelled` status returned by the server will not match any entry in the client's table. This is a functional break in interpreting a spec-defined enum value, not a cosmetic naming choice.

6. **client-js — `expires_at` misinterpreted as epoch seconds.**
   Spec: `"expires_at | integer | epoch_ms (milliseconds since epoch, UTC)"` and `"The expires_at field is always epoch_ms; treating it as seconds shifts expiry by three orders of magnitude."`
   Client: `"Expiry handling: new Date(res.expires_at * 1000) — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date."`
   This is the exact failure mode the spec explicitly warns against: multiplying an already-millisecond value by 1000 shifts the interpreted expiry by three orders of magnitude.

### Rejected candidates (non-findings)

1. **client-go sending headers in lowercase (`x-signature-v2`, `content-type`).**
   Looked suspicious because it differs from the `X-Sig` / `X-Signature-V2` casing used elsewhere in the fixtures. Rejected: the spec states `"Header names are matched case-insensitively per RFC 9110; clients MAY send any casing."` Casing alone is explicitly permitted. (Note: this permitting clause does not rescue client-go's *separate* problem of sending the wrong header name entirely — that is confirmed finding #1 above.)

2. **client-py sending an `X-Trace-Id` header on every request.**
   Looked suspicious because it is not one of the headers named in the spec. Rejected: the spec's Extension clause (section 7) states `"Clients MAY send additional X-* extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."` `X-Trace-Id` is exactly the kind of tracing header this clause anticipates.

3. **client-js sending an `X-Trace-Id` header alongside `X-Sig`.**
   Same reasoning as candidate 2: permitted under the Extension clause (section 7) quoted above. Rejected as a non-finding.

## Evidence

All evidence is quoted directly from the four fixture files read for this
task:
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-go.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-js.md`

Exact quotes are inlined next to each finding and each rejected candidate
above rather than repeated separately, so that every claim is directly
traceable to the fixture text it is based on. No other files under
`benchmarks/mission-vs-goal/` were opened, read, grepped, or listed as part
of this task, per the task's scope restriction.

Coverage check: every bullet point present in `client-go.md` (6 bullets),
`client-py.md` (5 bullets), and `client-js.md` (4 bullets) is accounted for
in the per-client drift table above, either as a breaking finding, a
permitted non-finding, or a compliant behavior — none were omitted.

## Assumptions

- **client-go's initial (non-retry) `POST /v2/transfers` request never carries `Idempotency-Key` either.** This is inferred from the absence of any bullet in `client-go.md` describing the header being attached, combined with the retry-policy bullet's framing ("the team understood transfers to be safe to retry on 5xx," implying no idempotency-key mechanism was built at all). This is not a literal quote confirming the initial request lacks the header — it is the most defensible reading of the fixture as written, and it is called out explicitly here rather than silently folded into the retry-only finding.
- The `Idempotency-Key` value format (e.g., client-js's use of a UUID) is unmeasured against the spec, because `api-spec.md` does not specify a required format for the header value — only that the header must be present on every `POST /v2/transfers` request. No finding is made about format.
- Whether any client's behavior was actually exercised against a live or mocked server is unmeasured; this audit is a static reading of the fixture text only, not a runtime test.
- "Breaking" in the drift table means the client behavior, as described in its fixture notes, would violate a MUST/MUST NOT/REQUIRED clause in `api-spec.md` if executed as described.

## Stop Condition

This artifact is complete: it exists at the required path, contains all five
required headings (Goal, Result, Evidence, Assumptions, Stop Condition), and
satisfies the task validator's structural requirements — a per-client drift
table (client, spec requirement, client behavior, breaking-or-permitted), a
breaking-drift section with quoted evidence for each of the 6 confirmed
findings, and a rejected-candidates section citing the specific spec clause
(Extension clause section 7, or the RFC 9110 case-insensitivity sentence)
that permits each of the 3 rejected differences. No further fixture reading,
network access, package installation, commits, or pushes are needed. Work on
this task is done.
