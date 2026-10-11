# Findingの棄却・保留を独立判定で記録する設計

状態: 設計再レビュー前のドラフト。実装の受入を示さない。
対象: [Issue907](https://github.com/tackeyy/mission/issues/907)。
上位契約: [修復lineage設計の§3–§5](https://github.com/tackeyy/mission/blob/d5f8fc02cb5e2854337b8984fa6af1e870db657a/docs/design/880-repair-lineage.md#L195-L239)。

## 目的と範囲

E3専用request/terminalを使い、独立判定の理由・証拠・候補bindingを記録する。
修復のpassed replay、通常検証、fresh search、coverageを代替しない。
実hostの保証は[Issue897](https://github.com/tackeyy/mission/issues/897)、eventは[Issue880](https://github.com/tackeyy/mission/issues/880)の後続範囲で扱う。

## 現行ソースとの照合

基点は`d5f8fc02cb5e2854337b8984fa6af1e870db657a`。
依存する[E2bのPR1002](https://github.com/tackeyy/mission/pull/1002)はmerge待ちで、実装はmerge後の最新mainへ統合してから開始する。

- `state_capacity.disposition_reserve`は0を返すstubで、E3予約をまだ保証しない。
- `repair_lineage.origin_identity`はattempt/lifecycle/候補marker以外を不変originとして比較する。disposition履歴を追加する際は原由来と可変判定を分離する。
- findingのlifecycleは修復attempt由来である。dispositionの終端とgenerationを含めた最新状態の導出が必要。
- E2bの全policy候補観測carrierを使い、判定後の候補変更とA→B→Aを失効として保持する。

## 維持する受入契約

- `deferred`は常に未解決。`rejected`が遮断を外すには独立判定が当該義務・禁止副作用の非違反を確認しなければならない。
- 重複棄却はcanonical targetの未解決状態を引き継ぎ、欠落・自己参照・循環を許さない。
- 取込み上限超過のoutputは全要素の対応表をeffectsに保持する。stateの重複先集合は最大61件とし、部分的な対応で解決しない。
- 実行停止を確認できない場合は資源を解放せず、未解決状態を維持する。
- 予約は受付から終端・stateのみのblocked保存までを含む。保存失敗後に成功を作らない。

## 再レビュー前に具体化する決定

request/output/terminalの閉じたshape、stage別容量、repairingからの遷移、adapter能力判定、公開失敗の回復表、completionの導出、検証経路を確定する。
設計再レビューで決定が揃うまで実装しない。
