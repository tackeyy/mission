# ADR-003: Adaptive Review Gating via review_tier

## Status

Accepted

## Date

2026-07-10

## Context

Two independent evidence sources shaped this decision.

**Benchmark (tail-v1, 2026-07-07):** The `2026-07-07-claude-goal-vs-mission-tail-v1`
run used five planted-defect tasks scored on content recall with no structure
credit, both arms running the same model. `/goal` and `mission` tied on all
three primary metrics (completion, validator pass rate, marker detection).
`mission` ran its full plan-review-score loop and cleared its own 4.0 pass
gate at iteration 1 in all five runs. It cost roughly 5.8x the wall-clock time
and 7.4x the recorded USD spend. See
[`benchmarks/mission-vs-goal/report.md`](../../benchmarks/mission-vs-goal/report.md).

**Production aggregate (451 missions, 2026-07-07):** Across 451 scored
production missions, 95% passed the quality gate at iteration 1 without any
review-forced change. The measured value of the review loop concentrated in the
~5% tail (24 missions forced to iterate, e.g. factual errors and runtime bugs
caught by reviewers) and in 7 halts that stopped irreversible production
actions pending approval. See [`docs/CASE_STUDIES.md`](../CASE_STUDIES.md).

The standard three-reviewer review added overhead on the 95% pass-through
majority. That majority was disproportionately lower-complexity work (Simple or
Standard) with no irreversible-keyword or high-risk signals. There was no
mechanism to narrow review depth for clearly lower-risk missions while keeping
the same completion gate semantics.

## Decision

Introduce `review_tier` (values: `light`, `standard`, `full`) as a per-mission
property derived at `init` time and stored in state alongside an audit trail.

### Tier derivation

`review_tier` is derived by `derive_review_tier(mission_text, complexity,
task_profile_risk)` in `mission-state.py`.

Base mapping (`REVIEW_TIER_BASE`):

| complexity | base review_tier |
|---|---|
| Simple | light |
| Standard | standard |
| Complex | full |
| Critical | full |
| None / Unknown / unrecognized | standard (safe fallback) |

Escalator conditions (any match promotes the base tier to `full`; there is no
downgrade path):

| Signal | Trigger |
|---|---|
| `task_profile.risk=high` | `task_profile` risk field is `"high"` |
| Irreversible keyword (EN) | `deploy`, `release`, `migration`, `drop`, `delete`, `publish`, `production`, `push`, `merge` — case-insensitive substring match in mission text |
| Irreversible keyword (JA) | `本番`, `リリース`, `マイグレーション`, `削除`, `公開`, `決済` — substring match |
| Security keyword (EN) | `auth`, `secret`, `token`, `credential`, `password` — case-insensitive |
| Security keyword (JA) | `認証`, `秘密`, `鍵` |

Matched signals are recorded in `review_tier_signals` as
`irreversible-keyword:<kw>` or `security-keyword:<kw>` entries.

### Reviewer count

`TIER_REVIEWER_COUNT` maps tier to reviewer count:

| review_tier | reviewer_count |
|---|---|
| light | 1 |
| standard | 2 |
| full | 3 |

Light tier additionally narrows specialist engagement: only `required: true`
specialists are auto-selected; optional specialists are not. The critic is
spawned only when findings require another iteration (fail path), not as a
default step after every scoring pass.

Standard and full tiers behave as they did before this ADR.

### Audit trail

`review_tier_source` records whether the tier is `"auto"` (derived at init) or
`"user"` (set via `--review-tier` or `set review_tier=`).
`review_tier_signals` records the escalator reasons that triggered promotion to
`full`, or an empty list for tiers that were not escalated.

### User override

Users may override via `init --review-tier <value>` or `set review_tier=<value>`.
When the override is lower than the auto-derived tier, `mission-state.py` emits
a `WARNING` to stderr but does not reject the value. `review_tier_source` is set
to `"user"` in both cases.

When `complexity` is later changed via `set complexity=` and
`review_tier_source` is `"auto"`, the tier is re-derived and `reviewer_count`
is synced. When `review_tier_source` is `"user"`, the tier and `reviewer_count`
are preserved across complexity changes.

### Gate semantics are invariant

`review_tier` does not change the pass/fail gate. The completion gate remains:

```
findings_evidence_path exists
AND evidence_high_count == open_high
AND max_agreement_delta <= 1.5
AND composite >= threshold  (default 4.0)
AND min(scored_items) >= 3.5
AND open_high == 0
```

A light-tier mission that accumulates open High findings, fails the agreement
gate, or scores below threshold does not pass. `mark-passes` enforces the same
conditions regardless of `review_tier`.

## Consequences

### Positive

- Review depth for Simple/Standard missions with no escalator signals narrows
  from three reviewers to one, reducing review overhead for the 95%
  pass-through majority.
- `review_tier_source` and `review_tier_signals` make the gating decision
  auditable and adjustable without removing the gate.
- Backward-compatible: state files without `review_tier` are handled
  gracefully by `set`, `next`, and `get` commands.

### Limitations (honest)

- **Cost reduction not yet measured.** The effect of `review_tier` on total
  API spend or wall-clock time has not been quantified in production runs.
  Do not cite this ADR as evidence of cost reduction until production
  measurements are available.
- **The escalator is conservative.** High-frequency operation terms —
  including `release`, `merge`, and `push` — promote even Simple missions to
  `full` tier. The escalator errs on the safe side; false-positive calibration
  based on production data is deferred to a follow-up task.
- **Keyword calibration deferred.** The keyword lists were chosen
  conservatively at initial implementation. Refinement is a separate task.

## Implementation Notes

Implemented in `skills/mission/bin/mission-state.py` (Issue #168):

- `TIER_REVIEWER_COUNT` and `REVIEW_TIER_BASE` constants define the mapping.
- `derive_review_tier()` is a pure function with no side effects; covered by
  `skills/mission/tests/test_issue168_review_tier.py`.
- `cmd_init` stores `review_tier`, `review_tier_source`, `review_tier_signals`,
  and `reviewer_count` in the initial state.
- `cmd_set` validates the value, warns on downgrade, and syncs `reviewer_count`
  unless `reviewer_count` is also set explicitly in the same call.
- `set complexity=` re-derives tier when `review_tier_source == "auto"`.

Operational guidance for the light tier is in
`skills/mission/refs/state-management.md` under
"review_tier 導出と Light Tier 運用".
