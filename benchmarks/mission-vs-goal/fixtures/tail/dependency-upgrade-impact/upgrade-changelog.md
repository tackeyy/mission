# relaykit v3.0.0 changelog (upstream, verbatim)

1. `parseConfig` is now strict: unknown keys raise `ConfigKeyError` (v2
   silently ignored them).
2. The `onRetry` hook signature changed from `(attempt, error)` to a single
   `(context)` object; two-argument callbacks are no longer invoked.
3. `publish()` default payload encoding changed from msgpack to JSON. Pin a
   codec explicitly to keep the old wire format; the codec pin must be set
   before the first `publish()` call.
4. `Queue.drain()` is now async and returns a Promise; synchronous callers
   will no longer block until the queue is empty.
5. `Logger.warnOnce` has been removed; use `Logger.warn` with a dedupe key.
6. `connect()` default timeout lowered from 30s to 10s. Call sites passing an
   explicit timeout are unaffected.
7. Internal buffer pooling rewritten; ~12% lower allocation rate.
8. New `Queue.peek()` API.
9. Documentation moved to a new site.
10. Minimum supported runtime raised to LTS.
