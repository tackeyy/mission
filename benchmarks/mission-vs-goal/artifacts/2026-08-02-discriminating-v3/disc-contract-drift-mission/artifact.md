# Disc Contract Drift Mission — API Contract Audit

## Mission

Audit three client implementations (`client-go`, `client-py`, `client-js`) against `api-spec.md` (source of truth) for **client-go.md**, **client-py.md**, **client-js.md** and `api-spec.md`, and report every breaking drift (behavior that violates the spec contract), while explicitly rejecting differences the spec permits (extension headers, case-insensitive header name matching). Fixtures read (exactly these four, no others under `benchmarks/mission-vs-goal/`):
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-go.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-js.md`

Mission complexity: Complex. Arm: mission. Mission profile: full.

## Plan

1. Extract structured spec requirements from `api-spec.md`: (R1) `X-Sig` auth header required, case-insensitive name matching permitted; (R2) `X-*` extension headers permitted (section 7); (R3) `Idempotency-Key` REQUIRED on every `POST /v2/transfers`; (R4) failed `POST /v2/transfers` MUST NOT be retried unless `Idempotency-Key` was supplied; (R5) `status` enum wire values are `pending`, `settled`, `cancelled` (British spelling), `failed`; (R6) `expires_at` is an integer already in epoch_ms (milliseconds); (R7) `id` field is a string (no other constraint stated).
2. For each client (`go`, `py`, `js`), match each requirement against the documented client behavior in parallel.
3. Classify every difference as **breaking** (violates a MUST/MUST NOT/REQUIRED clause) or **permitted** (covered by an explicit spec clause), citing the exact clause text for permitted cases.
4. Assemble the per-client drift table, breaking-drift section (with quoted fixture evidence), and rejected-candidates section (with quoted spec clause), covering every requirement for every client, including fully compliant rows.
5. Write this artifact to the required output path with all 8 required headings.

## Execution

### Step 1 — Spec requirements extracted (api-spec.md)

| ID | Requirement | Exact spec text |
|---|---|---|
| R1 | `X-Sig` header required for every request; name matching is case-insensitive | "Every request MUST carry the `X-Sig` header containing an HMAC of the body. Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." |
| R2 | Additional `X-*` extension headers are permitted | "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." |
| R3 | `Idempotency-Key` required on every `POST /v2/transfers` | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." |
| R4 | No retry of failed `POST /v2/transfers` without `Idempotency-Key` | "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." |
| R5 | `status` enum wire values, British spelling | "one of: `pending`, `settled`, `cancelled`, `failed`" / "The `status` enum uses British spelling `cancelled`." |
| R6 | `expires_at` is epoch_ms (milliseconds), not seconds | "`expires_at` \| integer \| epoch_ms (milliseconds since epoch, UTC)" / "treating it as seconds shifts expiry by three orders of magnitude." |
| R7 | `id` field is a string | "id \| string \|" (no further behavioral constraint stated) |

### Step 2 — Per-client matching (client-go.md, client-py.md, client-js.md)

Performed against each client's documented behavior; see the drift table below for the full per-requirement mapping, and the Evidence section for exact quotes.

### Step 3 — Breaking vs. permitted classification

Applied per row; see drift table `Classification` column and the Breaking-Drift / Rejected-Candidates sections below.

### Step 4 — Per-client drift table (all requirements × all clients, exhaustive)

| Client | Spec Requirement | Client Behavior | Breaking / Permitted / Compliant |
|---|---|---|---|
| client-go | R1: `X-Sig` header required | Sends signature in `X-Signature-V2` header instead (renamed during "v2 migration"); `X-Sig` is never sent | **Breaking** |
| client-go | R1 (casing sub-point): header name casing is case-insensitive | Sends all headers lowercase (`x-signature-v2`, `content-type`) | Permitted (casing alone; moot here since the header name itself is wrong — see Rejected Candidates) |
| client-go | R2: extension headers permitted | No extension headers documented | Compliant (not applicable) |
| client-go | R3: `Idempotency-Key` required on every POST | No `Idempotency-Key` is ever attached to `POST /v2/transfers` (not on initial send, not on retries) | **Breaking (inference — see Assumptions)** |
| client-go | R4: no retry without `Idempotency-Key` | Retries failed POST up to 3 times on any 5xx, with no `Idempotency-Key` attached | **Breaking** |
| client-go | R5: status enum British spelling | `switch` over `pending`, `settled`, `cancelled`, `failed` | Compliant |
| client-go | R6: `expires_at` epoch_ms | "Parses `expires_at` as epoch milliseconds" | Compliant |
| client-go | R7: `id` field | Not addressed in fixture | Unmeasured (no evidence either way) |
| client-py | R1: `X-Sig` header required | "Sends the `X-Sig` header exactly as specified" | Compliant |
| client-py | R2: extension headers permitted | Sends `X-Trace-Id` on every request | Permitted |
| client-py | R3: `Idempotency-Key` required on every POST | "fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated" | **Breaking** |
| client-py | R4: no retry without `Idempotency-Key` | "Never retries POSTs" | Compliant (never retrying trivially satisfies "MUST NOT retry unless key supplied" — see Rejected Candidates) |
| client-py | R5: status enum British spelling | Maps wire values to internal states using American spelling `canceled` (vs. wire `cancelled`), via **exact string equality** against the wire value | **Breaking** |
| client-py | R6: `expires_at` epoch_ms | "Parses `expires_at` as epoch milliseconds" | Compliant |
| client-py | R7: `id` field | Not addressed in fixture | Unmeasured |
| client-js | R1: `X-Sig` header required | "Sends the `X-Sig` header exactly as specified" | Compliant |
| client-js | R2: extension headers permitted | Sends `X-Trace-Id` "for tracing" | Permitted |
| client-js | R3: `Idempotency-Key` required on every POST | "attaches a UUID `Idempotency-Key` on every call" | Compliant |
| client-js | R4: no retry without `Idempotency-Key` | "never retries without one" | Compliant |
| client-js | R5: status enum British spelling | `switch` over `pending`, `settled`, `cancelled`, `failed` | Compliant |
| client-js | R6: `expires_at` epoch_ms | `new Date(res.expires_at * 1000)` — treats `expires_at` as epoch **seconds** and multiplies by 1000 | **Breaking** |
| client-js | R7: `id` field | Not addressed in fixture | Unmeasured |

## Review

Self-review against the task validator's three required elements, performed as a single-pass internal check (Complex-mission profile; no external reviewer fixtures are in scope for this run):

1. **Per-client drift table** — present above (Step 4), covering 22 rows total: client-go has 8 rows (R1 split into the main auth-header row plus a separate casing sub-point row), client-py has 7 rows, client-js has 7 rows — including compliant and unmeasured rows, so omissions are visible.
2. **Breaking-drift section with quoted evidence** — present below (Evidence → Breaking Drift), 6 breaking findings total (3 for client-go, 2 for client-py, 1 for client-js), each with an exact fixture quote plus the violated spec clause.
3. **Rejected-candidates section citing the permitting spec clause** — present below (Evidence → Rejected Candidates), 4 candidates, each with the exact permitting clause quoted.

Cross-check for internal consistency: every "Breaking" row in Step 4's table has a matching entry in Breaking Drift; every "Permitted" row (and the two behaviors that only *look* like violations but are compliant by omission) has a matching entry in Rejected Candidates. No table row is unaccounted for.

## Score

This is a controlled benchmark run; no external mission-reviewer/mission-scorer sub-skills were invoked (single-session, budget-bounded execution). Self-assessed against the stated validator only:

| Validator requirement | Met? |
|---|---|
| Per-client drift table (client, spec requirement, client behavior, breaking/permitted) | Yes — Step 4 table, 22 rows (client-go: 8, client-py: 7, client-js: 7) |
| Breaking-drift section with quoted evidence | Yes — 6 findings, each with verbatim fixture quotes |
| Rejected-candidates section citing permitting spec clause | Yes — 4 candidates, each with verbatim spec clause |
| Exhaustive coverage (including compliant items) | Yes — all 7 requirements listed per client, compliant/unmeasured rows included |

No numeric composite score was computed via `mission-reviewer`/`review-finalize` in this run; this is stated explicitly rather than fabricated (see Assumptions).

## Stop Decision

Task-scoped completion: the artifact is written once at the required path, contains all required headings, and satisfies all three structural elements the validator checks (drift table, breaking-drift with evidence, rejected-candidates with citations), with exhaustive per-client coverage. No further iteration was judged necessary — all in-scope fixture content has been mapped to a table row. Stopping here (iteration 1 of max 2).

## Evidence

### Breaking Drift (6 findings)

1. **client-go — wrong/missing auth header (R1).** Spec: "Every request MUST carry the `X-Sig` header." Client: "Signs every request and sends the signature in the `X-Signature-V2` header (renamed from the spec header during the v2 migration; the old name felt ambiguous)." → `X-Sig` is never sent; `X-Signature-V2` is a rename of the required header, not an additional extension header, so it is not covered by the section 7 extension clause. **Breaking.**

2. **client-go — missing `Idempotency-Key` (R3).** Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." Client: "No idempotency header is attached to retries because the team understood transfers to be safe to retry on 5xx" — no `Idempotency-Key` generation is described anywhere in the client notes, on the initial POST or otherwise. **Breaking.**

3. **client-go — retries without `Idempotency-Key` (R4).** Spec: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." Client: "Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with exponential backoff. No idempotency header is attached to retries." **Breaking.**

4. **client-py — missing `Idempotency-Key` (R3).** Spec: "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated." **Breaking.**

5. **client-py — enum spelling mismatch breaks exact-match lookup (R5).** Spec: "The `status` enum uses British spelling `cancelled`." Client: "Status handling: maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value." → the wire value is always `cancelled` (British); the client's lookup table key is `canceled` (American); exact string equality means the `cancelled` wire value will never match the `canceled` table entry. **Breaking.**

6. **client-js — `expires_at` unit mismatch (R6).** Spec: "`expires_at` \| integer \| epoch_ms (milliseconds since epoch, UTC)... treating it as seconds shifts expiry by three orders of magnitude." Client: "Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed the field is epoch seconds and multiplies by 1000 before constructing the Date." → the value is already epoch_ms; multiplying by 1000 inflates it by exactly the three orders of magnitude the spec warns about. **Breaking.**

### Rejected Candidates (non-findings, with permitting clause)

1. **client-go — lowercase header casing (`x-signature-v2`, `content-type`).** Looks suspicious because it differs from the spec's documented header capitalization. **Rejected**: permitted by "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." (api-spec.md, Authentication section). Note: this permits the *casing*, not the *name* — the name change (`X-Signature-V2` vs. `X-Sig`) is a separate, breaking issue (see Breaking Drift #1).

2. **client-py — `X-Trace-Id` header on every request.** Looks suspicious as an undocumented header not in the spec's endpoint tables. **Rejected**: permitted by "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers)... Sending an extension header is never a contract violation." (api-spec.md, Extension clause / section 7).

3. **client-js — `X-Trace-Id` header for tracing.** Same shape as candidate 2. **Rejected**: permitted by the same Extension clause (section 7) quoted above.

4. **client-py — "Never retries POSTs."** Looks suspicious because it differs from client-go's (non-compliant) retry behavior and could be mistaken for a missing feature. **Rejected**: the spec only constrains retries ("clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header"); it never requires that clients retry. Never retrying trivially satisfies the MUST-NOT clause and is not a contract violation.

## Assumptions

- **`id` field (R7):** none of the three client fixtures describe any handling of the `id` field, so it is recorded as "Unmeasured" in the drift table rather than assumed compliant or non-compliant — there is no fixture evidence either way.
- **No external mission-reviewer scoring was run** in this benchmark session (single-session, budget-bounded, task-scoped run per the benchmark harness rules); the Score section reflects a self-check against the validator's stated structural requirements only, not an independent peer score.
- **Scope discipline:** only the four named fixtures were read; no other file under `benchmarks/mission-vs-goal/` (task definitions, scoring configuration, answer keys) was opened, grepped, or listed, per the task's explicit restriction.
- **client-go retry scenario:** the fixture does not state whether the *initial* `POST /v2/transfers` from client-go carries an `Idempotency-Key`; it only states none is attached "to retries." This artifact treats the absence of any `Idempotency-Key` generation logic anywhere in the client-go notes as evidence that no key is ever sent (on first attempt or retry), since no key-generation step is described at all. This is stated as an inference from silence, not a directly quoted fact about the first attempt.
