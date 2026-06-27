# mission vs goal-only pilot report

Status: 20 件の paired run がすべて完了するまでは draft。

## Executive Summary

10 タスクの internal pilot で、同じ task set、model、starting commit、
validator criteria を使い、`mission` と goal-only baseline を比較しました。

下の result table が埋まるまでは、この section を公開しないでください。

## Method

- Benchmark: `mission-vs-goal-pilot`
- Task count: 10
- Paired runs: total 20。各 task につき `goal_only` と `mission` を 1 回ずつ実行
- Baseline: measurable goal + normal agent execution
- Treatment: persistent state、plan/review/score phases、threshold-gated completion を持つ `/mission`
- Primary evidence: task validators、final artifacts、run notes、human quality review、intervention counts

## Results

| Metric | goal_only | mission | Notes |
|---|---:|---:|---|
| Completion rate | TBD | TBD | 完了 task 数 / 10。 |
| Validator pass rate | TBD | TBD | validator pass task 数 / 10。 |
| Average human quality score | TBD | TBD | 1-5 reviewer score の平均。 |
| Average intervention count | TBD | TBD | 低いほどよい。 |
| Resume success rate | TBD | TBD | resume が該当する task のみ。 |
| Average evidence completeness | TBD | TBD | 1-5 evidence score の平均。 |
| Average elapsed minutes | TBD | TBD | tool availability の影響が大きいため慎重に扱う。 |

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

公開前に必ず確認すること:

- 10 task すべてに `goal_only` と `mission` の paired record がある。
- すべての result record が `result.schema.json` に適合している。
- report に sample size と task mix が明記されている。
- report が internal pilot であり、general model benchmark ではないと明記している。
- claim が model intelligence ではなく、測定した workflow behavior を指している。
- percentage を出す場合は numerator と denominator を併記している。
- example を出す場合は、assessment を再現できる artifact evidence がある。

## Safe Claim Patterns

使ってよい:

> 10 タスクの internal pilot では、review、evidence、validator pass 前に
> stopping してしまうリスクがある複数ステップ task で、`mission` が特に有用だった。

使ってよい:

> 小さく単一ステップで validator が明確な task では、goal-only execution も
> 十分に妥当な選択肢だった。

避ける:

> `mission` は `/goal` より X% 賢い。

避ける:

> `mission` は goal-based workflow に常に勝つ。

## Raw Result Index

run batch ごとに JSONL file を追加します:

```text
results/YYYY-MM-DD-run-001.jsonl
```

各行は `result.schema.json` に適合する必要があります。
