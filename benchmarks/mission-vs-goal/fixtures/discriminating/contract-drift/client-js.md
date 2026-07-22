# client-js implementation notes

- Sends the `X-Sig` header exactly as specified, plus `X-Trace-Id` for
  tracing.
- POST /v2/transfers: attaches a UUID `Idempotency-Key` on every call and
  never retries without one.
- Status handling: switch over `pending`, `settled`, `cancelled`, `failed`.
- Expiry handling: `new Date(res.expires_at * 1000)` — the author assumed
  the field is epoch seconds and multiplies by 1000 before constructing the
  Date.
