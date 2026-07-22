# notify-service configuration

Owner: messaging team. Last reviewed 2026-06-20.

| Constant | Value | Note |
|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | |
| REQUEST_RETRY_MAX | 2 | Override: at-most-once delivery guarantee; approved PLAT-390 |
| SESSION_TTL_SEC | 3600 | |
| DB_POOL_SIZE | 64 | |
| BATCH_WINDOW_MS | 250 | |
| TLS_MIN_VERSION | TLSv1.2 | |
| CACHE_TTL_SEC | 300 | |
| IDEMPOTENCY_WINDOW_SEC | 600 | |
| LOG_RETENTION_DAYS | 30 | |

Operational notes: the batch window was halved to reduce push latency during
the 2026-06 campaign. The retry override follows the override protocol with
approval reference PLAT-390.
