# Changelog

[Japanese](CHANGELOG.ja.md) | **English**

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `mission-state.py advance --phase <phase> --activity <kind>:<reason>` performs the phase transition and the activity switch in one lock and one atomic write, so a state where the phase advanced but the activity stayed empty can no longer be produced (a structural cause of the 9.96% activity coverage measured in the 2026-07-22 execution-speed audit). Validation (phase normalization, kind/reason enum) happens before the lock and rejects without writing; transitions to `done`/`halted` stay exclusive to `mark-passes`/`mark-halt` so `advance` cannot bypass the pass gate. Calling `advance` with the current phase switches only the activity segment (#237).

### Security

- `codex-preflight --strict` now rejects the deprecated `MISSION_REQUIRE_SCORING_EVIDENCE=0` escape hatch (exit 2) and reports the run as not ok. This environment variable bypasses the scoring-evidence gate via the legacy `push-score --items` path, so an active hatch must never proceed to real work. The hatch itself still functions for the moment but is now labelled `DEPRECATED ESCAPE HATCH` and is scheduled for removal in the next minor release (#226).

### Added

- Local authoring now performs a fail-closed source bootstrap before mission state initialization. It fetches `origin/main`, fast-forwards only a clean `main` checkout, verifies the local and remote-tracking commits match, and requires the updated `SKILL.md` to be reread. Dirty, non-main, detached, ahead/diverged, missing-remote, and network-failure cases stop without stale fallback or history rewriting (#229).
- `mission-state.py stats` and `mission-audit.py` now share an exclusive pass-rate health classification and expose finite `raw_pass_rate` and `completed_pass_rate` values with explicit numerators and denominators. Fresh active work remains visible but outside the completed population; stale active work remains actionable and enters that population as non-passing health debt. Separate active, active-no-score, stale, halt, and abandoned counts are always present in JSON and console output. The deprecated `pass_rate` alias preserves each command's historical meaning, and stats now reads the current immutable worktree archive generation used by audit (#208).
- `mission-audit.py --current-since` now maps detected record/item evidence through one registry-driven shared finding model and partitions force-pass, halt/slow/scoring, and specialist-provenance risks with the same inclusive UTC cutoff. One parser handles date/ISO bounds for `--since`, `--until`, and `--current-since`. JSON exposes canonical all/current/historical evidence lists, severity/code conservation counts, and compact count/index views by code; Markdown lists current P0/P1/P2 before historical risks. Historical evidence retains its original severity and provenance but does not enter the current improvement prompt. Missing/invalid timestamps stay current, and omitting the cutoff preserves the prior all-period behavior. Pass severity, required-specialist result gates, and force-approval gates are unchanged (#207).
- Mission state now records bounded, explicit activity segments for active work, external waits, approval waits, reviewer waits, and idle time. `mission-state.py stats` and `mission-audit.py` share one reducer for task/phase R7 p50/p90, kind/reason totals, coverage, unclassified time, and anomaly counts. Crash/resume gaps remain unclassified, legacy phase durations are preserved, and review/pass gates are unchanged (#211).
- `mission-state.py archive-worktree` now copies a terminal worktree session and its state-referenced evidence to an existing separate checkout in the same Git common directory. Updates publish content-addressed immutable generations before atomically advancing `current.json`, so a crash or parallel reader never loses the previous valid generation. A `mission-worktree-archive/1` manifest records session/mission/iteration identity, evidence type, private relative source/archive references, SHA-256, and size; duplicate paths, escapes, symlinks, missing evidence, and integrity failures fail closed. `mission-audit.py` snapshots and preflights the discovered generation before loading its state, resolves scoring and specialist evidence from the validated manifest, and caches validation once per record. It verifies `.mission-state` readiness before descent and records later walk access errors. Non-directory, unreadable, or symlinked `.mission-state` and archive roots, symlinked bundle or generation ancestors, bundles resolving outside the regular archive root, archive/pointer/generation access failures, malformed or unsafe pointers, missing or invalid archived state, and missing or invalid generation manifests produce one deduplicated `invalid-worktree-archive` finding rather than reading outside the root, silently omitting the archive, or falling back to stale files; legacy compatibility applies only when pointer absence is confirmed by `lstat` (#212).

### Fixed

- `mission-audit.py` now recognizes real-world delegated handoff and explicit merge-approval halt reasons when deriving actionable P1 `halted-runs`. Raw halt counts remain unchanged, stale/orphan states still fail closed as actionable, and the classifier no longer lets Japanese root-handoff or approval-wait wording depress actionable pass rate (#233).
- `mission-audit.py` now preserves raw halt counts while deriving P1 `halted-runs` and a separate `actionable_pass_rate` only from terminal states that still require root-cause action. Structured approval waits, delegated partial completion, user aborts, explicit resolved/superseded evidence, and narrowly recognized external waits remain visible in disposition breakdowns without depressing actionable quality; stale, stagnated, conflicting-gate, unknown, and ambiguous halts fail closed as actionable. The deprecated `pass_rate` alias remains the completed-session rate (#221).
- Non-interactive mission startup now grants only the packaged and repository-local state CLI commands needed by the orchestrator. `mission-state.py init` performs content-preserving, fsynced write probes for the session state directory and assumptions evidence before task execution. A failed probe exits 2 with a structured `blocked-external` halt; if state persistence is also unavailable, the same structured evidence is emitted on stdout without asking for approval. `permission-preflight --json` exposes the same check for explicit diagnostics (#220).
- Irreversible `review_tier` keywords are now evaluated per occurrence with operation-anchored clause and structural-unit context. Negation requires a direct grammatical anchor to that operation rather than any cue within a character window, including contractions, `cannot`, active `not perform/execute`, passive `will/should not be performed/executed`, and equivalent Japanese qualifier forms. Explicitly negated actual operations no longer escalate a Simple/Standard mission, while conditional exceptions, negated non-operation intent, and uncertainty remain conservative; multiple negation cues form a reversal only before the next operation. A global non-operation marker suppresses only candidates whose own context proves meta/non-operation intent, and any execution cue in the same logical unit that is not directly anchored to another named operation vetoes suppression as an ambiguous reference. Quote-only intent is overridden only when execution language immediately around that quote targets its command, including a passive modal immediately after it, not by wording inside the quote or execution of another named operation. Cached segment, operation-start, quote, meta/non-operation, negated-operation, negation-cue, and global-marker indexes avoid repeated full-text or dense-context scans. State adds per-match `review_tier_signal_details` provenance without changing the existing ordered string-list contract; security, high-risk, and Complex/Critical behavior is unchanged (#209).
  Meta/non-operation proof requires the complete candidate context to match a strict meta-only grammar; unknown trailing content prevents suppression. Execution cues inside quote spans do not trigger the ambiguous-reference veto. Quote-only suppression likewise requires no unknown outside residual after removing its marker, harmless terminators, and actions explicitly anchored to another named operation.
  Modal/contraction `not not` and outer negations such as `not the case that`, `not saying that`, and `cannot say that` are now treated as double negation. `except when`, `until`, pending-approval, and passive emergency exceptions remain conditional. Cross-sentence `follow/apply + pronoun` and Japanese `適用` / `従う` references now veto global meta-only suppression as ambiguous execution.
  Strong Japanese unconditional forms (`例外なく`, `緊急時にも`, and `原則ではなく絶対に`) no longer trigger broad exception markers. A causal assurance after a simple operation negation no longer turns its independent predicate negation into a false double negation.
  Contracted auxiliaries and `never` now share the same operation-scoped simple/double-negation grammar, including contracted outer reporting negation. Approval gating also recognizes `before`, `prior to`, and `while ... is pending`; ambiguous execution references recognize `follow`, `apply`, and `proceed with` against pronouns or named procedures. Japanese causal assurances include impact statements.
  Outer uncertainty now includes `not true that` and `no guarantee/assurance/certainty that`; contracted modal negation in the inner operation clause is handled by the same grammar as expanded auxiliaries.

## [2.0.0] - 2026-07-20

### Breaking

- `mark-passes --force` now also requires `--approved-by-user` and exits 2 without it. The new flag is an explicit user-approval declaration, not a validation bypass on its own: orchestrators must not set it autonomously, and it is only used when the user explicitly instructs an override. State records `force_approved_by_user` alongside the existing `force_reason`, and `mission-audit.py` reports a new P0 finding for forced passes that lack it (#185, #193).
- `set phase=` is now validated against a phase enum. Unknown values exit 2; the four known aliases (`execution`, `review`, `plan`, `score`) normalize to their canonical form with a warning. A real run previously set `phase=execution` unchecked and polluted `phase_duration_totals` (#188, #191).

### Added

- `mission-state.py stats` now reports `by_review_tier` (same shape as `by_complexity`) and `iteration_by_review_tier`, so light-tier rework can be monitored with a single command. States written before the tier existed aggregate under `unknown` (#180, #182).
- State now records `cli_version`, and a version-skew detector scans the Claude Code and Codex plugin caches to surface a stale install against the running CLI (#186, #195).
- `mark-halt` and `halt --all` accept `--category`, backed by a shared `HALT_CATEGORIES` enum (`blocked-external` / `awaiting-approval` / `partial-done` / `stagnation` / `user-abort` / `stale` / `other`). Missing or invalid values fall back to `other` with a warning, because an emergency stop must never fail on a bad category. Automatic halts now record `stale` (#190, #192).
- `mark-passes` prints a warning when optional specialists were selected but never closed out with any invocation status, along with the `specialists log-invocation --status skipped` command that closes them. The pass gate itself is unchanged (#189, #194).

### Changed

- Explicit user instructions such as "release" or "deploy to production" now count as advance approval for that matching irreversible action. Mission does not repeat the same confirmation immediately before execution unless the target, scope, rollback conditions, or required destructive operations materially change (#197).
- `_derive_next_action` now detects a score entry that is `score_source=scoring-json` but missing `findings_evidence_path`, and drives the retry through state instead of instructions alone. A real Codex run had escaped to `--force` when its `aggregate-reviews` output was unavailable, while other runs in the same period self-recovered (#187, #196).

### Fixed

- `task_profile.risk=high` keywords are calibrated with the same policy as #174: `prod` removed (redundant with `production`, and a false-positive source for `product`/`productivity`), `auth` replaced with `authenticat`/`authoriz`/`oauth`, and bare `token` replaced with six compound forms. Retroactive analysis over 506 missions: `risk=high` drops from 72 to 53, risk-driven escalation from 17 to 9, with missed cases unchanged at 3 (#175, #183).
- `mission-audit.py` now recognizes scoring evidence stored as `iter-N-<mission8>/scoring.{json,md}` inside archived worktree bundles, preventing false historical `missing-scoring-evidence` findings after worktree cleanup (#201).

## [1.2.0] - 2026-07-10

### Added
- `mission-state.py init` and `mission-state.py set` now derive and record a `review_tier` (`light`/`standard`/`full`) from session complexity and mission text, with a risk escalator (high-risk profiles, irreversible/production/security keywords) that only promotes, never demotes; `reviewer_count` is wired to the tier, pass gates and scoring thresholds are unchanged, and user-specified overrides are auditable through recorded `source` and `signals` (#168, #171).
- ADR-003 documents the adaptive review-gating decision: tier derivation table, escalator semantics, gate-invariant declaration, and supporting context from tail-v1 results and the 451-mission production aggregate (#169, #172).
- `docs/CASE_STUDIES.md` and `docs/CASE_STUDIES.ja.md` present anonymized evidence from 451 scored production missions, including pass-rate distribution, 24 forced iterations, 7 approval-gate halts on irreversible actions, and 6 representative case summaries with explicit provenance, limitations, and no comparative quality claims (#155, #158).
- Benchmark runner now supports a tail-first-failure cohort with planted-defect task fixtures: quality markers are defect-specific content tokens, `forbidden_markers` subtract from the net score, `hidden_paths` sanitization deletes the answer-key task file from the cloned worktree before either arm runs, and `markers_hidden` keeps marker names out of both prompts (#153, #156).
- Benchmark report for the tail-v1 paired run (10 tasks × 2 arms, claude-sonnet-5, 2026-07-07): arms tied on quality scores, mission arm used ~5.8× the time and ~7.4× the cost of the goal arm; iteration-1 self-gate passed on all five mission runs (#162).
- Benchmark smoke-v2 (N=1, 2026-07-10) verifies the health-interval marker pattern fix: goal arm score recovered from 0.86 to 1.00; mission arm was `api_usage_limit`-blocked and excluded from the quality comparison (#170, #173).

### Changed
- Benchmark runners now apply form-stripped scoring before marker matching: `strip_form` removes headings, label-only lines, horizontal rules, and table separator rows so template structure earns no marker credit; the pre-strip score is preserved as `quality_marker_score_raw` and `quality_score_method` is updated to `automated_heuristic_form_stripped_not_blind_human` (#154, #157).
- SKILL.md now documents light-tier operational discipline (one reviewer, required-only specialists, critic on failure only) and READMEs include an adaptive-gating summary paragraph; pass gate thresholds are unchanged (#169, #172).
- READMEs now carry measured-evidence positioning: on the tail-v1 run both arms scored equally while mission used ~5.8× time and ~7.4× cost, and production value concentrates in the ~5% forced-iteration tail and approval halts (#161).

### Fixed
- `review_tier` escalator keywords are calibrated against a 505-mission retroactive analysis: `push`/`merge` removed (standard dev-flow false positives), bare `token`/`auth` replaced with compound/stem forms, and bare `削除` replaced with data-deletion compounds; Simple/Standard over-escalation drops from 39.1% to 32.2% with no increase in missed low-scoring missions (#174, #178).
- `mission-audit.py` now treats `specialists_phase_plan` providers as advisory scheduling hints for `specialist-invocation-gap`, preventing planned-only providers from being reported as missing terminal invocations (#176).
- Specialist phase-plan providers now count as selected evidence providers for shared accounting, preventing false `unselected-specialist-invocation` findings when planned execution/review/synthesis providers are invoked (#165).
- `mission-audit.py` and `mission-state.py stats` now ignore non-session metadata JSON such as archived worktree `aggregate.json`, preventing false abandoned `unknown` sessions and low-pass-rate findings (#163).
- `mission-audit.py --since` and `--until` now accept ISO timestamps as well as date-only values, preventing same-day records after an automation cutoff from being silently excluded (#159).
- `mission-audit.py` now recognizes scoring evidence stored in `mission-archive/` worktree paths, preventing false `missing-scoring-evidence` findings after worktree cleanup (#151, #152).
- Benchmark health-interval marker patterns now match `HEALTH_CHECK_INTERVAL_SECONDS=75`, `(75`, and `` 75` `` quoted forms; affects future runs only, recorded scores are unchanged (#162).

## [1.1.1] - 2026-07-06

### Fixed
- Command providers can now classify explicit approval or human-input blockers as `awaiting-input` through `result_contract.awaiting_input_markers` or `result_contract.awaiting_input_exit_codes`, instead of flattening optional external-review blockers into generic failures (#145).

### Changed
- Specialist registry documentation now separates external-send, browser automation, browser session material, and paid quota consent scopes, making clear that first-use consent is not blanket approval for session-cookie reuse or paid model usage (#146).
- Added Oracle command-provider safe-default guidance so local wrappers default to manual login or `awaiting-input`, and only pass `--copy-profile` after explicit browser-session-material approval (#147).

## [1.1.0] - 2026-07-05

### Added
- `mission-state.py aggregate-reviews` now converts strict `mission-review/1` reviewer JSON into deterministic `push-score --scoring-json` payloads, including rubric caps, reviewer consensus, open High counts, and archived findings evidence (#119).
- `mission-state.py specialists log-invocation --selection-source task-required` records task-mandated source retrieval or evidence providers as selected specialists without hard-coding private skill names (#115).
- `mission-state.py resume` now prints the recovery order for active sessions, including the current mission state, latest artifact, next action, progress checkpoint, and stale-session hints (#123).
- The benchmark runner now supports arm-blind scoring, counterbalanced order, explicit `model_id` capture, and updated result/report schemas for mission-vs-goal comparisons (#129, #130).

### Changed
- `aggregate-reviews` now keeps reviewer agreement out of score `items`, records it as independent `review_agreement` plus `agreement_detail`, and `mark-passes` gates very low agreement (`max-min > 1.5`) before passing (#126).
- `mark-passes` now treats machine-derived findings evidence as the primary pass gate for `score_source=scoring-json`, rejecting missing `findings_evidence_path` values or mismatched High-finding counts before applying the score threshold (#121).
- Standard Phase 5 orchestration now uses reviewer `mission-review/1` JSON, `aggregate-reviews`, and `push-score --scoring-json` without spawning `mission-scorer`; `mission-scorer` is now documented as a fallback prose-to-JSON converter only (#120).
- `mission-state.py` and `mission-audit.py` now share mission state classification, duration, specialist checkpoint, and preparation-marker logic through `skills/mission/lib/mission_common.py`, reducing audit/state drift risk (#127).
- `mark-passes` now rejects new Standard, Complex, and Critical sessions that lack a `task_profile` plus `specialists_decision.policy` checkpoint, while accepting explicit fallback/degraded decisions as valid checkpoints (#112).
- `cleanup-stale` can now halt stale active no-score sessions even when their recorded agent PID is still alive, after the configurable `MISSION_STALE_ACTIVE_SECONDS` threshold (#113).
- Public ref docs and packaged plugin mirrors were swept for OSS portability, removing maintainer-local home paths and private skill names from distributed setup examples (#118, #132).
- README, Codex setup docs, critic/planner handoff guidance, and slimmed mission skill instructions now match the source scoring flow: `mission-review/1`, `aggregate-reviews`, `push-score --scoring-json`, independent review-agreement gating, and `open_high`/findings evidence pass checks (#128, #134, #137, #140, #141, #142).

### Fixed
- Mission audit now recognizes explicit `score_history[].scoring_evidence_path` values and JSON scoring evidence stored in normal or archived worktree `.mission-state` trees (#111).
- Mission audit now separates fresh active no-score planning sessions from actionable specialist accounting debt, while reporting stale active no-score sessions explicitly in JSON and Markdown output (#113, #114).
- `push-score --scoring-json` now rejects inflated self-reported scalar scores and requires `--resubmit-reason` before replacing a score for the same iteration, preventing silent score evidence overwrite or transcription inflation (#122, #131).
- Documentation consistency guards now cover the `open_high` gate, `findings_evidence_path`, `--scoring-json`, `--root`, README test-count freshness, and the v1.1.0 release themes (#128, #134).

## [1.0.7] - 2026-07-03

### Fixed
- `mission-state.py` and `mission-migrate.py` now import `from __future__ import annotations`, so PEP 604 union annotations no longer crash module load on Python 3.9 (macOS Xcode CLT `python3`), which previously killed every command from skill startup step 1 (#99).

### Added
- `mission-state.py codex-preflight` checks whether the current Codex `/mission` session has an active state, whether a user Stop hook references `mission-stop-guard.sh`, and whether the `mission-state.py next` fallback is available. It warns on skills-only Codex runs and can fail with `--require-stop-hook`, preventing Issue #108's "no state, no guard, premature final" failure mode.
- `specialists recommend --user-specified <skill,skill>` treats skills the user explicitly named in the mission description as confirmed selections: they are recorded as `selected` with `selection_source: user-specified` even on high-risk task profiles, so later `log-invocation` calls no longer reject with a `--selection-source confirmed-user` demand (#100). If any named provider still needs first-use consent, or a required specialist is missing, the whole decision falls back to the normal confirmation flow.
- `mission-state.py push-score --scoring-json <path>` (ADR-002 Stage 1) reads scorer items from a structured JSON file, recomputes `composite`/`min_item` server-side, rejects unknown item keys and out-of-range values, archives the payload as `iter-N-<mid8>-scoring.json` with `_meta`, and records `score_source`/`scoring_evidence_path` on the score entry — removing the orchestrator score-transcription layer.
- `push-score` now rejects submissions where every item score is `<= 1.0` as suspected 0-1-normalized scale input (regression guard for a logged session that pushed composite 0.96 = 4.8/5).
- `mission-state.py next` (ADR-002 Stage 3) derives the single next action from session state (`run-planner`/`run-reviewers`/`run-scorer`/`mark-passes`/`report-blocker`/...), giving Codex sessions and post-compaction resumes a harness-independent, deterministic progression guide instead of prose-only instructions.

### Changed
- Evidence-less `push-score` calls now hard reject by default. Use `--scoring-json` (preferred) or `--scoring-output`; `MISSION_REQUIRE_SCORING_EVIDENCE=0` remains as a temporary escape hatch for migration-only runs (#105).
- Removed the generated scoring evidence fallback for evidence-less `push-score`, so successful score entries are no longer backed by reviewer-less `generated=true` archive files (#105).

## [1.0.6] - 2026-07-02

### Fixed
- `mission-state.py init` now quarantines corrupt session JSON instead of crashing during same-session mission changes.
- `mission-state.py set` now freezes pass, score history, and threshold fields so completion gates cannot be bypassed through raw state updates.
- `mission-state.py push-score` now warns when supplied scalar scores diverge from the supplied item scores.
- Stop hook CWD discovery now avoids slow `lsof` hangs, prefers Linux `/proc/<pid>/cwd`, honors direct session lookup, and skips stale auto-halt for `awaiting_user` sessions.
- Specialist tie handling now auto-selects installed optional low/medium-risk providers deterministically and records the tie-break reason.
- Mission executor now declares bounded allowed tools without `Agent` or `rm` access.
- Specialist task-profile classification now recognizes architecture/system-design missions, so architecture-only project or user providers can be selected instead of being hidden behind documentation fallback.
- Mission audit now recognizes scorer evidence stored in archived worktree `iteration-archive/` directories, preventing false `missing-scoring-evidence` findings when the scoring artifact is present.
- Mission audit now classifies JSON-identical archive-only worktree state copies as resolved duplicates, preventing cross-root audits from reporting expected archive/archive copies as P1 `duplicate-state` findings.

### Added
- ADR-002 now defines the staged typed mission state object roadmap for Findings, Scores, Decisions, and Actions while preserving local JSON + flock storage.
- `mission-state.py artifact` CLI manages local-first mission artifacts with archived evidence; see `docs/MISSION_ARTIFACTS.md`.
- Specialist registry `kind: command` providers can now declare `env` and `timeout` runtime configuration, passed only to that provider process (CLI `--timeout` still overrides).

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

[2.0.0]: https://github.com/tackeyy/mission/releases/tag/v2.0.0
[1.2.0]: https://github.com/tackeyy/mission/releases/tag/v1.2.0
[1.1.1]: https://github.com/tackeyy/mission/releases/tag/v1.1.1
[1.1.0]: https://github.com/tackeyy/mission/releases/tag/v1.1.0
[1.0.7]: https://github.com/tackeyy/mission/releases/tag/v1.0.7
[1.0.6]: https://github.com/tackeyy/mission/releases/tag/v1.0.6
[1.0.5]: https://github.com/tackeyy/mission/releases/tag/v1.0.5
[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
