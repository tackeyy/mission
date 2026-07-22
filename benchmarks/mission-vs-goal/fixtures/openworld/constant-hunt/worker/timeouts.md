# Worker timeouts

The background worker uses its own timeout block.

| Setting | Default |
|---|---|
| CONNECT_TIMEOUT_MS | 6500 |
| READ_TIMEOUT_MS | 9000 |

The worker sets `CONNECT_TIMEOUT_MS = 6500` because it dials a slower internal
host. `READ_TIMEOUT_MS = 9000` is kept identical to the gateway so the read
budget stays uniform across processes.
