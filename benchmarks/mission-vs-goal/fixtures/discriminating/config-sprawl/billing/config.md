# billing-service configuration

Owner: payments team. Last reviewed 2026-06-11.

| Constant | Value | Note |
|---|---|---|
| CONNECT_TIMEOUT_MS | 12000 | Override: PSP provider p99 latency is 9s; approved PLAT-482 |
| REQUEST_RETRY_MAX | 5 | |
| SESSION_TTL_SEC | 3600 | |
| DB_POOL_SIZE | 64 | |
| BATCH_WINDOW_MS | 500 | |
| TLS_MIN_VERSION | TLSv1.2 | |
| CACHE_TTL_SEC | 300 | |
| IDEMPOTENCY_WINDOW_SEC | 86400 | |
| LOG_RETENTION_DAYS | 30 | |

Operational notes: the idempotency window was widened while debugging
duplicate settlement webhooks in 2026-03. The connect timeout override
follows the platform override protocol with approval reference PLAT-482.
