親 Issue: #473（Wave 2 / 依存順 9 番目・P2）

# 概要

永続ドキュメントが実装に追随していない。schema の記述が 1 世代古く、実装済みの ADR が Proposed のまま残っている。この状態で Wave 3 の typed migration に入ると、旧新 reader/writer の互換範囲を誤る危険がある。

# 一次証拠（現在の main）

| ドキュメント | 記述 | 実装 |
|---|---|---|
| `skills/mission/refs/state-management.md` L164 | `schema_version` (現在 3) | `SCHEMA_VERSION = 4`（`skills/mission/bin/mission-state.py` L241） |
| 同 L174 / L178 | 「schema v3」「v1/v2 は derive_terminal_outcome() で互換導出」 | v4（structured scoring provenance 必須）の記述が無い |
| `docs/adr/002-typed-mission-state-objects.md` L5 | Status: **Proposed** | typed mission state objects は実装済み |

ADR 一覧と status:

- `docs/adr/001-specialist-auto-selection.md`: Accepted
- `docs/adr/002-typed-mission-state-objects.md`: **Proposed**（要確認）
- `docs/adr/003-adaptive-review-gating.md`: Accepted（Wave 2-7a で改訂予定。**本 Issue では触らない**）
- `docs/adr/004-activity-segment-observability.md`: Accepted

# 変更内容

1. `skills/mission/refs/state-management.md` の schema 記述を v4 に更新する
   - `schema_version` の現在値
   - v4 で何が必須になったか（structured scoring provenance）
   - v1/v2/v3 の互換導出が read-time であり物理 rewrite しないこと（既存記述の維持）
2. `docs/adr/002-typed-mission-state-objects.md` の status を実態に合わせる
   - **まず実装状況を確認すること**。ADR に書かれた内容がすべて実装済みなら Accepted、一部だけなら「どこまでが実装済みか」を追記した上で status を判断する。確認せずに Accepted へ書き換えない
3. architecture 記述が実装と食い違っている箇所を洗い出し、修正するか「この文書は historical な分析であり現況ではない」と明記する
4. 日本語版がある文書（`state-management.md` に対応する ja 版、ADR の ja 版があれば）も同期する

## やらないこと

- ADR-003 の改訂（Wave 2-7a の範囲）
- コード変更（本 Issue はドキュメントのみ）
- 新規 ADR の起票
- schema v5 の設計記述

# 受け入れ条件

- [ ] `state-management.md` の schema 記述が v4 と一致する
- [ ] ADR-002 の status が実装状況の確認結果に基づいて更新され、判断根拠が PR 本文に書かれている
- [ ] 実装と食い違う architecture 記述が解消されるか、historical である旨が明記されている
- [ ] コード変更が無い（`git diff` がドキュメントのみ）
- [ ] `test_doc_consistency.py` を含む既存テスト全緑

# 注意

`skills/mission/tests/test_doc_consistency.py` は README の主要トークンと日付付き検証スナップショットを pin している。ドキュメント更新でこれらに触れる場合は、同一 PR でテスト側も更新すること（過去に更新漏れで CI が落ちた実績あり）。
