# Design: pregate verdictがaccepted以外の場合のinit/next警告（Issue #433）

Issue 本文をそのまま設計とする:

Related #420（Pre-Gate 基盤: #421 / PR #430 の後続）

# 目的

`init --issue-ref` 時に pregate record の verdict が `split-required` / `rejected` の場合、現状は参照記録のみで警告がない。エージェントが気づかず planning を進め、後で split 対応の mission 作り直しが発生する（実測: 再 init 儀式で数分の損失）。

# スコープ（codex 委譲可能な粒度）

1. `init` が pregate 参照を記録する際、verdict が `accepted` 以外なら stderr に WARNING 1〜2 行を出す（例: 「pregate verdict=split-required。planning 前に分割を解決してください」）。**init の exit code・routing・state 遷移は変えない**（観測と警告のみ）
2. `next` の planning guidance にも同趣旨の1行を追加（state の `pregate.verdict` を参照）
3. テスト: verdict 別の警告有無、exit code 不変、pregate 不在時の無警告

# 受け入れ条件

- [ ] split-required / rejected で WARNING、accepted で無警告
- [ ] gate 意味論・exit code・routing 完全不変（既存テスト全緑）
- [ ] plugins ミラー同期


## 補足実装メモ
- base branch は #432（pregate digest）の上にスタックしている。pregate 関連の既存コード（_pregate_state_reference / cmd_pregate）と整合させる
- WARNING は stderr のみ・1回・正常系 stdout を汚さない。#423 の HINT 慣習（HINT:/WARNING: プレフィクス）に合わせる
- next 側は planning phase の guidance 内 1 行のみ（command_sequence 等の機械可読フィールドは変更しない）
- conftest run_cli fixture 使用・plugins ミラー同期・既存テスト全緑
