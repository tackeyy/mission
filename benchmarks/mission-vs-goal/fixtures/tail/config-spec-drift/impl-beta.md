# Service Beta — effective configuration (excerpt from deployed config)

```text
# beta/config/production.env
REQUEST_TIMEOUT_MS=3000
MAX_RETRIES=3
RETRY_BACKOFF_STRATEGY=constant-interval
RETRY_BACKOFF_BASE_MS=250
QUEUE_MAX_DEPTH=10000
TLS_MIN_VERSION=1.3
HEALTH_CHECK_INTERVAL_SECONDS=75
ENABLE_LEGACY_AUTH=false
IDLE_TIMEOUT_TICKS=5400
LOG_LEVEL=info
DB_POOL_SIZE_PER_REPLICA=32
```

Beta counts idle time in scheduler ticks; the scheduler runs at 60 ticks per
second. Backoff strategy names follow the retry library's enum
(`constant-interval`, `exponential`, `decorrelated-jitter`).
