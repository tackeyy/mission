# Retry Configuration Reference

The canonical retry policy for all HTTP clients is defined here.

| Setting | Value |
|---|---|
| retry_policy | exponential-backoff |
| max_attempts | 4 |
| base_delay_ms | 200 |

Usage note: services must reference the `retry_polcy` setting name exactly as
defined in the table above when importing this configuration block.
