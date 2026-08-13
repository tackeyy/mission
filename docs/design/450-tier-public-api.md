# Design: tier導出「公開」のコード語彙誤発火較正（Issue #450）

Issue 本文をそのまま設計とする:

Related #420（実測: mission run cc-8ca1da98 / Issue #444 で reviewing 675秒の一因）

# 問題

review_tier 自動導出の不可逆キーワード「公開」が、**「公開API」「公開関数」等のコード語彙（public の訳語）に誤発火**し、テストのみの Standard ミッションを full tier（reviewer 3名 + M6）へエスカレーションさせた（2026-08-13 実測: mission 記述「…digestヘルパーの公開API使用を…」に対し `irreversible-keyword:公開` / reason `affirmative-actual-operation` で included）。

「公開する」（release 操作）と「公開API/公開メソッド」（アクセス修飾子の意味）は別物であり、後者は #174 の較正方針（可逆なコード変更への誤発火を除外）に該当する。

# 提案（codex 委譲可能な粒度）

1. tier シグナル評価で、「公開」のマッチ直後が技術名詞（`API` / `Api` / `api` / `関数` / `メソッド` / `クラス` / `インターフェース` / `プロパティ` / `属性` / `型` / `モジュール` / `フィールド`）に連続する場合は **compound-technical-noun** として抑制する（`decision: suppressed` / reason_code を signal_details に記録し監査可能に）
2. 「公開する」「一般公開」「サイト公開」「公開時」等の操作文脈は従来どおり included（安全側維持）。security / high-risk シグナルの抑制規則には触れない
3. 英語側は変更しない（publish に同型問題は未観測）
4. テスト: 「公開API使用」→ standard 維持 / 「本番へ公開する」→ full 維持 / 「公開APIを公開する」→ full 維持（操作語が別途存在）/ signal_details の suppressed 記録
5. `refs/state-management.md` の tier 導出節へ較正内容を追記（#174 の較正履歴に連ねる）

# 受け入れ条件

- [ ] 上記4パターンのテストが通り、既存 tier テスト全緑
- [ ] gate 意味論不変（tier 導出のみの較正）・signal_details で抑制理由が監査可能
- [ ] plugins ミラー同期・refs 追記（修正履歴込み）


## 補足実装メモ
- 対象は mission-state.py の tier シグナル評価（_IRREVERSIBLE_KEYWORDS_JA を使う 2箇所の評価経路。行 2207/2448 付近。両経路に同一規則を適用するため共通ヘルパー化してよい）
- signal_details の既存 decision/reason 語彙（included/suppressed 系）を実コードで確認し、既存の suppressed 表現に合わせる（新語彙は compound-technical-noun のような kebab で追加）
- tier 関連の既存テストファイル（test_review_tier* / tier を grep）を特定し、その慣習でテスト追加
- plugins ミラー同期必須
