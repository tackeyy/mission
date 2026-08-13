# Design: レーン別 duration 計測と完了時間 SLO bench（Issue #425）

## 目的

「複雑 Issue を 10〜15 分で完了」の SLO を、rollout ログの手掘りなしに state だけで検証・継続監視できるようにする。レーン（session_role: implementer / checker / planning / analyze / release）ごとの壁時間 vs 実働時間と、サブエージェント待ち（ランデブー損失）を機械算出する。

実測背景（2026-08-12/13、ベースラインとして記録する）:

- Claude Code 経路: full-tier TDD 実装→merge 37 分
- Codex 経路: planning フェーズのみで 1021 秒
- checker: 壁時間 11.3 分 vs 実 active 152 秒（ランデブー損失 約9分）

## スコープ

やること:

1. **`skills/mission/lib/activity_segments.py`**:
   - `ACTIVITY_REASONS_BY_KIND` に kind `subagent-wait` を追加。reason enum: `{checker-evidence, planner-response, implementation-provider, other}`（checker/planner 待ちを既存 `reviewer-wait` と区別する）
   - `summarize_activity_states(states)` にレーン別集計を追加: state の `session_role` をキーに、role ごとの `wall_clock_sec`（`started_at`→`updated_at`）、`observed_active_sec`、`wait_totals_sec`（kind 別）を返す新キー `role_summaries` を追加（既存キーは変更しない）
2. **`mission-state.py` に read-only サブコマンド `lane-report` を追加**（lease 不要・state mutation なし）:
   - `mission-state.py lane-report [--json] [--slo-minutes N]`
   - `.mission-state/sessions/*.json`（archive は対象外）を走査し、session ごとに `{session_id, session_role, phase, wall_clock_sec, observed_active_sec, wait_totals_sec, unobserved_gap_sec}` を出力
   - `rendezvous_loss_sec`: 各 session の `subagent-wait` + `reviewer-wait` 合計から、同期間に稼働した従属 session（role != implementer）の `observed_active_sec` を引いた値の下限 0 クリップ。従属 session の対応付けは同一 state root 内の role で近似（v1 では厳密な親子リンクは不要と明記）
   - `--slo-minutes N` 指定時: terminal（done/halted）な implementer session について `wall_clock_sec > N*60` なら `slo_breached: true` を付ける。**観測のみで gate 意味論・exit code に影響しない**（常に exit 0、state 不在時のみ exit 1）
3. **ベースライン記録**: `benchmarks/mission-vs-goal/results/2026-08-13-lane-slo-baseline.json` を新規作成。schema `mission-lane-slo-baseline/1` で上記実測値（cc_full_tier_wall_sec: 2220, codex_planning_sec: 1021, checker_wall_sec: 678, checker_active_sec: 152, rendezvous_loss_sec: 526）と出典（実運用ログ観測、2026-08-12/13）を記録
4. **テスト** `skills/mission/tests/test_issue425_lane_slo.py`:
   - state fixture ベースの決定的テスト（実エージェント起動なし）
   - 代表3シナリオ: (a) implementer 単独 terminal、(b) implementer + checker 並行（rendezvous_loss 算出）、(c) SLO 超過検知
5. **plugins ミラー同期**: `activity_segments.py` は SYNC_PAIRS 登録済み。`mission-state.py` も登録済み。両ミラーを cp で同期

やらないこと:

- gate / pass 判定・exit code 規約の変更（lane-report は観測専用）
- schema_version の bump（`role_summaries` は summarize 出力のみで state 保存フィールドを増やさない）
- 実エージェントを起動する e2e bench（`run_paired_pilot.py` の拡張は別 Issue）
- 厳密な親子 session リンク機構（root_run_id 連携の強化は将来 Issue）

## 受け入れ条件（検証可能形式）

1. `activity start --kind subagent-wait --reason checker-evidence` が CLI から通り、rollup の `wait_reason_totals_sec` に集計される
2. 未知 reason（`subagent-wait:bogus`）は既存の enum バリデーションで exit 2
3. `lane-report --json` が role 別 summary と `rendezvous_loss_sec` を返す（fixture で数値検証）
4. `--slo-minutes 15` で 15 分超の terminal implementer に `slo_breached: true`、以内なら `false`
5. ベースライン JSON が存在し、テストが読み取って schema 検証する
6. 既存テスト全緑（activity 系の既存 enum / rollup テストを壊さない）
7. plugins ミラー同期（test_plugins_in_sync.py green）

## テストリスト（test_issue425_lane_slo.py）

- test_subagent_wait_kind_accepted_and_rolled_up
- test_subagent_wait_unknown_reason_rejected
- test_lane_report_groups_by_session_role
- test_lane_report_rendezvous_loss_computed
- test_lane_report_slo_breach_detection
- test_lane_report_slo_within_budget
- test_lane_report_without_state_dir_exits_1
- test_baseline_json_schema_valid

## 実装メモ

- `ACTIVITY_REASONS_BY_KIND` は lib/activity_segments.py 冒頭（現行 19-33 行付近）。`summarize_activity_states` は同 731 行付近。行番号はズレる可能性があるため実コードで確認すること
- `lane-report` は `cmd_list`（全 state 走査の既存実装）を参考に read-only 実装とし、lease 取得・renew を行わない
- conftest の `run_cli` fixture・`state_dir` fixture を使う
- ドキュメント・fixture に実 home パス・個人名を含めない（test_artifact_hygiene.py が全 tracked file を走査）
- ベースライン JSON の数値は本設計書記載の値をそのまま使う（出典注記付き）
