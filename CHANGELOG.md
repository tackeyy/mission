# Changelog

[Japanese](CHANGELOG.ja.md) | **English**

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-06-16

### Fixed
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

[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
