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
| Irreversible keyword (EN) | `deploy`, `release`, `migration`, `drop`, `delete`, `publish`, `production` — case-insensitive substring match in mission text |
| Irreversible keyword (JA) | `本番`, `リリース`, `マイグレーション`, `データ削除`, `レコード削除`, `物理削除`, `公開`, `決済` — substring match |
| Security keyword (EN) | `secret`, `credential`, `password`, `api token`, `api-token`, `api_key`, `access token`, `access-token`, `bearer`, `authenticat`, `authoriz`, `oauth` — case-insensitive substring match |
| Security keyword (JA) | `認証`, `秘密`, `鍵` |

Every occurrence of an irreversible keyword is evaluated in sentence/contrast
clauses within paragraph, list-item, blockquote, and heading-delimited logical
units. Negation, execution, and quote-only intent are anchored to the matching
operation rather than applied to every nearby keyword.
Only an explicit, simple statement that the actual operation will not happen is
suppressed. Conditional, double-negative, uncertain, and merely quoted contexts
remain conservative escalations; an explicit quote-only intent may be suppressed.
Those intents do not leak across a conjunction, exception clause, or structural
unit. Segment boundaries are indexed once per mission so repeated keyword
lookups do not rescan the full text. Security keywords,
`task_profile.risk=high`, and the Complex/Critical base tiers are never suppressed
by this context rule.

Included signals remain recorded in the backward-compatible
`review_tier_signals` string list as `irreversible-keyword:<kw>` or
`security-keyword:<kw>` entries, in constant order with one entry per keyword.

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
`review_tier_signal_details` additively records every matched occurrence with its
matched text, bounded context, include/suppress decision, reason, source, and
source span. This makes a suppressed negated candidate auditable through `get`
without changing the existing string-list contract.

### User override

Users may override via `init --review-tier <value>` or `set review_tier=<value>`.
When the override is lower than the auto-derived tier, `mission-state.py` emits
a `WARNING` to stderr but does not reject the value. `review_tier_source` is set
to `"user"` in both cases.

When `complexity` is later changed via `set complexity=` and
`review_tier_source` is `"auto"`, the tier is re-derived and `reviewer_count`
is synced. When `review_tier_source` is `"user"`, the tier and `reviewer_count`
are preserved across complexity changes.

When `set review_tier=` changes an existing auto-derived state, the existing
signals and details remain observation provenance. With
`review_tier_source="user"`, they are not the rationale for the applied tier.
An `init --review-tier` state starts with empty signals and details.

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
- `review_tier_source`, `review_tier_signals`, and
  `review_tier_signal_details` make the gating decision
  auditable and adjustable without removing the gate.
- Backward-compatible: state files without `review_tier` are handled
  gracefully by `set`, `next`, and `get` commands.

### Limitations (honest)

- **Cost reduction not yet measured.** The effect of `review_tier` on total
  API spend or wall-clock time has not been quantified in production runs.
  Do not cite this ADR as evidence of cost reduction until production
  measurements are available.
- **The escalator is conservative.** The escalator errs on the safe side;
  keyword calibration is an ongoing process as retrospective data accumulates.
- **Keyword calibration is iterative.** The initial keyword lists were chosen
  conservatively. Issue #174 applied the first retrospective calibration
  (see below). Further refinement should be driven by accumulated production data.

### Calibration (Issue #174, 2026-07-10)

A retrospective analysis of 506 deduplicated production missions was performed
to measure escalator false-positive rates and verify that the miss count
(Simple/Standard missions with first composite score < 4.0 that remained at
light/standard tier) did not increase.

**Before → After (506 missions):**

| Metric | Before | After |
|---|---|---|
| full tier % | 79.1% (400/506) | 76.7% (388/506) |
| Escalation rate (Simple/Standard → full) | 39.1% (68/174) | 32.2% (56/174) |
| Miss count (Simple/Standard, score < 4.0, not escalated) | 3 | 3 |

The miss count is unchanged; the calibration reduced false positives without
introducing new missed escalations.

**Keywords removed and rationale:**

- `push`, `merge` removed from irreversible EN: retrospective confirmed that
  all occurrences were standard development-flow descriptions (e.g.
  "implement → verify → PR/merge/push") rather than irreversible production
  actions. True irreversible intent is co-signalled by `deploy`, `production`,
  or `本番` which remain in the list.
- Bare `token` removed from security EN, replaced with compound phrases
  (`api token`, `api-token`, `api_key`, `access token`, `access-token`,
  `bearer`): retrospective showed that a product name containing the bare
  keyword generated spurious escalations unrelated to credential handling.
- Bare `auth` removed from security EN, replaced with stems `authenticat`,
  `authoriz`, and `oauth`: retrospective confirmed false fires on
  "awaiting external authority" — the stem `authoriz` does not appear in
  `authority`, while still matching `authorization` and `authorized`.
- Bare `削除` removed from irreversible JA, replaced with compound forms
  `データ削除`, `レコード削除`, `物理削除`: reversible code-level removals
  (e.g. "delete a NavBar component") were triggering escalation.

### Calibration (Issue #175, 2026-07-10)

The same retrospective corpus (506 deduplicated missions) was re-run to measure
the effect of calibrating `HIGH_RISK_KEYWORDS` in `classify_task_profile` using
the same policy applied to the escalator in Issue #174.

**Before → After (506 missions):**

| Metric | Before (#174 baseline) | After (#175) |
|---|---|---|
| risk=high 発火件数 | 72 件 (14.2%) | 53 件 (10.5%) |
| review_tier エスカレーション (risk=high 起因) | 17 件 | 9 件 |
| full tier % | 75.3% | 74.5% |
| Escalation rate (Simple/Standard → full) | 28.2% | 25.9% |
| Miss count (Simple/Standard, score < 4.0, not escalated) | 3 | 3 |

The miss count is unchanged. Calibration reduced risk=high false positives by 19
missions without introducing new missed escalations.

**Keywords removed and rationale (same policy as Issue #174):**

- `prod` removed: `production` already covers the intent; `prod` was the sole
  trigger on "product" / "productivity" mission descriptions.
- Bare `token` removed, replaced with compound phrases (`api token`, `api-token`,
  `api_key`, `access token`, `access-token`, `bearer`): token was the largest
  contributor (15 of 19 reduction cases) and fired on bare product-name strings
  such as "token-battle" unrelated to credential handling.
- Bare `auth` replaced with stems `authenticat`, `authoriz`, `oauth`:
  eliminated 2 false-fire cases from "authority"-containing descriptions.

## Implementation Notes

Implemented in `skills/mission/bin/mission-state.py` (Issue #168):

- `TIER_REVIEWER_COUNT` and `REVIEW_TIER_BASE` constants define the mapping.
- `derive_review_tier_decision()` is a pure function that returns per-occurrence
  provenance; `derive_review_tier()` preserves the existing tuple API. They are
  covered by `test_issue168_review_tier.py` and
  `test_issue209_review_tier_negation.py`.
- `cmd_init` stores `review_tier`, `review_tier_source`, `review_tier_signals`,
  `review_tier_signal_details`, and `reviewer_count` in the initial state.
- `cmd_set` validates the value, warns on downgrade, and syncs `reviewer_count`
  unless `reviewer_count` is also set explicitly in the same call.
- `set complexity=` re-derives tier when `review_tier_source == "auto"`.

Operational guidance for the light tier is in
`skills/mission/refs/state-management.md` under
"review_tier 導出と Light Tier 運用".
