# Discriminating cohort N≥10 採用判定 runbook

目的: openworld-v1 で確認した品質天井 (marker 1.0 / 分散 0) を解消した
`tasks.discriminating.json` cohort で、「品質>goal・速度≈goal」の採用判定を行う。
判定契約は #236 の「較正 N=3 → 採用判定は対象 cohort N≥10 の paired benchmark」に従う。

## 前提

- #261 (mission-loop 遵守ガード) がマージ済みであること。無効 record は
  `failure_kind=mission_loop_not_initialized` として comparable 集計から自動除外される
- Pro プラン利用上限が回復していること (較正 run は名目 $22 消費。本 run は下記見積)
- model 固定は PATH shim で `--model claude-sonnet-5` を注入し、artifact の
  `modelUsage` で確認する (ANTHROPIC_MODEL 環境変数は効かない)

## Step 1: 較正 smoke (1 task, fail-first 検証)

fail-first 設計が実際に iteration >= 2 を発生させるかを、本 run 前に 1 task で検証する。

```bash
cd benchmarks/mission-vs-goal
PATH="<shim-dir>:$PATH" python3 run_claude_goal_vs_mission.py \
  --starting-commit <latest-main> \
  --tasks-file benchmarks/mission-vs-goal/tasks.discriminating.json \
  --run-id <date>-discriminating-smoke \
  --model-id claude-sonnet-5 \
  --task-ids disc-config-sprawl \
  --max-budget-usd 10 --mission-budget-minutes 30 --timeout 2400
```

smoke gate (すべて満たしたら Step 2 へ):
- mission record の `mission_iterations >= 2` (fail-first が機能)
- mission state に `critic_has_new_scope` が記録されている (#258 配線の実運用初観測)
- 両 arm とも `failure_kind != mission_loop_not_initialized`
- marker score が 1.0 未満の record が存在する (天井飽和の解消)

smoke で iter1 素通しした場合は fail-first タスクの難度を上げてから再 smoke する
(本 run に進まない)。

## Step 2: N≥10 本 run

5 tasks x 2 arms x `--repeats 1` = 10 records (N=10)。分散を厚くする場合は
`--repeats 2` で 20 records。

```bash
PATH="<shim-dir>:$PATH" python3 run_claude_goal_vs_mission.py \
  --starting-commit <latest-main> \
  --tasks-file benchmarks/mission-vs-goal/tasks.discriminating.json \
  --run-id <date>-discriminating-v1 \
  --model-id claude-sonnet-5 \
  --limit-tasks 5 --repeats 1 \
  --max-budget-usd 10 --mission-budget-minutes 30 --timeout 2400
```

見積: 較正実測 (goal $1.3-5.0 / mission $5.1-7.4 名目) から、repeats 1 で
名目 $35-60、壁時計 2-3 時間。repeats 2 はその 2 倍。

## Step 3: 採用判定ゲート

すべて summary / records から機械的に判定する:

1. **測定妥当性**: `mission_loop_not_initialized` record が 0、または除外後の
   comparable N >= 10
2. **判別力**: 両 arm の `marker_score_variance` が 0 でない (天井飽和の解消)
3. **iter>=2 の実運用観測**: mission records に `mission_iterations >= 2` が 1 件以上、
   その record の state で `critic_has_new_scope` 記録を確認 (#240/#241 発火証跡)
4. **品質判定**: `comparable_average_quality_score` と marker recall の arm 差で
   「品質>goal」を判定。同点なら「mission の価値はテール保険+ガバナンス」の
   現行結論を維持する
5. **速度判定**: `comparable_average_elapsed_minutes` の arm 比で「速度≈goal」
   (目安 1.5x 以内) を判定

## 結果の記録

- results JSONL + summary + artifacts を commit し、report.ja.md / report.md に
  セクション追記 (openworld-v1 節の形式に従い「危険な解釈」ガードを必ず付ける)
- 判定結果を Issue #262 に記録してクローズ

---

## 修正履歴
| 日時 | 内容 |
|------|------|
| 2026-07-22 | 初版作成 (#262) |
