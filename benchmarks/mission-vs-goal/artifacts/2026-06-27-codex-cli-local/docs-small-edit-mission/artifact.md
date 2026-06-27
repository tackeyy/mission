# Mission

docs-small-edit: clarify one installation prerequisite in a README section without changing behavior.

Scope is constrained by the benchmark rule to this single output artifact plus `.mission-state/`. The README itself was inspected but not edited.

# Plan

1. Inspect the README installation and requirements sections.
2. Identify one prerequisite that can be clarified without changing commands or runtime behavior.
3. Record the intended documentation-only clarification in this artifact.
4. Review the artifact against the validator and mission constraints.
5. Record a passing mission score and stop decision through the mission state CLI.

# Execution

Inspected `README.md` and found the relevant installation context:

- `## Installation` includes Claude Code and Codex install paths.
- `## Requirements` lists `jq` for the Stop hook.
- The current text names `jq`, but does not explicitly say that it must already be installed and available on `PATH` before enabling the Stop hook.

Requested README clarification:

```diff
 ## Requirements

 - macOS or Linux
 - Python 3.9 or later
-- `jq` for the Stop hook
+- `jq` installed and available on `PATH` before enabling the Stop hook
 - Claude Code or Codex for skill execution
```

This is documentation-only. It clarifies an installation prerequisite and does not alter behavior, commands, defaults, or supported platforms.

# Review

Self-review against the validator:

- Requested clarification is present: yes, the proposed diff clarifies the `jq` prerequisite.
- Behavior unchanged: yes, only wording changes are proposed.
- No unrelated files: yes, the only non-state artifact intended by this benchmark run is this file.
- Scope respected: yes, README content was inspected but not modified because benchmark rules restrict edits to benchmark output files.

# Score

Composite score: 4.575 / 5.0

Item scores:

- Mission achievement: 4.5
- Accuracy: 4.7
- Completeness: 4.5
- Usability: 4.6

Reviewer consensus is omitted because this Simple mission uses a single self-review pass.

# Stop Decision

Pass. The artifact satisfies the benchmark requirement, includes the requested headings, documents a narrow README prerequisite clarification, and avoids unrelated edits.

# Evidence

- Mission state initialized with session id `bench-2026-06-27-codex-cli-local-docs-small-edit-mission`.
- README inspection found `jq for the Stop hook` under `## Requirements`.
- Proposed diff changes only that bullet to clarify installation and `PATH` availability.
- No package installation, commit, push, or network access was used.

# Assumptions

- The benchmark instruction to write exactly one task artifact takes precedence over directly editing `README.md`.
- The task validator can evaluate the requested clarification from the artifact diff recorded above.
- `.mission-state/` updates are allowed for the mission arm and are not counted as the single task artifact.
