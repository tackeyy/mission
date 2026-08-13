# Design: archive-worktreeのリポジトリ内artifact対応（Issue #449）

Issue 本文をそのまま設計とする:

# 問題

`archive-worktree` は artifact contract の artifact-path が `.mission-state` 外（リポジトリ内のコードファイル等）を指す mission で `required evidence is outside .mission-state` により fail-closed し、実行できない（2026-08-13 の mission run cc-8ca1da98 / Issue #444 で実測。artifact = merge 済みテストファイル）。

コード変更ミッションでは artifact がリポジトリファイルになるのが通常形であり、merge 済みなら git が永続化を担うため、state archive に artifact 実体を要求する必要はない。

# 提案

- artifact-path がリポジトリ管理ファイル（git ls-files に存在 or merge 済み revision_scope 内）の場合、archive-worktree は artifact を「reference（path + digest + head sha）」として manifest に記録し、実体コピーを要求しない
- リポジトリ外・untracked の artifact は従来どおり fail-closed
- テスト: コード artifact mission の archive-worktree 成功経路 / untracked artifact の従来拒否

# 受け入れ条件

- [ ] merge 済みコード artifact を持つ mission の archive-worktree が成功する
- [ ] manifest に artifact reference（digest + sha）が残り、audit で検証可能
- [ ] 既存の fail-closed 経路（untracked / 範囲外）不変


## 補足実装メモ
- 対象ロジックは skills/mission/lib/worktree_archive.py（'required evidence is outside .mission-state' の発生源を特定して修正）
- 「リポジトリ管理ファイル」の判定は 'git -C <root> ls-files --error-unmatch <path>' の exit 0（tracked）で行う。untracked は従来どおり fail-closed
- manifest への reference 記録形式は {kind: repo-artifact, path, digest(state記録のartifact digest), head_sha(git rev-parse HEAD)} とし、実体バイトのコピー・再read はしない（digest は state の artifact contract 値を転記。改ざん検出は audit 側の突合で成立）
- 既存の manifest schema へのフィールド追加は additive のみ。既存 bundle の読み込み互換を壊さない
- テストは既存 test_issue212_worktree_archive*.py / test_archive_*.py の慣習に従う。plugins ミラー同期（worktree_archive.py が SYNC_PAIRS 登録済みか確認し、未登録なら登録）
