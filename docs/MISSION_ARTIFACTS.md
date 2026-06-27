# Mission Artifacts Design

Status: design contract and implementation plan. This document does not claim
that artifact support is already implemented. Artifact support is not yet
implemented by this design document.

`mission` should produce an inspectable mission artifact: a durable local file
that explains what was requested, what was done, what evidence was checked, what
review found, how the score gate was reached, and why the mission can stop.

This is deliberately local-first. Claude Code Artifacts are a useful product
reference, but `mission` is an OSS plugin that must remain portable across
Claude Code, Codex, shell usage, and offline repositories.

## Official Reference

The best-practice reference is Claude Code's official documentation:

- [Claude Code Artifacts](https://code.claude.com/docs/en/artifacts)
- [Claude Code `/goal`](https://code.claude.com/docs/en/goal)

The design takes these practices from Claude Code Artifacts:

- Artifact creation is explicit. A shareable page is not silently published.
- The artifact is a concrete HTML or Markdown file before it becomes a hosted
  page.
- Publishing creates a private URL with sharing boundaries controlled by the
  platform.
- The artifact is meant to be inspectable by a human, not just consumed as
  hidden agent state.

`mission` should adopt the same user-facing discipline without making Claude
Code hosting a required dependency.

## Goals

- Create one canonical artifact per mission by default.
- Keep artifact data tied to `.mission-state` so the stop decision and the
  evidence trail cannot drift apart.
- Support Markdown first, then optionally rendered HTML.
- Keep remote publishing as an explicit opt-in adapter.
- Make artifact requirements testable, so marketing claims can point to files
  and raw evidence instead of chat-only summaries.

## Non-Goals

- Do not make Claude Code Artifacts a hard runtime dependency.
- Do not publish anything remotely without explicit user approval.
- Do not use artifacts as a replacement for `.mission-state`; they are the
  human-readable surface of the state and evidence.
- Do not claim benchmark superiority from artifact existence alone.

## Artifact Contract

The first implementation should add a local artifact under:

```text
.mission-state/artifacts/<session_id>/mission-artifact.md
```

The state file should record:

```json
{
  "artifact": {
    "status": "draft",
    "format": "markdown",
    "path": ".mission-state/artifacts/<session_id>/mission-artifact.md",
    "exports": [],
    "publish_events": [],
    "redaction_status": "unchecked"
  }
}
```

The artifact should include these sections:

| Section | Purpose |
|---|---|
| Mission | User request, scope, constraints, and session id |
| Plan | Current plan and meaningful plan changes |
| Execution | Files changed, commands run, and external systems touched |
| Evidence | Test results, benchmark records, source links, and raw artifact paths |
| Review | Reviewer findings and whether they were fixed or accepted |
| Score Gate | Score items, threshold, pass/fail, and stop rationale |
| Assumptions | Claims that are inferred, unverified, blocked, or time-sensitive |
| Follow-ups | Work intentionally left outside the completed mission |

## CLI Plan

Add artifact subcommands to `skills/mission/bin/mission-state.py`:

```text
mission-state.py artifact init --format markdown --title "..."
mission-state.py artifact append --section evidence --file path/or/stdin
mission-state.py artifact render
mission-state.py artifact export --to docs/marketing/<slug>.md
mission-state.py artifact publish --provider claude-code --require-confirm
```

Rules:

- `artifact init` creates the local file and records the artifact block in the
  active session state.
- `artifact append` only accepts known section names.
- `artifact render` regenerates the canonical Markdown from state plus appended
  evidence blocks.
- `artifact export` copies a reviewed version to a user-selected durable path.
- `artifact publish` is optional and must require explicit user confirmation.

## Stop-Gate Integration

Artifact support should start as opt-in, then become required for mission types
where a durable result is expected.

Recommended phases:

| Phase | Change | Verification |
|---|---|---|
| 0 | Document the contract and plan | Doc consistency tests |
| 1 | Add local artifact state schema and CLI commands | Unit tests for init/append/render/export |
| 2 | Update the orchestrator skill to create and update artifacts during normal runs | Integration tests with a temporary mission state |
| 3 | Add stop-gate checks for artifact-required missions | `mark-passes` refuses missing artifacts unless forced |
| 4 | Add optional publisher adapters | Tests prove publishing is opt-in and records consent |
| 5 | Re-run `/goal` vs `mission` benchmark with artifact-required mission runs | Raw paired records plus artifact paths |

## Security And Privacy

Artifacts are more shareable than state files, so they need stricter handling:

- Local artifact is private by default.
- Publishing must require an explicit user approval step.
- Redaction status must be recorded before export or publish.
- Secrets, tokens, private customer names, and local home-directory paths should
  be redacted unless the user explicitly asks to include them.
- Remote adapters must record provider, timestamp, destination, and approval
  evidence in `publish_events`.

## Benchmark Implication

For marketing-safe comparisons, artifact support changes the benchmark question
from "which agent says done?" to "which workflow leaves a reusable, auditable
result behind?"

Until Phase 5 paired runs are completed, the only defensible public claim is:

> `mission` has a planned local-first artifact contract designed to make
> completion evidence auditable. The current `/goal` comparison results do not
> yet measure that artifact behavior, and do not yet measure that artifact
> behavior as a benchmark outcome.
