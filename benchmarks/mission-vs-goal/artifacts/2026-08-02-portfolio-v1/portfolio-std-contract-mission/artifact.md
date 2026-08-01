# portfolio-std-contract-mission

## Mission

Audit the client implementation in `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` against the API specification in `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`. The spec is source of truth. Find every breaking drift in client-py, and identify spec-permitted differences to reject as non-findings (citing the permitting clause). Quote exact header names, field names, and enum values as evidence.

---

## Plan

1. Read `api-spec.md` in full to extract all normative requirements (MUST / MUST NOT / REQUIRED).
2. Read `client-py.md` in full to enumerate all stated client behaviors.
3. Cross-map each client behavior against each spec requirement to classify: **breaking drift**, **compliant**, or **spec-permitted (non-finding)**.
4. Populate the drift table, breaking-drift section with quoted evidence, and rejected-candidates section.
5. Score and record stop decision.

Dependencies: steps 1 and 2 are independent (parallel-eligible); steps 3–5 depend on both.

---

## Execution

### Spec requirements extracted

| Req ID | Clause | Normative text |
|--------|--------|----------------|
| R1 | Authentication | Every request MUST carry the `X-Sig` header containing an HMAC of the body. |
| R2 | Authentication | Header names are matched case-insensitively per RFC 9110; clients MAY send any casing. |
| R3 | Extension clause (§7) | Clients MAY send additional `X-*` extension headers. Sending an extension header is never a contract violation. |
| R4 | POST /v2/transfers | Clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header. |
| R5 | POST /v2/transfers | `Idempotency-Key` is REQUIRED on every POST /v2/transfers request. |
| R6 | GET /v2/transfers/{id} | `status` enum values: `pending`, `settled`, `cancelled`, `failed` (British spelling `cancelled`). |
| R7 | GET /v2/transfers/{id} | `expires_at` is epoch_ms (milliseconds since epoch, UTC). |

### Client behaviors extracted

| Behavior ID | Client-py statement |
|-------------|---------------------|
| B1 | Sends the `X-Sig` header exactly as specified. |
| B2 | POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint. |
| B3 | Never retries POSTs. |
| B4 | Status mapping uses American spelling: `canceled` (single `l`). Mapping matches on exact string equality against the wire value. |
| B5 | Parses `expires_at` as epoch milliseconds. |
| B6 | Sends an `X-Trace-Id` header on every request for distributed tracing. |

### Cross-map

| Behavior | Relevant req | Classification | Reason |
|----------|-------------|----------------|--------|
| B1 — sends `X-Sig` | R1 | Compliant | Header present as required. |
| B2 — no `Idempotency-Key` on POST | R5 | **Breaking drift** | R5: REQUIRED on every POST /v2/transfers. |
| B3 — never retries POSTs | R4 | Compliant | R4 prohibits retry without the key; not retrying at all satisfies the MUST NOT. |
| B4 — `canceled` vs `cancelled` | R6 | **Breaking drift** | R6 defines wire value as `cancelled` (two `l`s). Client matches on exact string equality so `cancelled` never maps. |
| B5 — parses `expires_at` as epoch ms | R7 | Compliant | Matches R7 exactly. |
| B6 — sends `X-Trace-Id` | R3 | Non-finding (spec-permitted) | R3 §7: clients MAY send additional `X-*` extension headers; never a contract violation. |

---

## Review

- All seven spec requirements were checked against all six client behaviors.
- Two breaking drifts identified (B2, B4); both are unambiguous REQUIRED / enum violations with direct textual evidence.
- One non-finding (B6) is expressly permitted by the extension clause and correctly rejected.
- No spec requirement was skipped; no client behavior was overlooked.
- The no-retry behavior (B3) is frequently a false positive: it is compliant because the prohibition in R4 is conditional ("unless they supply the key"), and never retrying satisfies the condition trivially.

---

## Score

| Dimension | Rating (1–5) | Notes |
|-----------|-------------|-------|
| Completeness — all breaking drifts found | 5 | Both breaking drifts (missing Idempotency-Key; `canceled` vs `cancelled`) identified. |
| Precision — no false positives | 5 | `X-Trace-Id` correctly rejected via §7 extension clause; no-retry correctly classified as compliant. |
| Evidence quality — exact quotes used | 5 | Header names, field names, and enum values quoted verbatim from spec and client. |
| Reasoning clarity | 5 | Each classification traceable to a named requirement with normative language cited. |

**Overall: 5 / 5**

---

## Stop Decision

**HALT — mission complete.** Both breaking drifts are identified and evidenced. All non-findings are cited with permitting clauses. The artifact satisfies all required sections. No further iteration needed.

---

## Evidence

### Breaking drift 1 — Missing `Idempotency-Key` on POST /v2/transfers

**Spec (R5):**
> "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."

**Client-py (B2):**
> "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."

The client never attaches the `Idempotency-Key` header to the single-transfer POST path. Every such request violates R5.

---

### Breaking drift 2 — Status enum spelling mismatch (`canceled` vs `cancelled`)

**Spec (R6):**
> "status | enum | one of: `pending`, `settled`, `cancelled`, `failed`"
> "The `status` enum uses British spelling `cancelled`."

**Client-py (B4):**
> "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."

The wire value is `cancelled` (two `l`s). The client's mapping key is `canceled` (one `l`). Because the match is exact-string, every `cancelled` response arrives unmatched. Downstream state is silently corrupted or falls into an unhandled branch.

---

## Rejected Candidates (non-findings)

### Candidate — `X-Trace-Id` header on every request

**Client-py (B6):**
> "Sends an `X-Trace-Id` header on every request for distributed tracing."

**Rejected — spec-permitted, not a drift.** The extension clause (spec §7 / R3) states:
> "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation."

`X-Trace-Id` is an `X-*` header not defined in the spec's normative endpoint tables, and the permitting clause explicitly names "tracing headers" as an example of what is allowed. This is not a breaking drift.

### Candidate — never retrying POST /v2/transfers

**Client-py (B3):**
> "Never retries POSTs."

**Rejected — compliant, not a drift.** Spec R4 states:
> "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header."

The prohibition is conditional (retry-without-key is forbidden); abstaining from retries entirely trivially satisfies the MUST NOT. There is no spec clause requiring the client to retry, so this is not a drift candidate against any requirement.

---

## Drift Table (summary)

| # | Behavior | Spec requirement | Classification | Severity |
|---|----------|-----------------|----------------|----------|
| 1 | No `Idempotency-Key` on POST /v2/transfers | R5 — REQUIRED on every POST | **Breaking drift** | High — every single-transfer POST violates the spec; retry safety absent |
| 2 | Status enum `canceled` (1 `l`) vs wire `cancelled` (2 `l`s) | R6 — exact wire values defined | **Breaking drift** | High — `cancelled` status never matched; client enters undefined state on cancelled transfers |
| 3 | `X-Trace-Id` extension header | §7 — `X-*` extension headers are always permitted | Non-finding (spec-permitted) | — |

---

## Assumptions

- The two fixture files are the complete and authoritative inputs for this audit; no external documentation was consulted.
- "Breaking drift" is defined as a client behavior that violates a MUST / MUST NOT / REQUIRED normative clause in the spec, causing incorrect runtime behavior in at least one realistic scenario.
- "Non-finding" means the client behavior is either compliant or explicitly permitted by the spec; it cannot be cited as a violation.
- B3 (never retries) is treated as compliant rather than a gap, because R4 prohibits retry only without the `Idempotency-Key`. Not retrying at all is a valid (if conservative) implementation choice. If the intent were to assess retry coverage (a non-contract concern), B3 would warrant a separate advisory note outside the contract-drift audit scope.
