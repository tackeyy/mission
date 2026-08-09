# Pass-rate metrics

`mission-state.py stats` and `mission-audit.py` use the same exclusive session-health and terminal-outcome reducers. They expose all-session, completed-session, implementer-quality, and evidence-completion metrics without mixing roles.

## Rates

| Field | Numerator | Denominator |
| --- | --- | --- |
| `raw_pass_rate` | passed sessions | every selected session |
| `completed_pass_rate` | passed sessions | `pass + halt + abandoned + stale` |
| `implementer_pass_rate` | implementer records with `completed_pass` | implementer records with `completed_pass`, `failed`, or `incomplete` |
| `evidence_completion_rate` | checker/planning/analyze records with `completed_evidence` | checker/planning/analyze records with `completed_evidence`, `failed`, or `incomplete` |

Both rates have explicit `_numerator` and `_denominator` fields. A zero denominator produces JSON `null`, never `NaN` or infinity.

Fresh live work is excluded only from the completed denominator. A stale live session is included as non-passing completed health debt, so it cannot make the completed population look healthy by disappearing. No current session is implicitly excluded; a session is omitted only when it is outside an explicit root/period selection or removed by identity-based deduplication.

`release` records remain visible in `role_counts` and `terminal_outcome_counts`, but are excluded from both role-specific rate denominators. In `mission-audit.py`, `actionable_pass_rate*` is retained as a compatibility alias for `implementer_pass_rate*`, and `low-pass-rate` uses that role-aware population.

## Terminal outcomes

Schema v3 terminal writers persist one of:

`completed_pass`, `completed_evidence`, `blocked_external`, `awaiting_approval`, `stale_superseded`, `failed`, `incomplete`, `user_aborted`, or `routed_elsewhere`.

`evidence-submitted` maps to `completed_evidence` only for checker, planning, and analyze roles. It maps to `incomplete` for implementer and release roles. `partial-done` also maps to `incomplete`; `routed-goal` maps to the non-comparative `routed_elsewhere`. Active records have no terminal outcome. An explicit outcome that contradicts `passes`, `loop_active`, `halt_reason`, role, or halt category fails closed as `failed`.

Legacy schema v1/v2 records are derived at read time. Audit and stats never rewrite them merely to add `terminal_outcome`. `terminal_count` must equal the sum of `terminal_outcome_counts`; `non_terminal_count` records active states outside that conservation total.

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
Quality consumers should use `implementer_pass_rate*`; evidence workflow consumers should use `evidence_completion_rate*`.
