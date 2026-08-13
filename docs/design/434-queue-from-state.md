# Design: queue enqueue --from-state（Issue #434）

Issue 本文をそのまま設計とする:

Related #420（merge queue: #424 / PR #431 の後続）

# 目的

`queue enqueue` の `--head-sha/--base-sha` は現状オーケストレーターの自己申告で、レビュー accepted の revision_scope（state の score_history 内 base/head sha）との突合がない。転記ミスで誤った sha が enqueue されると verify が意味を失う。

# スコープ（codex 委譲可能な粒度）

1. `queue enqueue` に `--from-state` オプションを追加: 呼び出し session の state から最新 score_history エントリの `revision_scope.base_sha/head_sha` を読み取り、`--head-sha/--base-sha` を自動導出する。明示指定と併用された場合、値が食い違えば exit 2（転記ミス検出）
2. score_history が空 / revision_scope 欠落時は明確なエラー（HINT 付き: 手動指定 fallback を案内）
3. テスト: 自動導出、矛盾検出 exit 2、欠落時エラー、手動指定のみの後方互換

# 受け入れ条件

- [ ] `--from-state` で申告レス enqueue ができ、矛盾は exit 2
- [ ] 後方互換（手動指定のみは従来どおり）
- [ ] plugins ミラー同期・既存テスト全緑


## 補足実装メモ
- base branch は #433 の上にスタック。merge_queue.py（#424）の _locked_queue_update / BaseMismatchError パターンに合わせる
- state の読み取りは呼び出し session の resolve_state_file 経由（read-only）。score_history 最新エントリの score_provenance / review_evidence_ref 内 revision_scope（base_sha/head_sha）を参照する（構造は state 内の既存 revision_scope を実コードで確認して特定すること）
- エラーは #423 の HINT 慣習（ERROR + HINT 行）に合わせる
- conftest run_cli fixture 使用・plugins ミラー同期・既存テスト全緑
