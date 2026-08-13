# Design: lane-reportのroot_run_id単位グルーピング（Issue #435）

Issue 本文をそのまま設計とする:

Related #420（lane-report: #425 / PR #429 の後続）

# 目的

`lane-report` の rendezvous_loss_sec は「全 implementer の wait 合算 − 非 implementer の実働合算」の state root 全体近似で、複数案件が同居する root では精度が落ちる。state には root_run_id / parent_run_id が既にある。

# スコープ（codex 委譲可能な粒度）

1. lane-report のグルーピングを root_run_id 単位に拡張: 同一 root_run_id（欠落時は従来どおり root 全体を1グループ扱い）で rendezvous_loss を算出し、report を `groups: [{root_run_id, sessions, rendezvous_loss_sec}]` 構造にする（トップレベルの合算値は後方互換で維持）
2. checker 側 state に root_run_id が入っていないケース（手動起動）は従来近似に fallback
3. テスト: 2 グループ同居時の分離算出、後方互換フィールド、fallback

# 受け入れ条件

- [ ] root_run_id 単位の rendezvous_loss が算出される
- [ ] 既存出力フィールドの後方互換（#425 のテストが無修正で通る）
- [ ] plugins ミラー同期


## 補足実装メモ
- base branch は #434 にスタック。lane-report の現行実装（cmd_lane_report / _lane_report_session_entry）と #425 のテストを壊さない（既存テスト無修正で通ることが受け入れ条件）
- rendezvous_loss の集約意味論（implementer wait 合算 − 非 implementer active 合算・下限0）はグループ内で適用する。トップレベル rendezvous_loss_sec は全グループ合算で後方互換維持
- root_run_id 欠落 state は 1 つの共有グループ（root_run_id: null）に fallback
- conftest run_cli fixture 使用・plugins ミラー同期・既存テスト全緑
