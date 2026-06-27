# Goal

Produce the single task artifact for `complex-cross-file-feature` (arm:
`claude_code_goal_command`) in this controlled local benchmark run, using Claude
Code's built-in `/goal` command as the completion controller. The underlying task
prompt is to implement a small feature touching CLI parsing, core behavior, docs,
and tests, then leave a migration-free release note. The validator requires that
this artifact map every changed surface to an explicit acceptance check, include
test evidence, and identify any deferred non-goals.

# Result

No production source code was changed in this run. The run rules restrict edits to
benchmark output files (and, for the mission arm only, `.mission-state/`), and
state "Write exactly one task artifact" and "Only complete this task artifact."
The CLI/core/docs/tests feature implementation and the migration-free release note
were therefore intentionally **not** performed; they are recorded below as deferred
non-goals. The only changed surface in this run is this artifact file (plus the
`run-output/2026-06-28-claude-goal-vs-mission-smoke-v3/` directory created to hold
it).

Because the feature was not implemented, the surface map below records, for each
notional surface the task names, (a) the explicit acceptance check that *would*
verify it and (b) the current, honest status. No acceptance checks were executed
against new code, since no new code exists.

## Surface → Acceptance Check Map

| # | Surface (from task prompt) | Explicit acceptance check | Status in this run |
|---|---|---|---|
| 1 | This artifact file | File exists at the mandated path and contains the headings Goal, Result, Evidence, Assumptions, Stop Condition | **Done** — verified by writing this file (only changed code/doc surface) |
| 2 | CLI parsing | New/changed flag is parsed; `--help` lists it; an automated test asserts the parsed value and rejects an invalid value | **Deferred (not implemented)** — acceptance check defined, not run |
| 3 | Core behavior | Unit test asserts the new behavior is exercised when the flag is set and the prior default path is unchanged when it is absent | **Deferred (not implemented)** — acceptance check defined, not run |
| 4 | Docs | A user-facing doc documents the flag/behavior; a doc/lint or grep check confirms the flag name in docs matches the CLI definition | **Deferred (not implemented)** — acceptance check defined, not run |
| 5 | Tests | New tests cover the added flag and behavior and the existing suite still passes (repository test command green) | **Deferred (not implemented)** — unmeasured; suite not run |
| 6 | Migration-free release note | A CHANGELOG/release entry describes the change and explicitly states no migration is required; reviewer check confirms no migration steps are implied | **Deferred (not implemented)** — acceptance check defined, not run |

# Evidence

- **Workspace state:** `git status --short --branch` reported `## HEAD (no branch)`
  (detached HEAD) with no tracked or untracked changes at the start of the run.
- **Target directory:** `benchmarks/mission-vs-goal/run-output/` did not previously
  exist (`ls` returned exit 1 / "No such file or directory"); the
  `2026-06-28-claude-goal-vs-mission-smoke-v3/` subdirectory was created to hold
  this artifact.
- **Headings present:** This file contains all five required headings — Goal,
  Result, Evidence, Assumptions, Stop Condition (surface map row #1 acceptance
  check).
- **No prohibited actions:** No commit, push, package install, or network access
  was performed during this run.
- **Test evidence: unmeasured.** No test suite was run because no source files were
  changed. Surfaces #2–#6 have defined acceptance checks but zero executed
  evidence; their pass/fail status is therefore unverified, not claimed.
- **Convention precedent:** The prior v2 run's `claude_code_goal_command` artifact
  (`benchmarks/mission-vs-goal/artifacts/2026-06-28-claude-goal-vs-mission-smoke-v2/complex-cross-file-feature-claude_code_goal_command/artifact.md`)
  uses the same convention: under the edit-scope restriction, the cross-file source
  change is not performed and the artifact states this explicitly. This v3 artifact
  was written independently and its workspace facts were re-verified, not copied.

# Assumptions

- The benchmark harness is evaluating artifact completion and reporting discipline
  for the `claude_code_goal_command` arm, not requiring a real cross-file code
  change in this scope-restricted run. This is inferred from the run rules ("Keep
  edits narrowly scoped to benchmark output files", "Write exactly one task
  artifact", "Only complete this task artifact") overriding the literal task
  prompt. Where the rules and the task prompt conflict, the rules govern.
- "Migration-free release note" refers to a CHANGELOG/release entry stating that no
  migration is needed; since no feature was implemented, only the acceptance check
  for such a note is recorded, not the note itself.
- The exact repository test command is not asserted here because no suite was run;
  it is referenced generically in surface #5 rather than claimed as executed.
- Per the rules, no benchmark-superiority claim is made; this artifact reports only
  what was and was not done in this single run.

# Stop Condition

Stop when this artifact exists at the mandated path
(`benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-smoke-v3/complex-cross-file-feature-claude_code_goal_command.md`)
with all five required headings (Goal, Result, Evidence, Assumptions, Stop
Condition) and the artifact (1) maps every named surface to an explicit acceptance
check, (2) reports test evidence honestly — here, unmeasured because no source was
changed — and (3) identifies the deferred non-goals (the CLI/core/docs/tests
feature implementation and the migration-free release note, surfaces #2–#6). All
three conditions hold as written above, so the task is complete; no further source
edits are in scope for this run.
