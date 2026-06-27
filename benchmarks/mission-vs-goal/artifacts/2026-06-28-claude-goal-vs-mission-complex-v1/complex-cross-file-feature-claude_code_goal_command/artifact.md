# Goal

Produce exactly one task artifact for task `complex-cross-file-feature` (category:
coding; arm: `claude_code_goal_command`) in this single controlled local benchmark
run, using Claude Code's built-in `/goal` command as the completion controller.

The underlying task prompt is to "implement a small feature that touches CLI
parsing, core behavior, docs, and tests, then leave a migration-free release note."
The task validator requires that this artifact (1) map every changed surface to an
explicit acceptance check, (2) include test evidence, and (3) identify any deferred
non-goals. This file must carry the headings Goal, Result, Evidence, Assumptions,
and Stop Condition.

# Result

No production source code was changed in this run. The run rules constrain edits to
"benchmark output files" only (for this arm; `.mission-state/` is allowed for the
mission arm, not this one) and instruct me to "write exactly one task artifact" and
"only complete this task artifact." These constraints override the literal task
prompt, so the CLI-parsing / core-behavior / docs / tests feature and its
migration-free release note were **intentionally not implemented**. They are
recorded below as deferred non-goals with the acceptance check that *would* verify
each one.

The only surface changed in this run is this artifact file (and its parent
directory, newly created). Every other surface the task names is mapped to a
concrete, repo-grounded acceptance check whose status is honestly reported as
deferred/unmeasured rather than claimed as passing.

## Surface → Acceptance Check Map

| # | Changed surface (task prompt) | Explicit acceptance check (concrete) | Status in this run |
|---|---|---|---|
| 1 | This artifact file at the mandated path | File exists at `benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-complex-v1/complex-cross-file-feature-claude_code_goal_command.md` and contains headings Goal, Result, Evidence, Assumptions, Stop Condition | **Done** — only changed surface; verified by writing this file (see Evidence) |
| 2 | CLI parsing (e.g. an `argparse` flag in `skills/mission/bin/mission-state.py`) | New flag is parsed; `mission-state.py --help` lists it; a pytest case asserts the parsed value and that an invalid value is rejected (non-zero exit) | **Deferred (not implemented)** — check defined, not run |
| 3 | Core behavior (handler invoked by the new flag) | Unit test asserts the new code path runs when the flag is set and that the prior default path is unchanged when the flag is absent | **Deferred (not implemented)** — check defined, not run |
| 4 | Docs (`README.md` / `README.ja.md` and skill `refs/`) | User-facing docs document the flag/behavior; a grep check confirms the documented flag name matches the CLI definition string in `mission-state.py` | **Deferred (not implemented)** — check defined, not run |
| 5 | Tests (`skills/mission/**` test suite) | New tests cover the flag and behavior, and the full suite stays green via the repo's CI command `python -m pytest -q skills/mission` | **Deferred (not implemented)** — **unmeasured**; suite not run in this scope |
| 6 | Migration-free release note (`CHANGELOG.md` `[Unreleased]`, Keep a Changelog) | An `[Unreleased]` entry describes the change and explicitly states no migration/state-format change is required; reviewer confirms no `mission-migrate.py` step is implied | **Deferred (not implemented)** — check defined, not run |

# Evidence

- **Artifact path / headings (surface #1).** This file was written to the mandated
  path and contains all five required headings (Goal, Result, Evidence,
  Assumptions, Stop Condition). The target directory
  `benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-complex-v1/`
  did not previously exist (`ls` returned "No such file or directory", exit 1) and
  was created by `mkdir -p` to hold only this artifact.
- **Workspace state.** `git status --short --branch` reported `## HEAD (no branch)`
  (detached HEAD) with no other tracked or untracked changes at the start and a
  clean tree apart from this new artifact.
- **Repo-grounded acceptance checks.** The checks above reference verified repo
  facts, not guesses: the CLI entrypoint is the Python script
  `skills/mission/bin/mission-state.py` (confirmed via `ls plugins/mission/skills/mission/bin`);
  the test command is `python -m pytest -q skills/mission` (read from
  `.github/workflows/ci.yml`); the changelog uses Keep a Changelog with an
  `[Unreleased]` section (read from `CHANGELOG.md`). There is no `package.json`
  (Python project), so no npm/node test path applies.
- **Test evidence: unmeasured.** No test suite was executed because no source files
  were changed. Surfaces #2–#6 therefore have **zero executed evidence**; their
  pass/fail is explicitly unverified, not claimed.
- **No prohibited actions.** No commit, push, package install, or network access
  was performed in this run.
- **Precedent.** A prior run of this same task/arm
  (`benchmarks/mission-vs-goal/artifacts/2026-06-28-claude-goal-vs-mission-smoke-v2/complex-cross-file-feature-claude_code_goal_command/artifact.md`)
  applied the same convention: under the edit-scope restriction the source change is
  not performed and the artifact states so explicitly.

# Assumptions

- The benchmark is evaluating artifact completion and edit discipline for the
  `claude_code_goal_command` arm, not requiring a real cross-file code change in
  this scope-restricted run. This is inferred from the run rules ("Keep edits
  narrowly scoped to benchmark output files", "Write exactly one task artifact",
  "Only complete this task artifact") overriding the literal task prompt. If a real
  implementation were intended, surfaces #2–#6 would move from "deferred" to
  executed-with-evidence; that is the single assumption that, if wrong, changes the
  result.
- "Migration-free release note" means a CHANGELOG/release entry stating no migration
  (here: no mission-state format change requiring `mission-migrate.py`) is needed.
  Since no feature was built, only the acceptance check for such a note is recorded,
  not the note itself.
- The specific flag/behavior is left unspecified by the task prompt; the acceptance
  checks are written to be valid for any single small flag-driven feature on the
  named surfaces rather than inventing a concrete flag that was never built.
- Per the rules, no benchmark-superiority claim is made; this artifact reports only
  what was and was not done in this single run.

# Stop Condition

Stop when this artifact exists at the mandated path with all five required headings
and (1) maps every surface named by the task to an explicit acceptance check
(surfaces #1–#6 above), (2) reports test evidence honestly — here **unmeasured**,
because no source was changed and the suite `python -m pytest -q skills/mission` was
not run — and (3) identifies the deferred non-goals (the CLI/core/docs/tests feature
implementation and the migration-free release note, surfaces #2–#6). All three
conditions hold as written, so the task is complete and no further source edits are
in scope for this run.
