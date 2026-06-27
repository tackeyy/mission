# 公式 `/goal` vs `/mission` 再実行 Runbook

Status: Claude Code workspace API usage limit の復帰前に準備済み。

2026-06-28 JST の smoke run は blocked であり、比較 evidence ではありません。
`/mission` arm の raw Claude result は workspace API usage limit を報告しており、
access は 2026-07-01 00:00 UTC、つまり 2026-07-01 09:00 JST に戻ると記録されています。

## Objective

以下を clean paired comparison として実行します。

- `claude_code_goal_command`: Claude Code 公式 built-in `/goal` command。
- `mission`: `/mission` plugin workflow。

以前の local `goal_only` baseline とは混ぜません。

## Preconditions

1. Claude Code usage limit が解消済みと別途確認できない限り、2026-07-01 09:00 JST
   より前には実行しない。
2. 最新の `main` branch から開始する。
3. 両 arm で同じ starting commit を使う。
4. unique な `--run-id` を使い、過去の raw result directory を上書きしない。
5. `run_status=blocked` は infrastructure/account state と扱い、task quality と扱わない。

## Step 1: Smoke Gate

最初に 1 task だけ実行します。full benchmark budget を使う前に、両 arm が artifact
を作れることを確認します。

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-smoke \
  --starting-commit "$STARTING_COMMIT" \
  --limit-tasks 1 \
  --timeout 600 \
  --max-budget-usd 2 \
  --mission-max-iter 1
```

次の条件を両 arm が満たす場合だけ full run に進みます。

- `run_status=completed`
- `completion=true`
- `blocked_reason` がない

どちらかの arm が `run_status=blocked` なら停止し、report には blocked として更新します。
blocked record を performance claim に変換してはいけません。

## Step 2: Full Paired Pilot

smoke gate が pass した後だけ、10 complex tasks を実行します。

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-complex \
  --starting-commit "$STARTING_COMMIT" \
  --limit-tasks 10 \
  --timeout 1800 \
  --max-budget-usd 2 \
  --mission-max-iter 2
```

Expected records: 20。

## Step 3: Validation

```bash
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
```

## Step 4: Report Update

以下の両方を更新します。

- `benchmarks/mission-vs-goal/report.md`
- `benchmarks/mission-vs-goal/report.ja.md`

report に必ず含めること:

- run id、starting commit、task count、scoring method。
- `completed`、`failed`、`blocked` records の分離。
- blocked records を capability conclusion から除外すること。
- blind human review を実際に行っていない限り、
  `quality_score_method=automated_heuristic_not_blind_human` と明記すること。

言ってよい claim shape:

> controlled Claude Code 10-task paired run では、automated heuristic scoring において
> 公式 `/goal` は X、`/mission` は Y だった。

言ってはいけない:

> `mission` は公式 `/goal` より賢い。

> 片方の arm が API usage limit で blocked されたため、`mission` が勝った / 負けた。
