# Goal dispatch provider

Adaptive routing decides whether a Simple task needs the mission loop. Goal dispatch
only chooses how a task is completed after that routing decision; it does not change
the routing conditions, exclusions, scoring gates, or the meaning of a mission pass.

## Modes

- `inline` is the default and preserves the portable goal contract: Goal, Result,
  Evidence, Assumptions, and Stop Condition.
- `host-native` delegates completion to the current host's native goal mechanism.
  Claude Code guidance uses `/goal <objective>`; Codex guidance registers the
  objective in goal mode.

If `host-native` is configured but the host cannot be detected, routing remains
successful and falls back to `inline`. The verdict records
`goal_dispatch_effective: inline` and a `goal_dispatch_fallback_reason`. Invalid or
unreadable configuration also warns and fails safe to `inline`.

## Configuration

Project configuration lives at `.mission/routing.yml` and user configuration at
`~/.config/mission/routing.yml`. The version-1 schema is intentionally minimal:

```yaml
version: 1
goal_dispatch: host-native
```

Only `inline` and `host-native` are valid. Unknown keys, unsupported versions,
invalid values, and malformed lines produce a warning and select `inline`.

Precedence, highest first:

1. An explicit directive in the mission text: `goal_dispatch: inline` or
   `goal_dispatch: host-native`.
2. `mission-state.py init --goal-dispatch <mode>`.
3. Project `.mission/routing.yml`.
4. User `~/.config/mission/routing.yml`.
5. Default `inline`.

The resolved request and source are stored in session state so the later
`set complexity=Simple` and `next` routing paths use the same selection. A routed
halt additionally records `goal_dispatch_effective` and, when applicable,
`goal_dispatch_fallback_reason`.

## Host detection and fail-safe behavior

Host detection uses native identity environment variables already used by mission
session routing: `CLAUDE_CODE_SESSION_ID` or `CLAUDECODE` for Claude Code, and
`CODEX_THREAD_ID` for Codex. Missing identity yields `unknown`; generic CI or
sandbox variables are not treated as host identity.

Cross-host process launching is intentionally not a default provider. Starting a
different host's CLI would introduce separate authentication, cost, process, and
portability boundaries. `host-native` emits guidance for the host already running;
it does not launch another agent runtime.

`--force-mission`, `--issue-ref`, checker roles, user-pinned review tiers, risk
signals, and scored sessions continue to keep the mission loop exactly as before.
