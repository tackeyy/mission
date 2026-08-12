# Issue #375 implementation

## Scope

`mission-review/1` の既存スコア契約を維持し、任意の `mission-review-learning/1` metadata、再生成可能な session-local failure ledger、critic/stats/audit の限定consumerだけを追加する。

## TDD evidence

- Red: `test_review_learning.py` は `review_learning` module不在でcollection error。
- Green: strict schema/reducer `6 passed`。

## Non-goals

- score軸、pass gate、stagnation semanticsの変更
- session横断DB、fuzzy matching、legacyからの推測
- raw cause/evidenceのledger複製
