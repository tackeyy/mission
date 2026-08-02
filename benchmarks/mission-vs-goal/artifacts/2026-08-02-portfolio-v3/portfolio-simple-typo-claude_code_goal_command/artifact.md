# Goal

Read exactly the fixture `benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md`, identify the misspelled setting-name reference in the usage note (which does not match the table), quote it exactly, and state the correct setting name from the table.

# Result

- Misspelled reference found in the usage note: `retry_polcy`
- Correct setting name (from the table): `retry_policy`

# Evidence

Quoted directly from `benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md`:

Table (lines 5–9):
```
| Setting | Value |
|---|---|
| retry_policy | exponential-backoff |
| max_attempts | 4 |
| base_delay_ms | 200 |
```

Usage note (lines 11–12):
```
Usage note: services must reference the `retry_polcy` setting name exactly as
defined in the table above when importing this configuration block.
```

Confirmed finding: the usage note references `retry_polcy`, which does not appear anywhere in the table. The table's first row lists `retry_policy` as the setting name. `retry_polcy` is `retry_policy` with the `i` and `c` transposed/missing (`retry_pol` + `cy` vs `retry_poli` + `cy`) — a misspelling, not a distinct setting.

No rejected candidates apply here: the task involves a single misspelled reference, and the other two table entries (`max_attempts`, `base_delay_ms`) are not referenced anywhere in the usage note, so there was nothing else to evaluate or reject.

# Assumptions

- The fixture file content read at the time of this task is authoritative and unmodified between read and write.
- "Setting name" in the task prompt refers to the `Setting` column values in the table, not the `Value` column.
- No other files under `benchmarks/mission-vs-goal/` were read or referenced, per the task's scope restriction.

# Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-simple-typo-claude_code_goal_command.md` with all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition) populated, the misspelled reference `retry_polcy` and the correct setting name `retry_policy` are both quoted above, and no commit/push/install/network actions were taken. The task is complete.
