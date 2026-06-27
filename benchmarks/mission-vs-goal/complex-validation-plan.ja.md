# Complex task validation plan

Status: planned, not measured.

最初の 10-task pilot では、completion rate と validator pass rate は両 arm とも
100% だったため差が出ませんでした。一方で、`mission` は automated evidence /
completion-quality score が高く、runtime は長くなりました。この結果だけでは、
より曖昧で、複数ファイルにまたがり、context loss、security sensitivity、stop/go
decision を含むタスクで何が起きるかはまだ分かりません。

この plan は、追加の性能主張をせずに、次の検証ステップを定義するものです。

## Research Question

タスクが十分に複雑で、「完了」の判断に state tracking、hypothesis discipline、
safety gates、cross-artifact consistency が必要な場合、`mission` は goal-only
baseline と比べて completion quality を改善するか。

## Hypotheses

以下は検証すべき仮説であり、結果ではありません。

| Hypothesis | Why it may happen | How to falsify it |
|---|---|---|
| `mission` は complex tasks で evidence completeness を改善する。 | plan、review、score、stop-decision evidence を残す workflow だから。 | complex cohort 全体で goal-only artifacts も同等の evidence を残す。 |
| `mission` は stop/go や safety-gated tasks で premature "done" を減らす。 | threshold-gated completion により未解決 risk が見えやすいから。 | 両 arm が同等に安全な stop/go decision を同等の evidence 付きで出す。 |
| `mission` は遅い。 | state、review、scoring の追加作業があるから。 | runtime 差がほぼない、または goal-only の retry で時間差が消える。 |
| tightly scoped な complex tasks では goal-only も競争力がある。 | 複雑に見えても validator が deterministic な task があるから。 | deterministic task も含めて `mission` が一貫して大きく上回る。 |

## Task Set

次の cohort では `tasks.complex.json` を使います。

- 10 tasks。
- arm は同じく `goal_only` と `mission`。
- `mission` tasks は前回の `Simple --max-iter 1` ではなく、`Complex` または
  `Critical` の state initialization を使います。
- cohort は local controlled pilot です。general model benchmark ではありません。

## Execution Protocol

過去 results を上書きしないよう、clean starting commit と unique run id を使います。

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-local \
  --starting-commit <commit> \
  --timeout 1800
```

推奨する first pass:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-smoke \
  --starting-commit <commit> \
  --limit 2 \
  --timeout 1800
```

full 20-run complex cohort の前に、smoke run で 1 task x 2 arms が完了することを確認します。

## Measurement Requirements

| Metric | Requirement |
|---|---|
| Completion rate | 完了した task artifact だけを数える。 |
| Validator pass rate | task-specific validator text と required headings を満たす run だけを数える。 |
| Quality score | blind human review を実際に行わない限り automated heuristic と明記する。 |
| Evidence completeness | memory や仮定ではなく artifact と raw logs から採点する。 |
| Resume success | resume / context recovery が prompt に含まれる task のみ採点する。 |
| Runtime | runner が記録した wall-clock minutes を使う。 |
| Intervention count | run 開始後の human clarification / correction を数える。 |

## Interpretation Rules

full measurement 後に言ってよい:

- "この controlled complex-task cohort では、`mission` は Y scoring method において X の measured outcome だった。"
- "`mission` はこの local run で平均 X minutes 遅い / 速い。"
- "効果は task category A/B に集中していた。"

追加 evidence なしに言ってはいけない:

- "`mission` は goal-only より一般に優れている。"
- "`mission` はより賢い。"
- "complex task には常に `mission` が必要。"
- sample size、local setup、task mix、scoring method を隠した主張。

## Exit Criteria

complex validation を marketing use に回せるのは、以下をすべて満たした後です。

1. 同じ starting commit から 20 / 20 paired executions を attempt する。
2. Raw JSONL、summary JSON、artifacts を保存する。
3. reports に quality scoring が automated heuristic か blind human かを明記する。
4. English / Japanese reports を更新する。
5. tests で task-set shape、runner configuration、report honesty を検証する。
6. unsupported claims を明示的に unsafe として列挙する。
