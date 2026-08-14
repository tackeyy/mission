親 Issue: #473（Wave 2 / 依存順 7 番目の後半・P2）

**依存: Wave 2-7a（ADR-003 改訂と FP/FN コーパス整備）の完了が前提。先に着手しないこと。**

# 概要

ADR-003 の改訂内容に従って、review tier のキーワード判定に lexical boundary を導入する。無害なテキスト（`reproduction` など）の full tier 誤昇格を減らしつつ、既存の検出（FN 非退行）を壊さない。

# 一次証拠（現在の main）

`skills/mission/bin/mission-state.py` L2066–L2069 `_review_keyword_matches` が `re.finditer(re.escape(keyword), text, flags)` の純 substring 一致。詳細な背景・キーワード一覧・誤一致実例は Wave 2-7a と改訂後の ADR-003 を参照。

# 変更内容

1. `_review_keyword_matches` を、改訂 ADR-003 が定義する境界規則に従って実装し直す
2. **日本語キーワードは `\b` が機能しない**ため、ADR が定めた日本語向けの扱いを実装する。英語規則をそのまま適用しないこと
3. 既存の文脈評価（否定抑制・引用抑制・技術名詞複合語抑制）は一致集合の後処理として動く。境界導入で一致集合が変わるため、**これらの後処理が期待どおり働くことを再検証する**

## やらないこと

- ADR-003 に書かれていない独自の判定規則の追加
- キーワード一覧そのものの増減（ADR 改訂で決まっていない限り）
- tier 導出の閾値・エスカレーション条件の変更
- ゲート意味論の変更（threshold / open_high / agreement / halt は不変）

# 受け入れ条件

- [ ] Wave 2-7a のコーパスの負例が full tier へ昇格しない
- [ ] 同コーパスの正例がすべて従来どおり昇格する（**FN 非退行**）
- [ ] 既存 113 テスト（`test_issue168_review_tier.py` 47 + `test_issue209_review_tier_negation.py` 66）が全緑、または ADR 改訂で意図的に変わる分だけが更新され、変更理由が PR に明記されている
- [ ] 日本語キーワードの扱いが ADR の定義と一致している
- [ ] tier 判定の provenance（`review_tier_source` / `review_tier_signals` / `review_tier_signal_details`）が引き続き監査可能
- [ ] plugins ミラー一致

# テストリスト

1. コーパスの負例が昇格しないことを一括検証する（parametrize。**lambda を値に入れない**）
2. コーパスの正例が昇格することを一括検証する
3. 語形 family（`deploy` / `deploys` / `deployed` / `deployment`）について ADR の定義どおりの結果になる
4. 日本語キーワードが ADR の定義どおりに動く
5. 否定抑制・引用抑制・技術名詞複合語抑制が境界導入後も期待どおり動く（既存テストの観点を維持）
6. `review_tier_signal_details` に記録される provenance が新しい一致規則と整合する

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
