# Issue1014内部証拠再読取の検証

対象: [Issue1014](https://github.com/tackeyy/mission/issues/1014)。
契約: request/input/source/launch/terminal/output/mappingの安定bytesと参照を照合し、既存の純粋validatorでorigin・scope・mapping・aliasを再検査する。内部carrierは一時的な入力であり、新しい解決権威ではない。writer・公開command・正のcompletionは追加しない。

## テストリスト

- [x] 正常artifact集合をimmutable carrierへ再読取する。非空E3は未解決を維持する。
- [x] 全read-setの途中交換、欠落、改変、部分JSON、不正参照、上限超過を型付き失敗にする。
- [x] 元D origin/membership、operation/fence、候補、scope/reproを既存validator経由で拒否する。
- [x] overflow全indexとduplicate targetの認証・alias閉包を検査する。
- [x] 最新pending/blocked/deferredから古terminalへfallbackしない。旧欠落/空履歴を保持する。

## 検出価値と実測

既存`test_issue907_disposition.py`は純粋carrierを使い56件成功（3.50秒）。本変更は実filesystemの再読取境界とread-set同一性を追加して検査する。同種のsemantic入力表は既存pure validatorに残す。CI全体の時間差・費用差は未測定。

## 実装と検証結果

内部入口は`mission_application.disposition_evidence.observe_disposition_results(document, *, root, candidates)`。純粋な再検証入口は`mission_kernel.finding_disposition.validate_disposition_evidence(document, evidence, candidates)`で、解決集合を返さない。既存terminal/mapping/origin/alias validatorを再利用する。

- Red→Green: 未実装入口のImportError、読取途中のrequest交換2ケースの未拒否、null等の履歴4ケースの未拒否、launch入力上限の未拒否を確認して修正した。
- 実filesystem拒否、1MiB境界、overflow全index、認証済み2edgeのcycle、最新pending/blocked/deferredのfallback禁止、旧履歴互換を67ケースで固定した。
- 既存純粋validator56ケースとwriter/spawn inventoryを合わせ701件成功（12.27秒）。writer inventoryに追加sinkはない。spawn inventoryは保存launchデータの参照だけを明示した。
- strict readerは読取ごとにFDを閉じるため、全read-setを再読取してdirectory/file identityとbytesを比較する。比較後のfilesystem変更まで防ぐatomic snapshotではない。carrierの消費側で候補・fence・証拠を再検査する契約を維持する。

独立探索・fresh session review・Checker・required CI・mergeは未実施で、依頼元が同headで実施する。旧E2targetの有効receiptによる解決集合や全候補mutation配線は後続保存単位の対象。本段階は有効proofがあっても非空E3履歴を未解決のまま保持する。

## レビュー指摘に対するkind境界の修正

supporting evidenceのkindを期待値へそのまま渡すと自己比較になり、一般identifierでない型・空文字・過長値を受理した。E3共有`validate_source_ref`で既存`_identifier`を適用し、pure outputとreaderのsource参照を同じ境界へ接続した。request sourceの既知2種choiceは保持し、D共通`_reference`全体は変更していない。検索範囲はcanonical/mirrorのproduction lib、検索語は`_reference(`・`source=True`・`['kind']`。

不正kind8件の純粋入口とreader代表1件が未拒否のRed、任意の有効identifier5件はGreenだった。修正後、reader81件と既存pure validator56件が成功（計137件、1.78秒）。全kind表のreader側への複製を避け、共有検証の入力表とfilesystem配線の代表に分けた。追加は同じ証拠参照境界の回帰であり、writerや解決権威を増やしていない。

kind修正後のwriter/spawn inventory・mirror/hygiene・module inventoryテスト・CI shard検査は603件成功（33.97秒）。全moduleのmirror一致と、変更した2moduleのPython3.9構文・canonical/mirror importも成功した。追加のkind独立探索と最終レビューは依頼元の固定snapshot検証を待つ。
