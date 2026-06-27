# mission vs goal-only pilot benchmark

このディレクトリには、`mission` と goal-only 実行 baseline を
マーケティング利用してよい粒度で比較するための 10 タスク pilot benchmark を置きます。

この benchmark は意図的に小さくしています。目的は、大きな性能主張を出す前に、
`mission` が意味を持つタスクタイプと、軽量な goal condition で十分なタスクタイプを
見分けることです。

最初に計測した cohort は `tasks.json` です。より複雑なタスクの検証は
`tasks.complex.json` と `complex-validation-plan.ja.md` に分けています。
complex-task outcomes は、別 run id の raw JSONL と artifacts がそろうまで未測定です。

## Research Question

同じ agent model に同じ task objective を与えたとき、`mission` の stateful な
plan / review / scoring / iteration loop は、goal-only baseline と比べて
複数ステップ作業の outcome を改善するか。

## Arms

| Arm | Setup | 検証するもの |
|---|---|---|
| `goal_only` | task を測定可能な goal に変換し、agent が goal 達成と判断するまで通常実行する。 | 軽量な completion-condition workflow。 |
| `mission` | 同じ objective を `/mission` で実行し、state、review、scoring、threshold gate を使う。 | stateful な loop-engineering workflow。 |

両 arm では、同じ model、repository state、branch starting point、tool permission、
time budget、task prompt を使います。

## Metrics

| Metric | Definition |
|---|---|
| `completion` | 必要な artifact または code change が作られ、未解決のまま停止していない。 |
| `validator_pass` | task 固有の validator が pass した。例: test、lint、schema check、file assertion、review checklist。 |
| `human_quality_score` | 下記 rubric に基づく 1-5 の blind reviewer score。 |
| `intervention_count` | 実行開始後に必要だった人間の clarification、correction、restart の回数。 |
| `resume_success` | interruption task で、task state を失わずに resume できたか。 |
| `evidence_completeness` | commands、artifacts、reviewer notes、final state など、「done」と言える証跡が残っているか。 |
| `elapsed_minutes` | agent の最初の action から final answer までの wall-clock runtime。 |
| `token_estimate` | 取得できる場合の token usage。取得できない場合は null。 |

## Protocol

1. clean checkout または isolated worktree から開始する。
2. `tasks.json` の各 task について、同じ starting commit から `goal_only` arm と
   `mission` arm を実行する。
3. 可能なら run order を counter-balance する。task id ごとに先に実行する arm を
   交互にし、一方だけが operator learning の恩恵を受けないようにする。
4. 片方の arm に、もう片方の transcript を見せない。
5. `result.schema.json` に沿って、run ごとに JSONL record を 1 行保存する。
6. human quality score を付ける前に task validator を実行する。
7. 可能なら arm label を隠して human reviewer が採点する。
8. 個別例を出す場合は anonymized かつ reproducible にし、基本は aggregate results だけを要約する。

次の complex-task cohort では、明示的な task file と unique run id を指定して同じ
protocol を実行します。

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-local \
  --starting-commit <commit> \
  --timeout 1800
```

full 20-run complex executions の前に、`--limit 2` で smoke run を実行します。

## Human Quality Rubric

| Score | Meaning |
|---:|---|
| 5 | prompt、validator、evidence requirement を完全に満たし、実質的な cleanup が不要。 |
| 4 | prompt と validator を満たし、presentation または completeness に軽微な gap だけが残る。 |
| 3 | prompt を部分的に満たすが、重要な requirement を落としているか、evidence が弱い。 |
| 2 | 一部有用な作業はあるが、validator または core acceptance criteria が fail している。 |
| 1 | usable result がない、または未解決状態で停止している。 |

## Marketing Guardrails

raw evidence がそろった後に使ってよい表現:

- 「10 タスクの internal pilot では、review、resume、evidence tracking が必要な
  複数ステップ作業で `mission` が completion quality を改善した」
- 「`mission` は、主なリスクが stopping too early である作業で最も有用だった」
- 「小さく単一ステップの task では、goal-only execution でも十分なケースがあった」

この pilot からは言えない表現:

- general model intelligence に関する主張。
- `mission` が `/goal` より普遍的に優れているという主張。
- denominator、task mix、scoring method を出さない percent improvement。
- 10 個すべての paired task run が完了していない状態での性能主張。

## Files

| Path | Purpose |
|---|---|
| `tasks.json` | 計測済みの固定 10 タスク baseline pilot set。 |
| `tasks.complex.json` | planned 10-task complex cohort。まだ未測定。 |
| `result.schema.json` | result record 1 件分の JSON Schema。 |
| `report.md` | 英語の current measured status と package-validation results。 |
| `report.ja.md` | 日本語の current measured status と package-validation results。 |
| `report-template.md` | 英語の publishable report skeleton と claim guardrails。 |
| `report-template.ja.md` | 日本語の publishable report skeleton と claim guardrails。 |
| `complex-validation-plan.md` | 英語の complex task validation plan。 |
| `complex-validation-plan.ja.md` | 日本語の complex task validation plan。 |
| `README.md` | 英語版の benchmark protocol。 |
