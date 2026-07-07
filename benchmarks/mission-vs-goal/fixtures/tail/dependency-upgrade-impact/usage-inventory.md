# relaykit usage inventory (repo-wide grep, current main)

| Call site | Usage | Detail |
|---|---|---|
| `services/ingest/loader` | `parseConfig(raw)` | Config file still contains the deprecated `flush_interval` key kept "for reference". |
| `services/dispatch/retry-metrics` | `onRetry((attempt, error) => ...)` | Two-argument callback records retry counters. |
| `services/edge-cache/consumer` | subscribes to `publish()` output | Decodes payloads with a msgpack reader; no codec pin is set anywhere in the repo. |
| `scripts/shutdown-hook` | `queue.drain()` | Called synchronously as the last line before process exit. |
| `services/*/logging` | `Logger.warn` | No `warnOnce` call sites found (grep returned zero). |
| `services/*/bootstrap` | `connect({ timeout: 20_000 })` | Every `connect()` call site passes an explicit timeout. |
| `services/billing/exporter` | `Queue.peek()` (planned) | Not yet using it; listed from the design doc. |
