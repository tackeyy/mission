# Platform Canonical Defaults (PLAT-CONFIG v4)

Every service MUST use these values unless an override is documented in the
service config with an approval reference (`PLAT-<id>`). Undocumented
divergence is a compliance violation.

| Constant | Canonical value | Rationale |
|---|---|---|
| CONNECT_TIMEOUT_MS | 4000 | Upstream LB kills idle connects at 5s |
| REQUEST_RETRY_MAX | 5 | Backoff budget fits the 30s request SLA |
| SESSION_TTL_SEC | 3600 | Security review SR-2026-02 |
| DB_POOL_SIZE | 64 | Sized for the shared PgBouncer tier |
| BATCH_WINDOW_MS | 500 | Downstream consumer throughput contract |
| TLS_MIN_VERSION | TLSv1.2 | Security baseline; TLSv1.1 is end-of-life |
| CACHE_TTL_SEC | 300 | Balance of freshness and origin load |
| IDEMPOTENCY_WINDOW_SEC | 600 | Duplicate-suppression window for retries |
| LOG_RETENTION_DAYS | 30 | Data-minimization policy DM-9 |

Override protocol: the service config must state the constant, the overridden
value, the reason, and the approval reference. Overrides without an approval
reference are treated as violations.
