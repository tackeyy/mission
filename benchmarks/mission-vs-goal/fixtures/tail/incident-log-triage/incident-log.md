# Incident 2417 — aggregated log excerpt (times UTC)

```text
01:42:10 api-edge      WARN  clock skew 12ms against ntp pool (recurring)
01:50:02 deploy-bot    INFO  assets-web release 2024.11.3 rolled out (static bundle only)
01:55:31 config-svc    INFO  rollout complete: worker_concurrency 8 -> 16 (checkout-workers)
01:58:44 checkout-db   WARN  connection pool utilization 88% (max 40)
02:00:00 job-runner    INFO  nightly-reindex started (tables: orders, order_items)
02:02:17 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire
02:03:05 checkout-api  ERROR upstream timeout talking to checkout-db
02:04:52 orders-api    ERROR lock wait timeout exceeded on table orders
02:07:33 api-edge      WARN  clock skew 11ms against ntp pool (recurring)
02:09:41 orders-api    ERROR lock wait timeout exceeded on table orders
02:13:20 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)
02:13:21 checkout-api  ERROR payment authorization failed: TLS handshake
02:15:48 checkout-db   ERROR connection pool exhausted (max 40); rejecting acquire
02:18:00 alerting      PAGE  checkout error rate 34% (threshold 5%)
02:22:09 payments-gw   ERROR x509: certificate has expired (peer: payments-gw.internal)
02:24:40 orders-api    ERROR lock wait timeout exceeded on table orders
```
