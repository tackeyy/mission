# Change history around incident 2417

| Time (UTC) | Change | Scope |
|---|---|---|
| 01:50 | assets-web 2024.11.3 | Static asset bundle only; no API, config, or schema changes. |
| 01:55 | checkout-workers config rollout | `worker_concurrency` raised from 8 to 16; DB pool size unchanged (max 40). |
| 02:00 | nightly-reindex scheduled job | Rebuilds indexes on `orders` and `order_items`; takes table locks in v1 mode. |
| (standing) | payments-gw.internal certificate | Issued 90 days ago; renewal ticket open, unassigned. |
