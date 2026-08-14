# Issue #500 K1 実装・検証記録

## 結論

`docs/design/500-mission-state-aggregate-implementation-design.md` を仕様境界として、
`docs/design/k1-review-findings.md` の High 1-8、Medium 1-2、Low に対応した。
production の state/evidence ファイル、Issue #483 の golden expected object、CLI route は変更していない。

## 指摘別の対応

| 指摘 | 対応 | 主な検証 |
| --- | --- | --- |
| High 1 | 現行 writer の flattened handoff wire shape を `Prepared` / `Consuming` / `Consumed` / `Rejected` の closed variant へ decode | 実 CLI corpus の4 statusを decode |
| High 2 | v5 の top-level、Identity、Control、Plan、Handoff、Review、Score、Lease を required/exact/closed validation | 指摘済み8反例と unknown/missing/type/invariant 反例を rejection test化 |
| High 3 | Open/Resolved Finding を分離し、resolution 3 field、prior identity、timestamp、evidence kindを厳密化 | 各欠落・混在・不整合を rejection、正規形を round-trip |
| High 4 | legacy default、terminal/phase、score provenance、review aggregate、lease、unloaded findings を設計どおり正規化 | Issue #483 literal golden、実 CLI review/score/lease/terminal corpus |
| High 5 | Plan/Handoff/Review/Finding/Score/Lease を frozen closed union化し、nested JSONもfreeze | mutation rejection、手作り invalid model の encode拒否 |
| High 6 | schema version、strict file read、strict JSON pair handlingを既存 CLI wrapperから共通 primitiveへ委譲 | wrapper characterization と既存回帰 |
| High 7 | `O_NOFOLLOW`/`O_NONBLOCK`、single-link regular file、完全 identity tuple、descriptor/path再検証を実装 | FIFO、symlink、hardlink、oversize、short read、append、swap、消失を拒否 |
| High 8 | legacy projectionでtyped-owned fieldを再投影し、unknown fieldはlossless保持 | `dataclasses.replace`後のauthorityとsource bytes不変を検証 |
| Medium 1 | 実 CLI corpus、literal expectation、fresh import、AST/parser route inventory、migration executeを追加 | 非トートロジー assertionとproduction到達不能テスト |
| Medium 2 | error APIを `(code, json_path, detail)` に統一し、raw `KeyError`/`ValueError`を正規化 | `.code` と `.json_path` の反例 assertion |
| Low | 設計にない public decoder aliasを削除し、private decoder名へ変更 | package root/export検査 |

## 実 CLI corpus

各 fixture は pytest の隔離一時ディレクトリで `skills/mission/bin/mission-state.py` を subprocess 実行し、
生成された `.mission-state/sessions/test.json` を読み戻して corpus にする。decoder の都合に合わせた
state JSON の手書きは行わない。provider variant は設計指定の既存 lifecycle fixtureで前提状態を構築した後、
production CLI の import/promotion出力を読み戻す。lease takeover のみ、CLI の実処理を維持したまま `iso_now` を固定して
期限切れを再現する。

網羅した writer variant:

- Plan: core adoption、provider import/promotion
- Handoff: `prepared`、`consuming`、`consumed`、`rejected`
- Review: `review-input` 2 reviewer、`review-aggregate`
- Score: legacy（Issue #483 の既存 tracked corpus）、現行 CLI の `scoring-json` / `manual-import` complete provenance、v5 read-only wire の両 source
- Lease: 初回 acquire、期限切れ takeover、history
- Phase: `planning`、`executing`、`reviewing`、`scoring`、`done`、`halted`
- Terminal outcome: `completed_pass`、`completed_evidence`、`blocked_external`、`awaiting_approval`、`stale_superseded`、`failed`、`incomplete`、`user_aborted`、`routed_elsewhere`

## CLI characterization

薄い wrapper 化の前後で次を固定した。

- `_validate_schema_version`: missing/v1-v4 の戻り値、bool/string/0/5 の exception type と完全一致 message
- JSON pair hook: unique object の戻り値、duplicate key の exception type/message
- strict review file: regular file bytes、missing、hardlink の既存 message
- `review-import`: subprocess exit code 0、empty stderr、stdout の完全key setとevidence ref、および publication sequence
  `publish-evidence -> verify-evidence -> verify-evidence -> publish-state`

semantic な `mission-review/1` validation は既存の `_validate_review_payload` 呼び出し位置に残した。

## 検証結果

- K1 新規テスト: 153 passed
- 関連回帰（score/manual/providerを含む）: 581 passed
- artifact hygiene / vendor fingerprint / module inventory: 18 passed
- 実機 Python 3.9: canonical/plugin isolated import成功、Issue #99回帰 37 passed
- canonical/plugin mirror: 対象10ファイル byte-for-byte一致
- full suite: 3407 passed in 108.53s
- independent Checker: ACCEPTED（先行反例の再実行を含む）
- `git diff --check`: pass
- production `.mission-state`: tracked diffなし
- Issue #483 golden test file: tracked diffなし

## 未対応事項

設計上 K1 の非スコープである v5 production writer、migration route、CLI routeは追加していない。
legacy score は現行 CLI が新規生成できないため、設計 §12.1 が指定する Issue #483 の tracked corpusを使用した。
v5 production writerを作ってfixtureを捏造することはしていない。ユーザー指示に従い commit は作成していない。
