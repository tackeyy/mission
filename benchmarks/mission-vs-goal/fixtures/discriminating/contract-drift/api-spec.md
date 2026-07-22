# Transfer API Specification v2 (source of truth)

## Authentication
Every request MUST carry the `X-Sig` header containing an HMAC of the body.
Header names are matched case-insensitively per RFC 9110; clients MAY send
any casing.

## Extension clause (section 7)
Clients MAY send additional `X-*` extension headers not defined here (for
example tracing headers). Servers ignore unknown extension headers. Sending
an extension header is never a contract violation.

## Endpoints

### POST /v2/transfers
Creates a transfer. This endpoint is NOT idempotent by itself: clients MUST
NOT retry a failed POST /v2/transfers unless they supply the required
`Idempotency-Key` header. `Idempotency-Key` is REQUIRED on every
POST /v2/transfers request.

### GET /v2/transfers/{id}
Returns the transfer. Response fields:

| Field | Type | Semantics |
|---|---|---|
| id | string | |
| status | enum | one of: `pending`, `settled`, `cancelled`, `failed` |
| expires_at | integer | epoch_ms (milliseconds since epoch, UTC) |

The `status` enum uses British spelling `cancelled`. The `expires_at` field
is always epoch_ms; treating it as seconds shifts expiry by three orders of
magnitude.
