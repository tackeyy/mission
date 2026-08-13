# Design: pregate digest コマンドと事前充填レシピ（Issue #432）

Issue 本文をそのまま設計とする（起票時に codex 委譲粒度で記述済み）:

Related #420（Pre-Gate 基盤: #421 / PR #430 の後続）

# 目的

Pre-Gate キャッシュ（`pregate record/check`）を Issue 起票時などに事前充填する運用を、ホスト非依存の形で支援する。現状 subject_digest の算出方法が利用者任せで、ホスト側 hook から使いにくい。

# スコープ（codex 委譲可能な粒度）

1. `mission-state.py pregate digest --input <file|-` を追加: 入力（Issue 本文等のゲート対象スナップショット）の正規化 sha256 を `{"subject_digest": "sha256:..."}` で返す read-only コマンド。正規化規則は pregate_cache.py の payload digest と同一
2. `refs/state-management.md` の Pre-Gate 節に「事前充填レシピ」を追記: 起票時 hook / 定期実行から `digest → 外部ゲート評価 → record` を回す手順例（特定ホストのコマンド名は書かない・汎用擬似手順）
3. テスト: digest の決定性（同一入力→同一 digest）、stdin 入力、record→check との整合

# 受け入れ条件

- [ ] `pregate digest` が pregate_cache の subject_digest と一致する値を返す
- [ ] refs にレシピ節（修正履歴込み）
- [ ] plugins ミラー同期・既存テスト全緑


## 補足実装メモ
- digest の正規化規則は lib/pregate_cache.py の payload digest（sort_keys・ensure_ascii=False・separators カンマコロン の canonical JSON sha256）と同一実装を再利用する。入力が JSON でない場合は exit 2
- read-only（lease 不要・state 不要でも動作可: .mission-state 不在でも digest 計算のみは許可）
- conftest の run_cli fixture 使用。plugins ミラー同期 + 既存テスト全緑
