# portfolio-std-contract — API Contract Drift Audit (arm: claude_code_goal_command, rep3)

## Goal

Audit the client implementation (`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md`) against the API specification (`benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md`, the source of truth). Find every breaking drift in client-py, and reject spec-permitted differences as non-findings citing the permitting clause. Deliverable: this single artifact with a drift table, a breaking-drift section with quoted evidence, and a rejected-candidates section.

## Result

Audit complete. Exactly the two fixture files named in the task prompt were read; no other files under `benchmarks/mission-vs-goal/` were accessed.

- **Confirmed breaking drifts: 2** — (B1) missing required `Idempotency-Key` header on `POST /v2/transfers`; (B2) `status` enum mismatch: client matches `canceled` (American spelling) by exact string equality while the wire value is `cancelled`.
- **Rejected candidates (spec-permitted, non-findings): 4** — `X-Trace-Id` extension header, `X-Sig` header handling, `expires_at` epoch_ms parsing, and no-retry behavior on POST.

### Drift table

| # | Item | Client behavior | Spec requirement | Verdict |
|---|---|---|---|---|
| B1 | `Idempotency-Key` header | Not sent on `POST /v2/transfers` ("fires the request without an `Idempotency-Key` header") | "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." | **Breaking drift** |
| B2 | `status` enum value | Matches `canceled` (American spelling) via "exact string equality against the wire value" | Enum is `pending`, `settled`, `cancelled`, `failed`; "The `status` enum uses British spelling `cancelled`." | **Breaking drift** |
| R1 | `X-Trace-Id` header | "Sends an `X-Trace-Id` header on every request" | Permitted by Extension clause (section 7): extension `X-*` headers are "never a contract violation" | Rejected (non-finding) |
| R2 | `X-Sig` header | "Sends the `X-Sig` header exactly as specified." | Required by Authentication section; compliant | Rejected (non-finding) |
| R3 | `expires_at` parsing | "Parses `expires_at` as epoch milliseconds." | "The `expires_at` field is always epoch_ms" | Rejected (non-finding) |
| R4 | POST retry behavior | "Never retries POSTs." | Spec only forbids retrying without `Idempotency-Key`; not retrying at all is compliant | Rejected (non-finding) |

### Breaking drifts (with quoted evidence)

**B1 — Missing required `Idempotency-Key` header on `POST /v2/transfers`**

- Spec (source of truth): "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request."
- Client: "POST /v2/transfers: fires the request without an `Idempotency-Key` header; the wrapper generates one only for the bulk endpoint, and the single transfer path was never updated."
- Why breaking: the client omits a header the spec marks REQUIRED on every request to this endpoint. Every single-transfer POST violates the contract.

**B2 — `status` enum spelling mismatch: `canceled` vs `cancelled`**

- Spec (source of truth): `status` is "one of: `pending`, `settled`, `cancelled`, `failed`", and "The `status` enum uses British spelling `cancelled`."
- Client: "maps the API enum to internal states using American spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table matches on exact string equality against the wire value."
- Why breaking: the wire value for a cancelled transfer is `cancelled`, but the client's exact-string mapping table only contains `canceled`. The wire value `cancelled` will never match, so cancelled transfers fail to map to an internal state. (Note: `pending`, `settled`, `failed` are identical in both spellings and match correctly; only `cancelled` breaks.)

### Rejected candidates (spec-permitted differences, non-findings)

**R1 — `X-Trace-Id` extension header.** Client: "Sends an `X-Trace-Id` header on every request for distributed tracing." Permitted by the Extension clause (section 7): "Clients MAY send additional `X-*` extension headers not defined here (for example tracing headers). Servers ignore unknown extension headers. Sending an extension header is never a contract violation." `X-Trace-Id` is an `X-*` extension header, so this is explicitly not a violation.

**R2 — `X-Sig` header.** Client: "Sends the `X-Sig` header exactly as specified." This satisfies the Authentication requirement "Every request MUST carry the `X-Sig` header containing an HMAC of the body." Additionally, the spec permits any casing: "Header names are matched case-insensitively per RFC 9110; clients MAY send any casing." Compliant — non-finding.

**R3 — `expires_at` parsed as epoch milliseconds.** Client: "Parses `expires_at` as epoch milliseconds." Spec: "expires_at | integer | epoch_ms (milliseconds since epoch, UTC)" and "The `expires_at` field is always epoch_ms". The client matches the spec exactly (the seconds-vs-milliseconds trap does not apply here) — non-finding.

**R4 — Never retrying POSTs.** Client: "Never retries POSTs." Spec: "clients MUST NOT retry a failed POST /v2/transfers unless they supply the required `Idempotency-Key` header." The spec constrains retries; it never requires them. Not retrying at all trivially satisfies the MUST NOT clause — non-finding. (The independent omission of `Idempotency-Key` on the initial request is already captured as B1.)

## Evidence

All evidence quotes above are taken verbatim from the two permitted fixture files:

- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/api-spec.md` — spec quotes: `X-Sig` / "Header names are matched case-insensitively per RFC 9110" (Authentication); "never a contract violation" (Extension clause, section 7); "`Idempotency-Key` is REQUIRED on every POST /v2/transfers request." (POST /v2/transfers); enum `pending`, `settled`, `cancelled`, `failed` and `expires_at` "epoch_ms" (GET /v2/transfers/{id}).
- `benchmarks/mission-vs-goal/fixtures/discriminating/contract-drift/client-py.md` — client quotes: "without an `Idempotency-Key` header", "American spelling: `pending`, `settled`, `canceled`, `failed`", "exact string equality against the wire value", "Parses `expires_at` as epoch milliseconds.", "Sends an `X-Trace-Id` header", "Sends the `X-Sig` header exactly as specified.", "Never retries POSTs."

Unmeasured: no code was executed and no live API traffic was observed; the audit is a static comparison of the two fixture documents only. Runtime behavior beyond what the fixture notes state is unmeasured.

## Assumptions

- The fixture `client-py.md` implementation notes accurately and completely describe the client's behavior; behaviors not mentioned in the notes are assumed absent/unknown and are not audited.
- `api-spec.md` v2 is the sole source of truth, per the task prompt.
- "Breaking drift" means a client behavior that violates a MUST/REQUIRED clause of the spec or misinterprets a spec-defined wire value in a way that produces incorrect behavior.
- The bulk endpoint mentioned in the client notes is out of scope: the spec provided defines only `POST /v2/transfers` and `GET /v2/transfers/{id}`.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-contract-claude_code_goal_command-rep3.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition) plus the validator-required drift table, breaking-drift section with quoted evidence, and rejected-candidates section. Both permitted fixtures were read; no commits, pushes, package installs, or network access occurred. Task complete — stopping.
