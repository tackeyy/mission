# mission vs goal-only pilot report

Status: draft until all 20 paired runs are complete.

## Executive Summary

In a 10-task internal pilot, we compared `mission` with a goal-only baseline on
the same task set, model, starting commits, and validator criteria.

Do not publish this section until the result table below is complete.

## Method

- Benchmark: `mission-vs-goal-pilot`
- Task count: 10
- Paired runs: 20 total, one `goal_only` and one `mission` run per task
- Baseline: measurable goal plus normal agent execution
- Treatment: `/mission` with persistent state, plan/review/score phases, and
  threshold-gated completion
- Primary evidence: task validators, final artifacts, run notes, human quality
  review, and intervention counts

## Results

| Metric | goal_only | mission | Notes |
|---|---:|---:|---|
| Completion rate | TBD | TBD | Count completed tasks out of 10. |
| Validator pass rate | TBD | TBD | Count validator-passing tasks out of 10. |
| Average human quality score | TBD | TBD | Mean of 1-5 reviewer scores. |
| Average intervention count | TBD | TBD | Lower is better. |
| Resume success rate | TBD | TBD | Only tasks where resume applies. |
| Average evidence completeness | TBD | TBD | Mean of 1-5 evidence scores. |
| Average elapsed minutes | TBD | TBD | Report with caution; tool availability can dominate. |

## Task-Level Findings

| Task | Stronger arm | Why |
|---|---|---|
| `docs-small-edit` | TBD | TBD |
| `docs-cross-reference` | TBD | TBD |
| `bug-regression-test` | TBD | TBD |
| `review-comment-batch` | TBD | TBD |
| `interrupted-doc-task` | TBD | TBD |
| `ambiguous-research-brief` | TBD | TBD |
| `release-checklist-audit` | TBD | TBD |
| `small-refactor` | TBD | TBD |
| `quality-gate-failure` | TBD | TBD |
| `marketing-claim-draft` | TBD | TBD |

## Publishable Claim Checklist

Before publishing any claim, verify:

- All 10 tasks have paired `goal_only` and `mission` records.
- Every result record conforms to `result.schema.json`.
- The report states the sample size and task mix.
- The report says this is an internal pilot, not a general model benchmark.
- The claim names the workflow behavior measured, not model intelligence.
- Any percentage includes numerator and denominator.
- Any example includes enough artifact evidence to reproduce the assessment.

## Safe Claim Patterns

Use:

> In a 10-task internal pilot, `mission` helped most on multi-step tasks where
> the risk was stopping before review, evidence, or validators passed.

Use:

> Goal-only execution remained a reasonable fit for small, single-step tasks
> with obvious validators.

Avoid:

> `mission` is X% smarter than `/goal`.

Avoid:

> `mission` always beats goal-based workflows.

## Raw Result Index

Add one JSONL file per run batch:

```text
results/YYYY-MM-DD-run-001.jsonl
```

Each line should conform to `result.schema.json`.
