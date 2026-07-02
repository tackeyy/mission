# Open Issues 79-98 Progress

Date: 2026-07-02
Branch: fix/open-issues-79-98

## Scope

- #79 specialist tie fallback auto-select policy and ADR-001 amendment
- #80 ADR-002 typed mission state objects
- #89 tracking checklist update after merge
- #90 frozen pass/score/threshold fields
- #91 push-score item-derived mean/min warnings
- #92 corrupt session JSON init fallback
- #93 mission-executor allowed-tools
- #94 stop hook lsof timeout, Linux /proc cwd, HOOK_SID direct file
- #95 Phase 7 auto-merge opt-in and injection/CI-zero guard docs
- #96 reviewer/scorer discipline and quality gate docs
- #97 stale/refresh/hook robustness remainder
- #98 documentation drift and parallel push-score regression

## TDD Test List

- [x] #92 corrupt session JSON init exits 0, warns, and quarantines the corrupt file.
- [x] #90 set rejects passes, passes_forced, force_reason, score_history, and threshold while preserving loop_active=true reactivation.
- [x] #91 push-score warns when supplied composite/min_item diverge from the supplied items by more than 0.1.
- [x] #91 push-score accepts partial 4-item scoring without warning when scalar values match those items.
- [x] #94 stop hook does not wait on a slow lsof and falls back to hook input cwd.
- [x] #94/#97 stop hook uses HOOK_SID direct session lookup when available.
- [x] #97 awaiting_user=true prevents stale auto-halt.
- [x] #97 refresh-pid reactivates stale: halts as well as orphan: halts.
- [x] #98 concurrent push-score calls preserve all score_history entries.
- [x] #79 tied installed optional low/medium-risk specialists auto-select deterministically with an audit reason.
- [x] #79 high-risk tied candidates still ask the user.

## Evidence Log

- 2026-07-02: Issue bodies #79, #80, #89-#98 fetched with `gh issue view`.
- 2026-07-02: `main` synced with `git fetch -p origin` and `git pull --ff-only origin main`; already up to date at `4f26574`.
- 2026-07-02: Targeted Red tests failed for #90/#91/#92/#93/#94/#97/#79 as expected before implementation.
- 2026-07-02: Targeted tests and plugin drift tests passed: 22 passed.
- 2026-07-02: Full test suite passed: 419 passed, 1 pytest config warning (`cache_dir` unknown under `-p no:cacheprovider`).
