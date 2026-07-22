# Shared network defaults

These are the canonical network defaults every process is expected to inherit
unless it documents a deliberate override.

| Setting | Canonical default |
|---|---|
| CONNECT_TIMEOUT_MS | 4000 |
| READ_TIMEOUT_MS | 9000 |

Any process whose `CONNECT_TIMEOUT_MS` differs from 4000 is a divergence that
must be justified in that process's own notes.
