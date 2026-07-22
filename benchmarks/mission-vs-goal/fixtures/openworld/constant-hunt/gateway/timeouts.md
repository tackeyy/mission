# Gateway timeouts

The gateway process reads these network timeouts at startup.

| Setting | Default |
|---|---|
| CONNECT_TIMEOUT_MS | 4000 |
| READ_TIMEOUT_MS | 9000 |

The gateway inherits `CONNECT_TIMEOUT_MS = 4000` from the shared network
defaults and does not override it. `READ_TIMEOUT_MS` is set to 9000 to match the
upstream service budget.
