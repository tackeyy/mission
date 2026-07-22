# auth-service configuration

Owner: identity team. Last reviewed 2026-05-02.

| Constant | Value | Note |
|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | |
| REQUEST_RETRY_MAX | 5 | |
| SESSION_TTL_SEC | 7200 | |
| DB_POOL_SIZE | 64 | |
| BATCH_WINDOW_MS | 500 | |
| TLS_MIN_VERSION | TLSv1.1 | legacy SDK compat |
| CACHE_TTL_SEC | 300 | |
| IDEMPOTENCY_WINDOW_SEC | 600 | |
| LOG_RETENTION_DAYS | 30 | |

Operational notes: session length was extended during the 2026-04 login
incident and the change was kept afterwards. The TLS floor is pinned for an
older mobile SDK; the SDK deprecation ticket is still open.
