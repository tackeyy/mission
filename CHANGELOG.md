# Changelog

[Japanese](CHANGELOG.ja.md) | **English**

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.3] - 2026-06-20

### Added
- Mission audit now reports sessions started after the Phase 1 specialist-selection checkpoint rollout when selection metadata is missing.
- Release checklist now requires reconciling `git log <previous-tag>..HEAD --oneline` with English and Japanese changelog entries before release completion.
- Documentation consistency tests now guard the v1.0.2 release themes so future changelog edits cannot silently drop them.

### Fixed
- Corrected the v1.0.2 changelog entry to include the Phase 1 specialist selection checkpoint, specialist registry, file-overlap warnings, audit CLI, GitHub Flow guidance, contributor acknowledgements, reviewer/scorer safeguards, audit diagnostics, and Codex hook-packaging validation.

## [1.0.2] - 2026-06-20

### Added
- Optional specialist registry support now lets mission classify task profiles, select available domain specialist skills, use them as evidence providers, and record invocation evidence.
- Phase 1 now requires an executable specialist selection checkpoint after mission initialization by recording `specialists recommend --record-state --json` results in mission state.
- `mission-state.py init` now accepts `--files` and warns when another active session targets overlapping files.
- A read-only `scripts/mission-audit.py` CLI now audits local mission state, produces self-improvement prompts, and reports forced/ungated passes, duplicate state, halt, slow-session, and low-score-pass buckets.
- Mission audit now discovers nested worktree archive sessions, surfaces missing scoring evidence, and reports specialist invocation gaps.
- Slow-session reports now include a separate phase-duration observability breakdown.
- GitHub Flow guidance now documents issue-linked missions, `Closes #N` pull request bodies, and merge-driven issue closure.
- README now recognizes contributors and contribution types.

### Fixed
- Reviewer and scorer safeguards now use merge-base diff context and test-authenticity checks to reduce false regression reports and shallow test validation.
- Audit deduplication now prefers completed pass/done records over stale halt copies for the same logical mission run.
- Audit diagnostics now classify halt/incomplete root causes, slow-session buckets, and low-score pass risk buckets.
- Release validation now guards the Codex plugin hook-packaging contract.

## [1.0.1] - 2026-06-17

### Added
- **Q11 – stagnation auto-count**: `push-score` now increments `stagnation_count` automatically when the composite improvement (`cur − prev`) is in the range `[0, 0.1)`. Regression (score drop) and first-push are treated as non-stagnation and reset the counter to 0.
- **S3 – duplicate issue-ref warning**: `init` gains an `--issue-ref <ref>` option. When another active session for the same project already carries the same `issue_ref`, a `WARNING [S3]` is printed to stderr (non-blocking). Self-detection on resume (same `session_id`) is excluded.

### Fixed
- **Q11 regression fix**: negative delta (score regression) was incorrectly treated as stagnation. Condition tightened to `0 <= delta < 0.1` using `_is_valid_composite()` for type safety.
- Synced the copied Codex marketplace wrapper (`plugins/mission/`) with the canonical `skills/` and `scripts/` trees so it includes the latest stale auto-halt, High-gate, stats, and scoring-rubric fixes.
- Added a regression test that fails when the Codex wrapper drifts from the canonical implementation.

## [1.0.0] - 2026-06-15

First public release.

### Added
- Mission orchestrator skill and five supporting skills (planner, executor, reviewer, critic, scorer).
- `.mission-state` session-state CLI (`mission-state.py`) with multi-session isolation for Claude Code and Codex.
- Threshold-gated completion with score history and review/critic loops.
- Stop hook that blocks premature completion while a mission is active. Stale-state timestamp parsing is portable across macOS (BSD `date`) and Linux (GNU `date`).
- Claude Code plugin metadata and a local plugin marketplace manifest.
- Codex plugin packaging (`plugins/mission/`) and skill symlink guidance, with an opt-in Stop hook.
- Python test suite covering state routing, scoring gates, and hook behavior.
- GitHub Actions CI (`push`, `pull_request`, `workflow_dispatch`) with pytest and ShellCheck.

[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
