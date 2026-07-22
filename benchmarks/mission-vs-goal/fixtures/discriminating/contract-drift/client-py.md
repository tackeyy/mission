# client-py implementation notes

- Sends the `X-Sig` header exactly as specified.
- POST /v2/transfers: fires the request without an `Idempotency-Key` header;
  the wrapper generates one only for the bulk endpoint, and the single
  transfer path was never updated.
- Never retries POSTs.
- Status handling: maps the API enum to internal states using American
  spelling: `pending`, `settled`, `canceled`, `failed`. The mapping table
  matches on exact string equality against the wire value.
- Parses `expires_at` as epoch milliseconds.
- Sends an `X-Trace-Id` header on every request for distributed tracing.
