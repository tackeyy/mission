# ADR-002: Typed Mission State Objects

## Status

Proposed

## Date

2026-07-02

## Context

`mission` currently stores loop state in a local JSON session file protected by `fcntl.flock` and atomic writes. That foundation is intentionally simple and portable. Several important concepts are still represented mostly as Markdown or procedural instructions: reviewer findings, score derivation evidence, Phase 6 decisions, and action preconditions.

Recent quality-gate fixes strengthen deterministic CLI gates first:

- #90 freezes pass, score, and threshold fields from `set`.
- #91 warns when scalar scores diverge from supplied item scores.
- #92 hardens corrupt session recovery.
- #93 bounds executor tools.
- #94 hardens Stop hook state discovery.

These are Stage 0 prerequisites for introducing typed state objects without replacing the storage model.

The 2026-07-02 execution-log crosscheck review (`docs/log-crosscheck-review-2026-07-02.md`) validated this direction against 426 logged sessions and identified three gaps that typed objects alone do not close:

1. **Transcription layer**: the orchestrator reads scorer output as prose and re-types the numbers into `push-score` arguments. Logged evidence: score distributions collapsed into the 4.4–4.7 band (71.4% of 220 entries), evidence-free `generated=true` scoring files averaging higher (4.54) than evidence-backed ones (4.41), and a session (`cx-019efece`, xai-cli) that pushed 0–1-normalized composites (0.96 = 4.8/5) twice before re-pushing on the 5-point scale — all accepted by validation because 0.96 is within the 0–5 range.
2. **Pass semantics**: composite-threshold-as-primary-gate lets stagnant or unreviewed iterations pass (score histories like 4.2→4.2 and 4.6→4.6 with identical notes).
3. **Harness coupling**: 84% of sessions run on Codex, where the Stop hook — the only mechanical loop guard — does not apply.

## Decision

Keep the physical storage model as a single session JSON file under `.mission-state/sessions/<sid>.json`, guarded by `StateLock` and atomic writes. Introduce typed objects incrementally inside that file rather than splitting storage into multiple files or a service.

The first typed object families are:

### Finding

```json
{
  "id": "F-001",
  "iteration": 1,
  "reviewer": "mission-reviewer",
  "severity": "High",
  "claim": "Unresolved security issue",
  "evidence": "file:line or archived evidence path",
  "file": "src/example.py",
  "line": 42,
  "status": "open"
}
```

`status` starts with `open`, then moves to `resolved`, `accepted-risk`, or `not-reproducible`. The schema should align with `scripts/mission-audit.py` finding records so live loop and retrospective audit share vocabulary.

### Score

Existing `score_history[]` remains readable. New score entries may add links to `finding_ids`, `review_evidence_paths`, and `scoring_evidence_path`. `push-score` remains the append path and may later gain strict structured-evidence validation.

### Decision

Reuse the existing `decisions: []` array for Phase 6 outcomes:

```json
{
  "id": "D-001",
  "iteration": 1,
  "action": "continue",
  "score_index": 0,
  "open_high": 1,
  "open_medium": 2,
  "assumptions": ["A1"],
  "reason": "High finding remains unresolved"
}
```

Valid actions are `continue`, `pass`, `halt`, and `ask-user`. Decisions must link to the score entry and unresolved findings that justified the action.

### Action Metadata

Future commands such as `resolve-finding` should record the action lineage from `Finding -> Execution -> Review`. This lets iter2+ diff review verify that a finding was actually addressed instead of merely omitted from Markdown.

## Staged Extensions (2026-07-02 revision)

Three stages absorbed from the execution-log crosscheck review (G-1 / G-4 / G-3). They build on the typed object families above and close the gaps listed in Context.

### Stage 1 — Structured scoring evidence, no transcription (G-1)

Reviewers and the scorer write their results as structured JSON files (Finding[] and a Score document) directly under `archive/`. `push-score` gains a `--scoring-json <path>` mode that:

- reads `items` from the file instead of free-form CLI arguments;
- recomputes `composite` and `min_item` server-side from the supplied items (denominator = items present, consistent with diff-review partial scoring);
- rejects unknown item keys after alias normalization instead of warning;
- rejects scale anomalies: if every item value is `<= 1.0`, treat as a 0–1-normalized submission and exit 2 with a hint to use the 5-point scale (prevents the `cx-019efece` class of error);
- retires the `generated=true` fallback — a `push-score` without scoring evidence is rejected rather than backfilled with an evidence-free archive file.

The orchestrator's role shrinks to passing a file path; it no longer types score values.

### Stage 2 — Pass gate redefinition (G-4)

`mark-passes` promotes finding/evidence conditions to the primary gate:

- primary: `open High findings == 0` AND required findings evidence present. For `score_source=scoring-json`, the score entry must include `findings_evidence_path`; `mark-passes` reloads that aggregate reviewer evidence, counts High findings, and rejects if the evidence count differs from `open_high` or the file is missing.
- secondary: `composite >= threshold` and `min_item >= 3.5` as today.

A high composite alone can no longer pass a session whose findings ledger is empty because reviewers never ran. This inverts today's semantics, where the score is primary and `--open-high` is an optional (and in practice unused) argument.

**Stage 2 implementation policy (2026-07-05 confirmed)**: pass is first gated by machine-derived open High findings being zero and scoring evidence existing. `open_high` is calculated from reviewer findings (`mission-review/1`) by `aggregate-reviews`; `mark-passes` validates it against `findings_evidence_path`. Self-reported `open_high` remains only for the legacy `--items` path and produces a warning before the legacy gate is applied. Threshold recalibration is deferred until Stage 2 has enough stats (`by_agent` and score distributions).

### Stage 3 — Grounded next-step command (G-3)

`mission-state.py next` reads the session state and prints the single next action (e.g. "iteration 2: run reviewers, expected artifacts: ...", "score recorded: run mark-passes", "halted: report blocker"). This gives every harness — including Codex, which runs 84% of sessions and has no Stop hook — a deterministic resumption path that does not depend on prose Compact Instructions surviving compaction. The Claude Code Stop hook remains as an additional guard, not the primary loop-enforcement mechanism.

## Migration Strategy

1. Keep legacy state readable. Missing `findings` or typed `decisions` means an older session, not a corrupt one.
2. Add append-only CLI commands for new typed records before requiring them in gates.
3. Extend `mission-audit.py` to read both legacy Markdown evidence and typed findings during the transition.
4. Add strict validation only after the typed paths are exercised by normal runs.
5. Stage order: Stage 1 (`--scoring-json` + fallback retirement) ships first because it is additive and closes the largest measured gap (score inflation / transcription errors); Stage 2 flips the gate once typed findings exist; Stage 3 (`next`) can ship independently at any point.

## Out of Scope

- Distributed storage or database-backed state.
- Background service daemons.
- Purpose-based access control.
- Heavy ontology infrastructure outside the local JSON session model.

## Consequences

Positive:

- Deterministic gates move from prose instructions into CLI-verifiable state.
- Findings, scores, decisions, and actions become auditable across iterations.
- Legacy sessions remain readable.

Negative:

- State schema grows more complex.
- CLI and audit code must support a transition period with both legacy and typed records.
- Stage 2 changes pass semantics; sessions relying on score-only passes need the transition window in Migration Strategy step 4.

## Revision History

| Date | Change |
|------|--------|
| 2026-07-02 | Initial proposal (#101). |
| 2026-07-02 | Added Staged Extensions (G-1 structured scoring evidence / G-4 pass gate redefinition / G-3 grounded `next` command) and log-crosscheck evidence to Context, per `docs/log-crosscheck-review-2026-07-02.md`. |
| 2026-07-05 | Clarified Stage 2 implementation: `aggregate-reviews` writes findings evidence, `push-score --scoring-json` records `findings_evidence_path`, and `mark-passes` rejects missing or mismatched High findings evidence (#121). |
