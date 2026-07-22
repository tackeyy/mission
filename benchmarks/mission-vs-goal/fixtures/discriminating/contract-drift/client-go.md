# client-go implementation notes

- Signs every request and sends the signature in the `X-Signature-V2` header
  (renamed from the spec header during the v2 migration; the old name felt
  ambiguous).
- Sends all headers lowercase (`x-signature-v2`, `content-type`).
- Retry policy: on any 5xx, retries POST /v2/transfers up to 3 times with
  exponential backoff. No idempotency header is attached to retries because
  the team understood transfers to be safe to retry on 5xx.
- Parses `expires_at` as epoch milliseconds.
- Status handling: switch over `pending`, `settled`, `cancelled`, `failed`.
