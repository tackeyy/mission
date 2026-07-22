# search-service configuration

Owner: discovery team. Last reviewed 2026-06-27.

| Constant | Value | Note |
|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | |
| REQUEST_RETRY_MAX | 5 | |
| SESSION_TTL_SEC | 3600 | |
| DB_POOL_SIZE | 128 | |
| BATCH_WINDOW_MS | 500 | |
| TLS_MIN_VERSION | TLSv1.2 | |
| CACHE_TTL_SEC | 30 | Override: suggestion freshness SLA requires 30s; approved PLAT-511 |
| IDEMPOTENCY_WINDOW_SEC | 600 | |
| LOG_RETENTION_DAYS | 45 | |

Operational notes: the pool was doubled during a 2026-05 load test and never
reverted. Query logs are kept 45 days to debug relevance regressions; nobody
filed the retention change with the platform team. The cache TTL override
follows the override protocol with approval reference PLAT-511.
