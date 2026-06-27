# Changelog

[Japanese](CHANGELOG.ja.md) | **English**

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.5] - 2026-06-26

### Added
- Mission audit now reports unresolved `ask-user` specialist confirmations separately from unselected invocation findings.
- Mission audit now reports slow sessions whose elapsed time is coarsely attributed to planning despite phase-duration data.
- Mission audit self-improvement prompts now require duplicate issue checks and development/tech-lead review evidence before agents create GitHub issues.
- `mission-state.py push-score` now writes generated scoring evidence when `--scoring-output` is omitted, so every score history entry has an auditable archive artifact.
- `mission-state.py specialists log-invocation --selection-source` now records explicit/manual specialist selection metadata while logging inline or tool invocation evidence.
- `mission-state.py specialists summary` now emits final-report specialist usage grouped as selected, used, degraded, and unselected-manual with provider `kind` and registry/source metadata.
- Documented the versioning policy that separates ordinary merge releases from intentional distribution releases, so plugin versions are not bumped for every merged PR.
- Added OSS portability guardrails in `AGENTS.md`, `CLAUDE.md`, and ADR-001 so personal/private specialist skills stay in user or project registries instead of public defaults.
- Added `mission-state.py specialists accounting --json` as a pre-completion warning that reports available specialist/provider candidates without a terminal decision trail.
- Shared candidate-accounting logic between `mission-state.py` and `scripts/mission-audit.py` so high-risk candidate findings use the same rules in live checks and retrospective audits.
- Added a stable repository-root `scripts/mission-state.py` wrapper for the canonical state CLI.
- Added `mission-state.py progress update/get/clear` checkpoints for long-running batches, with archived progress evidence and audit output on slow-session lines.
- Specialist recommendations now include a bounded `specialists_phase_plan` for development and strategy-style registries without embedding maintainer-local skill names.
- Mission audit now reports invalid score iterations and blank specialist invocation records as explicit findings.
- Mission audit now accepts `--current-since` to keep historical audit debt visible while judging current regressions separately.
- Release guardrails now require distribution releases to create and push the matching git tag, create or update the GitHub Release, and re-verify both before completion is reported.

### Changed
- Mission orchestrator guidance now requires explicit `phase=executing` / `phase=reviewing` transitions and progress checkpoints for long-running work.
- Refined Complex specialist accounting to require explicit terminal decisions only for risk-bearing candidates, preserving hackable user plugins as optional evidence sources by default.
- Database/backend candidates now require strong database signals such as schema, migration, query, SQL, or persistence before they are treated as high-risk accounting candidates.
- Command providers can now classify preparation-only or too-short output as `prepared` using `result_contract`, preventing banners from being treated as completed review evidence.
- `oracle-reviewer` now has a conservative default result contract for browser-review preparation banners, and `ask-user` specialist confirmations must be persisted with `--selection-source confirmed-user` before applied evidence counts as selected.
- Broad orchestrator specialists are now bounded to non-execution evidence use and require `--bounded-purpose` when their applied plan/review evidence is recorded.
- Standard or Complex audit/self-improvement missions now require explicit accounting for available testing, security, or risk specialist candidates.

### Fixed
- Mission audit now flags command-provider invocations that were marked `completed` even though their archived evidence is only an Oracle/browser review preparation packet.
- Mission audit no longer reports active `ask-user` specialist waits as candidate-only specialist debt before the user decision can be recorded.
- Mission audit no longer treats core mission subskills as unselected external specialist invocations.
- Distribution-sync tests now guard the marketplace `mission-state.py` wrapper against dropping specialist-accounting/result-contract markers.
- Mission audit pass-rate calculations now exclude active no-score checkpoints from the denominator while still reporting them as incomplete active sessions.
- Mission audit now classifies nested `archive/worktree-*/sessions/*.json` copies as resolved archive duplicates, preventing cross-root audits from reporting exact live/archive copies as P1 `duplicate-state` findings.
- `mission-state.py mark-passes` now blocks required specialist providers that lack applied result evidence, so `prepared`, `skipped`, or `failed` evidence cannot satisfy strict required-provider gates.
- `mission-state.py push-score` now rejects iteration values below 1, preventing unauditable `score_history` entries.
- `mission-state.py specialists log-invocation` now rejects blank `role` or `skill` fields before writing specialist evidence.
- `mission-state.py stats` now includes nested `archive/worktree-*/sessions/*.json` files so its session counts match audit discovery.

## [1.0.4] - 2026-06-22

### Added
- README now positions `mission` as a loop-engineering quality gate and links to launch-positioning guidance.
- `mission-state.py stats` now accepts repeated `--root` arguments, aggregates all scanned roots, reports the scanned root list, and de-duplicates overlapping state identities.
- Specialist invocation logging now accepts `skill-tool-applied`, requires an explicit reason for skipped/unavailable/failed decisions, and documents high-risk candidate accounting.
- Mission audit now reports `candidate-only-specialists` when specialist candidates exist but no selection, invocation, or skip decision trail is recorded.
- Mission audit now reports specialist invocations that have terminal evidence but no matching Phase 1 selection metadata.
- Mission final-report guidance now includes a concise specialist summary for selected, used, degraded, and unselected-manual skills while preserving truthful `codex-inline` wording.
- Specialist registries are now auto-discovered from project, user, and skill/plugin manifest locations, with project-level disable overrides.
- Specialist provider schema now supports `kind: skill` and `kind: command`, first-use risk consent, and command-provider evidence invocation without hard-coding providers such as `oracle` into mission core.

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

[1.0.5]: https://github.com/tackeyy/mission/releases/tag/v1.0.5
[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
