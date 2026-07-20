# Pass-rate metrics

`mission-state.py stats` and `mission-audit.py` use the same exclusive session-health classification and expose both all-session and completed-session quality.

## Rates

| Field | Numerator | Denominator |
| --- | --- | --- |
| `raw_pass_rate` | passed sessions | every selected session |
| `completed_pass_rate` | passed sessions | `pass + halt + abandoned + stale` |

Both rates have explicit `_numerator` and `_denominator` fields. A zero denominator produces JSON `null`, never `NaN` or infinity.

Fresh live work is excluded only from the completed denominator. A stale live session is included as non-passing completed health debt, so it cannot make the completed population look healthy by disappearing. No current session is implicitly excluded; a session is omitted only when it is outside an explicit root/period selection or removed by identity-based deduplication.

## Exclusive health counts

- `active_count`: fresh live sessions with a finite scoring checkpoint.
- `active_no_score_count`: fresh live sessions without a finite scoring checkpoint.
- `stale_count`: live sessions with a missing, malformed, future, or threshold-expired progress timestamp.
- `halt_count`: terminal halted sessions.
- `abandoned_count`: inactive sessions without pass or halt evidence.

`incomplete_count` remains the compatibility total of `active_count + active_no_score_count + stale_count`. Orphan cleanup records that have already halted remain in `halt_count` and in the completed denominator.

## Compatibility aliases

`pass_rate` is deprecated because its historical meaning differs by command:

- In `mission-state.py stats`, `pass_rate`, `pass_rate_numerator`, and `pass_rate_denominator` alias the raw fields.
- In `mission-audit.py`, the same names alias the completed fields.

New consumers should always select `raw_pass_rate*` or `completed_pass_rate*` explicitly.
