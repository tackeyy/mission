# Design: docs/results-only PRのガード限定fast path + merge運用自動化（Issue #448 + #447縮小分）

Issue #448 本文:
Related #420（CI 実測分析 2026-08-13: bench 結果のみの PR #443 でもフルスイート 771 秒）

# 目的

docs / benchmarks results / refs のみの変更でもフル pytest（12〜15分）が走る。ci.yml には「Detect changed file scopes」ステップが既にあるため、これを活用して docs/results-only PR は必須ガードテストのみの fast path にする。

# スコープ（codex 委譲可能な粒度）

1. changed-scopes 判定を拡張: 変更が `docs/**`・`benchmarks/mission-vs-goal/results/**`・`benchmarks/mission-vs-goal/artifacts/**`・`*.md` のみの場合、pytest を「全 tracked file を走査するガード群（test_artifact_hygiene / test_vendor_fingerprint / test_plugins_in_sync / test_actions_cost_guard / test_doc_consistency）」に限定する
2. コード（`skills/**`・`scripts/**`・`benchmarks/**/*.py`・ci.yml 自体）が1つでも含まれる場合は従来どおりフルスイート（fail-safe 側に倒す）
3. merge_group イベントは常にフルスイート（最終ゲートの完全性維持）
4. cost guard の固定文字列を同時更新

# 受け入れ条件

- [ ] docs/results-only PR の CI が 3 分以内で完了し、ガード群は全て実行される
- [ ] コード変更を含む PR と merge_group は従来どおりフルスイート
- [ ] 判定ロジック自体のテスト（PR ファイル一覧の fixture で分岐検証）


## 追加スコープ（#447 の縮小分・docs のみ）
- refs/state-management.md の Phase 7 節に追記: 単独 mission の merge は 'gh pr merge --auto --squash' を既定とし、base 移動時は 'gh api repos/{owner}/{repo}/pulls/{n}/update-branch' で機械的に base 統合して auto-merge の発火を待つ。Merge Queue は個人リポジトリでは利用不可（2026-08-13 実測）。mission 内 queue（#424）の verify→refreeze 規律は従来どおり

## 補足実装メモ
- base branch は #446（xdist）にスタックしている。ci.yml の既存 'Detect changed file scopes' ステップの実装を読み、その仕組みに乗せて拡張する
- fast path 判定は fail-safe: 判定不能・API失敗時はフルスイートへ倒す
- ガード群の実行は pytest の明示ファイル列挙で行い、-n auto は維持
- merge_group イベント時は常にフル（ci.yml の分岐で保証）
- test_actions_cost_guard.py の固定文字列を同時更新
- 判定ロジックのテスト: github-script 部分は純関数化できる範囲を YAML 内に閉じず、可能なら判定関数を scripts/ に切り出してユニットテスト可能にする（過剰なら ci.yml 内で完結し、cost guard で文字列固定のみでも可 — 実装時に判断し最終メッセージで根拠を書く）
