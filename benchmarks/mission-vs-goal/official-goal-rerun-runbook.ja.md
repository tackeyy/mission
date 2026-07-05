# 公式 `/goal` vs `/mission` 再実行 Runbook

Status: Claude API limit 引き上げ後の 2026-06-28 JST に実行済み。

2026-06-28 JST の smoke run は blocked であり、比較 evidence ではありません。
`/mission` arm の raw Claude result は workspace API usage limit を報告しており、
access は 2026-07-01 00:00 UTC、つまり 2026-07-01 09:00 JST に戻ると記録されています。

その後、API-limit rerun を実行しました。

- `2026-06-28-claude-goal-vs-mission-smoke-v3`: 1 comparable task を両 arm で完了し、
  両 arm とも completion と validator に pass。
- `2026-06-28-claude-goal-vs-mission-complex-v1`: 20 records は保存されたが、
  全 record が `run_status=blocked`、`blocked_reason=api_usage_limit`。
- `2026-06-28-claude-goal-vs-mission-incremental-v1`: `--task-ids` で未測定 task
  2 件を選び、USD 3.00 per-invocation cap で実行。公式 `/goal` は両 task を完了し、
  `/mission` は両 task で `blocked_reason=max_budget_usd`。
- `2026-06-28-claude-goal-vs-mission-light-v1`: `--mission-profile light`、
  `--mission-max-iter 1`、USD 5.00 cap で未測定 task 1 件を実行。両 arm とも完了し、
  validator に pass。この 1 件では `/mission` light が速く、cost も低かった。
- `2026-07-03-claude-goal-vs-mission-quality-light-v1`: `--mission-profile light`、
  `--mission-max-iter 1`、USD 4.00 cap で quality-critical task 3 件を実行。
  両 arm とも 3 task すべてを完了し、validator に pass。automated quality-marker
  score は同点で、この run では `/mission` light が遅く、cost も高かった。
- `2026-06-28-claude-goal-vs-mission-quality-v1`: `--mission-profile quality`、
  `--mission-max-iter 2`、USD 6.00 cap で fresh quality-critical task 1 件を実行。
  公式 `/goal` が success 前に `api_usage_limit` に到達したため、`/mission` は未実行。

したがって、完了した evidence は normal smoke 1 件、complex light-profile 1 件、
quality-cohort light-profile 3 件です。10 task full performance claim はまだ
supported ではありません。incremental run は operational cost/runtime の注意材料です。
quality-profile run は blocked であり、completed quality comparison ではありません。

## Objective

以下を clean paired comparison として実行します。

- `claude_code_goal_command`: Claude Code 公式 built-in `/goal` command。
- `mission`: `/mission` plugin workflow。

以前の local `goal_only` baseline とは混ぜません。

## Preconditions

1. full run を再実行する前に、Claude Code workspace API limit が解消済みであることを確認する。
   1 task smoke が pass しても、full run で limit に到達する場合がある。
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

今回の rerun result:

- `2026-06-28-claude-goal-vs-mission-smoke-v3` はこの smoke gate を満たした。

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

今回の rerun result:

- `2026-06-28-claude-goal-vs-mission-complex-v1` は 20 records を保存したが、
  全 20 records が `api_usage_limit` で blocked。comparable completion /
  validator rate の denominator は 0。

## Step 2b: Cost-Controlled Incremental Pilot

full run が高コストまたは limit に当たる場合は、完了済み task を再実行せず、
selected tasks だけで続けます。

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-incremental \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids complex-failing-test-triage,complex-review-thread-resolution \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 3.0 \
  --mission-max-iter 2
```

今回の incremental result:

- `2026-06-28-claude-goal-vs-mission-incremental-v1` は 4 records を保存。
- 公式 `/goal`: completed comparable records 2、validator pass 2。
- `/mission`: USD 3.00 cap 下で `max_budget_usd` blocked records 2。
- recorded cost 合計: USD 9.39057695。

## Step 2c: Lightweight Mission Profile Pilot

full `/mission` profile が paired evaluation には高コストすぎる場合は、fresh selected
tasks を light profile で実行します。

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-light \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids complex-failing-test-triage \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 5.0 \
  --mission-max-iter 1 \
  --mission-profile light
```

今回の light-profile result:

- `2026-06-28-claude-goal-vs-mission-light-v1` は completed comparable records 2 件を保存。
- 公式 `/goal`: validator pass、9.56 分、USD 3.00670750。
- `/mission` light: validator pass、5.27 分、USD 2.00569500。
- これは 1 task の light-profile hypothesis だけを支持します。広い cost/runtime claim に
  使う前に fresh task 3-5 件で再実行します。

追加の 2026-07-03 light-profile result:

- `2026-07-03-claude-goal-vs-mission-quality-light-v1` は `tasks.quality.json`
  の selected tasks 3 件で completed comparable records 6 件を保存。
- 公式 `/goal`: validator pass 3 / 3、平均 2.48 分、合計 USD 3.11103025。
- `/mission` light: validator pass 3 / 3、平均 3.14 分、合計 USD 4.40842725。
- automated quality-marker score は両 arm とも 1.00 で同点。この結果は
  `/mission` light が常に高品質という claim は支えません。この run では
  `/mission` light が遅く、cost も高かった。

## Step 2d: Quality-Focused Critical Pilot

`/mission` の低コストではなく高品質を検証したい場合は、`tasks.quality.json` と
quality profile を使います。

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.quality.json \
  --run-id 2026-07-01-claude-goal-vs-mission-quality \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids quality-critical-release-governance \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --mission-max-iter 2 \
  --mission-profile quality
```

今回の quality-profile result:

- `2026-06-28-claude-goal-vs-mission-quality-v1` は expected records 2 件中 1 件だけを保存。
- 公式 `/goal`: `blocked_reason=api_usage_limit`、2.53 分、stop 前 cost USD 1.01481150。
- `/mission`: `/goal` block 後に `--stop-on-blocked` で停止したため 0 records。
- これは quality result ではありません。Claude Code workspace API limit の解消確認後に再実行します。

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
