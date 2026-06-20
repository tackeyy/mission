# 変更履歴

**日本語** | [English](CHANGELOG.md)

本プロジェクトの主要な変更を記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [1.0.2] - 2026-06-20

### 追加
- mission audit が nested worktree archive session を検出し、missing scoring evidence と specialist invocation gap を可視化するようになりました。
- slow session report に phase duration の観測可否 breakdown を分離して追加しました。

### 修正
- 同一 logical mission run について、stale halt copy より完了済み pass/done record を優先して dedupe するようにしました。

## [1.0.1] - 2026-06-17

### 追加
- **Q11 – stagnation 自動カウント**: `push-score` で composite の改善幅 (`cur − prev`) が `[0, 0.1)` の場合に `stagnation_count` を自動インクリメント。後退（スコア低下）と初回 push は停滞と見なさず 0 にリセット。
- **S3 – 重複 issue-ref 警告**: `init` に `--issue-ref <ref>` オプションを追加。同プロジェクト内の active session に同一 `issue_ref` が存在する場合は stderr に `WARNING [S3]` を表示（reject しない）。同一 `session_id` での resume は自己検出として除外。

### 修正
- **Q11 後退ロジック修正**: 負の delta（スコア後退）が誤って stagnation として計上されていたバグを修正。条件を `0 <= delta < 0.1` に限定し、`_is_valid_composite()` による型チェックも追加。
- コピー配布用の Codex marketplace wrapper（`plugins/mission/`）を正典の `skills/` / `scripts/` と同期し、最新の stale auto-halt、High gate、stats、scoring rubric 修正を含めました。
- Codex wrapper が正典実装から drift した場合に失敗する回帰テストを追加しました。

## [1.0.0] - 2026-06-15

初の公開リリース。

### 追加
- ミッション・オーケストレーター skill と 5 つの補助 skill（planner / executor / reviewer / critic / scorer）。
- `.mission-state` セッション状態 CLI（`mission-state.py`）。Claude Code / Codex のマルチセッション分離に対応。
- スコア履歴とレビュー/critic ループを伴う閾値ゲート付き完了判定。
- ミッション実行中の早期終了を防ぐ Stop hook。stale-state のタイムスタンプ解釈は macOS（BSD `date`）と Linux（GNU `date`）の両対応。
- Claude Code プラグインメタデータとローカルプラグインマーケットプレイス manifest。
- Codex プラグインパッケージ（`plugins/mission/`）と skill symlink ガイド（Stop hook は opt-in）。
- 状態ルーティング・スコアゲート・hook 挙動をカバーする Python テストスイート。
- GitHub Actions CI（`push` / `pull_request` / `workflow_dispatch`）。pytest と ShellCheck を実行。

[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
