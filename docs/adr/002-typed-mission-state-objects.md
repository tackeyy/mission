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

## Migration Strategy

1. Keep legacy state readable. Missing `findings` or typed `decisions` means an older session, not a corrupt one.
2. Add append-only CLI commands for new typed records before requiring them in gates.
3. Extend `mission-audit.py` to read both legacy Markdown evidence and typed findings during the transition.
4. Add strict validation only after the typed paths are exercised by normal runs.

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
