# Design: learning briefのmain root自動fallback（Issue #460）

Issue 本文をそのまま設計とする:

Refs #457（learning brief の初回実戦で発見・2026-08-13）

# 問題

`learning brief` は「同一 state root」の failure_ledger を集計するが、**worktree 実行 mission（#454 で規律化した推奨形）では state root が worktree 内の新規 `.mission-state` になるため、過去 mission の ledger が見えず brief が常に空**になる。実測: main root では 18 rule を返す状態で、worktree init 直後の同コマンドは 0 件。

学習の蓄積は main checkout root（archive-worktree の destination）に集まるため、worktree run こそ brief を最も必要とするのに届かない。

# 提案（codex 委譲可能な粒度）

1. `learning brief` に `--root <path>` オプションを追加（read-only のため任意 root 指定を許可。指定時はそのroot の sessions/ + archive を集計）
2. git worktree 内での実行時、`--root` 未指定なら **git common directory から main checkout root を自動解決**して fallback 集計する（`git rev-parse --git-common-dir` の親。解決不能なら従来どおり cwd root・fail-safe）
3. SKILL.md の注入規律の1文を「worktree 実行時は main checkout root の brief を用いる（自動 fallback）」へ更新
4. テスト: --root 明示 / worktree からの自動解決 / 非 git ディレクトリでの従来動作

# 受け入れ条件

- [ ] worktree 内の実行で main root の ledger が集計される（自動 fallback）
- [ ] read-only 性・gate 非影響は不変
- [ ] plugins ミラー同期・既存テスト全緑


## 補足実装メモ
- root 解決順: --root 明示 > worktree 自動解決（git rev-parse --git-common-dir の親ディレクトリ。common-dir が .git そのものの場合=非worktree は cwd root 継続） > cwd root
- 自動解決は subprocess の git 呼び出し失敗・非 git ディレクトリで従来 cwd root に fail-safe
- cmd_learning_brief のみの変更に留め、他コマンドの root 解決は触らない
- conftest run_cli fixture でテスト（worktree fixture は git init + git worktree add で構築）・plugins ミラー同期
