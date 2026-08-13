# Design: bench runnerへのlane-report回収とSLO判定の組み込み（Issue #436）

Issue 本文をそのまま設計とする（起票時に codex 委譲粒度で記述済み）:

Related #420（SLO ベースライン: #425 / PR #429 の後続）

# 目的

10〜15分 SLO の検証が現状 fixture ベースのみで、実運用 mission の before/after 比較が手動。bench runner に lane-report 回収と SLO 判定を組み込み、実測検証を再現可能にする。

# スコープ（codex 委譲可能な粒度）

1. `benchmarks/mission-vs-goal/run_paired_pilot.py` に post-run ステップを追加: 各 arm の run-root の `.mission-state` に対して `lane-report --json --slo-minutes 15` を実行し、結果を artifacts（`lane-report.json`）として保存する
2. summary JSON に `slo`（breached 件数 / wall_clock 中央値）フィールドを追加。`results/2026-08-13-lane-slo-baseline.json` との差分を出力する比較関数を benchmark_audit.py に追加
3. テスト: runner の post-run ステップ（subprocess を fixture でスタブ）、summary スキーマ、baseline 比較

# 受け入れ条件

- [ ] bench 実行後に arm ごとの lane-report が artifacts に残る
- [ ] summary に slo フィールド、baseline 比較が機械可能
- [ ] 実 LLM 呼び出しなしでテスト可能（スタブ）・既存テスト全緑


## 補足実装メモ
- mission-state.py 本体・skills/mission/lib は変更しない（benchmarks/ 配下と対応テストのみ。並行開発中の他Issueとの競合回避）
- lane-report 実行は subprocess 経由で mission-state.py を呼ぶ。テストでは subprocess をスタブ化し実行しない
- summary スキーマ変更は additive のみ（result.schema.json がある場合は整合させる）
- 実 home パス・ベンダー語彙を成果物に残さない（artifact hygiene / vendor fingerprint テストが走査）
