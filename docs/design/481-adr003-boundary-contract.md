親 Issue: #473（Wave 2 / 依存順 7 番目の前半・P2）

# 概要

review tier のキーワード判定は境界なしの substring 一致で、`reproduction` が `production` に一致するなど、無害なテキストが Standard から full tier へ誤昇格しうる。ただし**この substring 挙動は ADR-003 が Accepted として明文化している契約**なので、実装だけ直すと ADR と乖離する。

本 Issue は**契約側の改訂と評価用コーパスの整備まで**を行い、実装変更は行わない。実装は後続 Issue（Wave 2-7b）で行う。

# 一次証拠（現在の main）

## 現在の実装

`skills/mission/bin/mission-state.py` L2066–L2069:

```python
def _review_keyword_matches(text: str, keyword: str, *, ignore_case: bool) -> list[re.Match]:
    flags = re.IGNORECASE if ignore_case else 0
    return list(re.finditer(re.escape(keyword), text, flags))
```

word boundary を一切使っていない。

## キーワード一覧（計 29 語）

- L1884–L1887 `_IRREVERSIBLE_KEYWORDS_EN`（7 語）: `deploy` / `release` / `migration` / `drop` / `delete` / `publish` / `production`
- L1890 `_IRREVERSIBLE_KEYWORDS_JA`（8 語）
- L1901–L1906 `_SECURITY_KEYWORDS_EN`（11 語）
- L1908 `_SECURITY_KEYWORDS_JA`（3 語）: `認証` / `秘密` / `鍵`

別途 L476–L487 に `HIGH_RISK_KEYWORDS`（11 語、task_profile の risk 分類用）がある。

## 誤一致しうる実例

`production` ← `reproduction` / `deploy` ← `deployment diagram`, `redeployment` / `migration` ← `migration guide` / `delete` ← `deleteHandler`, `soft-delete` / `drop` ← `dropdown`, `drop shadow` / `secret` ← `secretariat` / `鍵` ← `鍵盤`, `解決の鍵`

## ADR-003 の記述

`docs/adr/003-adaptive-review-gating.md`（Status: **Accepted**、L5）

- L63–L66: キーワード群を「**case-insensitive substring match in mission text**」と定義
- L68–L69: 「irreversible keyword の全出現を文脈評価する」前提

## 既存の抑制ロジックとの関係

`_REVIEW_TECHNICAL_NOUN_SUFFIX_RE`（L1893–L1896）は `公開` に対する技術名詞複合語の後置抑制のみで、境界問題の汎用解ではない。否定抑制（`_REVIEW_DOUBLE_NEGATION_RE` L1920 等）も「一致した後で文脈評価する」後処理であり、マッチ対象を絞る設計ではない。

# 変更内容

**コード変更は行わない。** 以下のドキュメントとデータのみ。

1. `docs/adr/003-adaptive-review-gating.md` を改訂する
   - substring 契約を lexical boundary ありの契約へ変更する旨と、その理由
   - 言語別の扱い: 英語は word boundary が定義できるが、**日本語は空白区切りが無いため `\b` が機能しない**。日本語キーワードの扱いを別途定義すること（現行の substring を維持するのか、別の抑制手段を取るのか）
   - 語形 family（`deploy` / `deploys` / `deployed` / `deployment`）のうち、どれを一致とみなすかを明記する
   - 変更による FP 削減と FN 増加のトレードオフ、および「安全側 = full tier へ倒す」原則との整合
   - Status の扱い（改訂版を Accepted にするか、新 ADR で Supersede するか）
2. FP / FN 評価コーパスを作る
   - 正例（full tier へ昇格すべきテキスト）と負例（昇格すべきでないテキスト）を、上記の誤一致実例と既存テストのケースから構成する
   - 英語・日本語の両方を含める
   - 保存場所はテストから読める形（fixture ファイル等）にし、後続 Issue の非退行検証に使う

## やらないこと

- `_review_keyword_matches` およびキーワード定数の変更（後続 Issue の範囲）
- tier 導出の閾値・エスカレーション条件の変更
- 既存テストの期待値変更

# 受け入れ条件

- [ ] ADR-003 が改訂され、境界の扱いが英語・日本語それぞれについて明記されている
- [ ] 語形 family の判定基準が明記されている
- [ ] FP / FN コーパスが正例・負例つきで用意され、テストから参照できる形式になっている
- [ ] コーパスに、本文記載の誤一致実例（`reproduction` 等）が負例として含まれている
- [ ] コード変更が無いこと（`git diff` が docs とコーパスのみ）

# 参考にする既存テスト

コーパスの構成時に、既存の網羅ケースを取り込むこと。

- `skills/mission/tests/test_issue168_review_tier.py`（47 テスト）
- `skills/mission/tests/test_issue209_review_tier_negation.py`（66 テスト）

いずれも `reproduction` のような境界起因の誤昇格を陽性サンプルとして持っていない。
