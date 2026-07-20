# ADR-004: Bounded Activity-Segment Observability

## Status

Accepted

## Date

2026-07-21

## Context

Session wall-clock and `phase_durations_sec` show where elapsed time was
attributed, but they cannot separate work from external, approval, reviewer, or
idle waits. This makes it difficult to improve execution speed without weakening
the review and scoring gates. A crash can also leave an open interval whose end
is unknowable; treating the entire restart gap as work or idle would invent a
cause.

## Decision

Mission records one explicit open activity and a bounded history of closed
segments. The activity kinds are `active`, `external-wait`, `approval-wait`,
`reviewer-wait`, and `idle`. Every start requires a kind-specific reason enum;
an optional detail is control-character stripped, whitespace normalized, and
bounded to 160 characters. Unknown causes are never inferred.

`mission-state.py activity start` closes the previous activity and opens the
next one under the existing state lock. `activity end`, phase transitions,
`mark-passes`, and `mark-halt` close the open segment under that same lock.
Repeated starts and ends are idempotent. A backwards timestamp fails without
writing state, and a terminal state rejects new activity. Terminal control is
fail-open with respect to observability: malformed open measurement is removed
and counted in `activity_anomaly_counts`, but never prevents pass or halt.
Malformed terminal phase timing is likewise counted as
`invalid-phase-terminal`; existing duration evidence is preserved and no
replacement duration is fabricated.

On reinitialization, PID refresh, stale/orphan cleanup, or Stop-hook automatic
halt after a crash, an open segment closes at the last valid `updated_at`
between its start and the control time. The later gap is reported as
`activity_unobserved_gap_sec`; it is not classified as work or idle. Explicit
`mark-passes`, `mark-halt`, and `halt --all` instead close at their control time
because the caller is declaring a currently observed transition. A repeated
resume does not add duration again. Automatic stale halts retain the pre-halt
phase in `resume_target_phase`; PID refresh restores it and restarts
`phase_started_at`, while an explicit halt is never reactivated automatically.

The latest 32 closed segments remain in `activity_segments` for diagnosis.
Fixed-size `activity_rollup` maps preserve all earlier totals, so state growth
and rewrite cost remain bounded. Existing `phase_durations_sec` retains its
wall-clock semantics and is not incremented by activity commands.

`mission-state.py stats` and `mission-audit.py` call the same reducer. They
report:

- task and phase p50/p90 using linear interpolation R7;
- totals by activity kind and explicit wait reason;
- observed, unclassified, open, invalid, and unobserved-gap measures;
- coverage and total-consistency diagnostics.

Coverage includes the persisted-as-of window from `phase_started_at` through
`updated_at` for a nonterminal current phase, so observed activity cannot produce
a percentage above 100% merely because that phase has not transitioned yet.
Live and archived copies use the same `(project_root, session_id, mission_id)`
identity and the same status/newest/path precedence before aggregation. Project
roots are path-normalized; when absent, the state file's owning project path is
used so equal session identifiers in different projects are not collapsed.

Task samples use `mission_id`, falling back to `unknown`. Open segments are
reported but excluded from duration distributions. Malformed, negative,
non-finite, future-enum, missing or non-map rollup aggregates, and inconsistent
rollup values are excluded and counted as invalid. Bulk terminal writers sample
their control time after lock acquisition and never write a timestamp before the
latest persisted update. Legacy states remain readable: their phase time is
unclassified and no wait reason is fabricated.

Collection and grouping are linear in the number of states and segments. Exact
R7 percentiles sort each task/phase sample group, so percentile calculation is
`O(N log N)` in the largest group; bounded state keeps per-session ingestion
constant-size.

## Quality and control boundary

This feature is measurement only. It does not change reviewer count, thresholds,
findings evidence, agreement checks, `open_high`, pass/fail state, automatic
retry, or watchdog behavior. Speed improvements must be evaluated from the
recorded distributions while the existing quality gates remain unchanged.

## Consequences

Operators can identify whether elapsed time concentrates in execution or a
specific wait category and compare task/phase distributions over multiple
sessions. Historical sessions without activity data remain comparable at the
phase level, with their missing classification made explicit rather than
guessed.
