# Findingの棄却・保留を独立判定で記録する設計

状態: 実装前の設計決定案。異系統設計レビューacceptedを実装開始条件とする。
対象: [Issue907: Findingの独立判定](https://github.com/tackeyy/mission/issues/907)。
上位契約: [修復lineage設計](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/docs/design/880-repair-lineage.md)の§2〜5・§8〜9。
照合基点: E2bを含むmerged main `3cd23821512711d216e83be7a17ffe44f0fa5751`。

## 1. 目的と境界

E3専用request/terminalで、独立判定の理由・証拠・候補binding付きの保留・棄却を記録する。
修復replay、通常verification、fresh search、coverageは独立した必要条件として維持する。
保証はfixture adapterを通した公開CLIまで。実hostは[Issue897](https://github.com/tackeyy/mission/issues/897)、eventは[Issue880](https://github.com/tackeyy/mission/issues/880)の後続範囲で扱う。
予算policyのactivation、runner接続、credential操作、追加probeは範囲外。

| 判定 | 効果 |
|---|---|
| deferred | 独立判定と理由を保存する。常に未解決 |
| not-violation | 有効な独立証拠がある場合だけrejectedにする |
| duplicate | 有効な対応表を保存し、canonical targetの未解決状態を引き継ぐ |
| accepted-risk、予算不足、severity低下 | 義務違反の棄却理由として認めない |
| 未対応・観測不能・不正output | blockedまたはpendingを維持し、解決を作らない |

## 2. 型・保存・世代

新規schemaは`mission-finding-disposition-request/1`、`mission-finding-disposition-output/1`、`mission-finding-disposition/1`、`mission-finding-disposition-mapping/1`。
未知field・欠落・null・型違反を拒否する閉じた形式とする。
通常findingとoverflow rowへ任意field `dispositions`を別collectionとして追加する。
欠落だけを空履歴として扱い、旧stateを読んだだけで書き換えない。修復attemptやD collectionへ混ぜない。
旧overflowの`disposition=absent("disposition-not-enabled")`は固定fieldとして保持する。

| 保存先 | 内容 |
|---|---|
| state要約 | request ID/ref、受付generation、stage、intent、launch ref、terminal ref/generation、判定種別、重複先ID集合 |
| request artifact | origin、lineage、contract/requirement/policy、元finding/output、候補binding、adapter pin、上限 |
| terminal artifact | request/launch/output、独立性観測、理由、対応表ref/digest、実行・公開operation/fence |
| mapping artifact | 元output全要素の対応 |

本文・snapshot・全件対応表はstateへ埋め込まない。artifactは既存content-addressed refと安定読取・digest/size検査を使う。
ID・digest・int・時刻・refの上限はD/E2を継承する。理由本文はartifactの256KiB上限に含め、stateには閉じたreason codeだけを置く。
`origin_identity`から新しい`dispositions`だけを追加除外し、元finding・repro・義務リンク・導入候補の照合を維持する。
`next_generation`は候補marker・通常receipt・修復terminal・disposition受付/terminalの最大値から増加させる。
同一commitでは候補変更を先、判定terminalを後にする。
修復履歴の検査を維持し、lifecycleは両collectionから共通関数で導出する。
古いrejectedへのfallbackは禁止する。後続判定がpending/blocked/deferredなら現在の解決証拠にはならない。

## 3. 修復との排他と停止証拠

同一lineageで修復pendingとdisposition pendingを同時に保持しない。全過去intentも確認し、未知の旧実行を後続attemptで隠さない。

| 受付時点 | 判定 |
|---|---|
| open/deferred/実効open、未確認の過去実行なし | prepare可能 |
| repairing、intentなし | 同一fenced commitで修復をblocked化しdispositionを受付 |
| repairing、intentあり | 受付拒否。開始しない |
| terminal済みでも過去intentの終了を証明できない | repair-execution-unobservableで拒否 |
| disposition pending | 同一operationの再読取だけを許可 |
| 現候補で有効なverified/rejected | 冗長prepareを拒否。staleなら実効openとして判定 |

`blocked(reason="disposition-requested")`はintentなし修復の終端化とdisposition受付を同じcommandが実行する場合だけ許す。
これにより上位設計のrepairingからdeferred/rejectedへの移行を保持する。reverifyとの競合は同一stateのCASで決着する。
停止対象はrunner所有のprocess group。finished_at、deadline経過、lease切れ、terminal status、settled単独を停止証拠にしない。
budgetなし正常receiptも直接子の終了までしか示さず、群停止の証拠として認めない。
実行済みintentからの受付は次の全条件を満たす場合に限る。

1. 保存comparison/replayが元attempt・intent・operation/fence・candidate・reproに完全一致する。
2. receiptはpassedまたはfailed、timed_out=false、block_reason=null、整数exit_codeである。
3. 元dispatchがbudget付きであることを実在するadmission/operation証拠へ束縛できる。
4. 内側verifier群と外側supervisor群のcleanup成功後に正規frameを返す実行経路である。
5. 同reservationのactive/system-recovery hold、未解消kill-unconfirmedが残っていない。

根拠は[verification runner](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/skills/mission/lib/mission_application/verification_runner.py#L424-L442)と[budgeted execution](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/skills/mission/lib/budgeted_exec.py#L273-L289)。
現在policyやreservation IDの文字列だけから出所を推定しない。旧証跡から確定できなければ拒否する。
settlementは32件で刈られるため全過去settled行の現存は要求せず、欠落を停止証拠にも使わない。
publication-result-lost/effects-unavailable/不明intentは識別可能な運用blockerとして返す。
自然終了を期待した無期限poll、再実行、資源解放、成功変換は行わない。E2の停止契約やreceipt schemaをE3で変更しない。

## 4. Request・adapter・予算

request IDはmission/session・lineage・受付operationからdomain-separated digestで導出する。
同一operationの内容違いは拒否し、同内容なら保存結果を返す。
packetは元要求・ledger・criterion・禁止副作用・元finding/output・元replay観測・候補を含む。
実装者の説明は未検証の提案として識別する。packetは1MiB、outputと個別evidenceは256KiB。超過を切り詰めない。

registryの5fieldとDのrequest/output schemaは変更しない。
登録済みadapterへ任意能力`launch_disposition(request_bytes, input_handle, snapshot_handles, dispatch)`を追加する。
既存5methodだけでE3対応と判断しない。bounded callback内のcallable検査で未対応を未起動blockedにする。
E3専用serializerとpacket materializationを明示分岐し、D request_documentやlaunchへ暗黙にE3を送らない。
既存transport・bounded callbackを再利用し、pinを読込・起動前後・回収時に照合する。
parent/child/context/input受領はhost観測から導出し、callerや子の自己申告で代用しない。
recover/cancelは元dispatchだけを扱い、recoverから再launchしない。

予算entryは既存`repair-disposition-run`、classは`repair`。
[budget契約](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/skills/mission/lib/mission_kernel/budget.py#L25-L31)のREPAIR_ENTRIESに含まれ、新分類やactivationは不要。
policyなしは非activationを維持し、adapter呼出しのwall-time/output上限は適用する。
policyありは既存予約・deadline・settlementを使う。起動後の停止不明では資源holdを保持する。

## 5. 棄却・alias・overflow

通常findingの判定はdeferred/not-violation/duplicate。非違反は義務・禁止副作用との関係と根拠refを持つ。
意味的正しさは独立adapterの信頼境界に依存し、理由の語句や長さで機械認定しない。
overflow対応表は元outputのfindings全indexについてindex・canonical element digest・verdictを持つ。
finding IDだけで対応付けず、重複IDでも全要素を検査する。欠番・重複index・余分要素・digest不一致・未判定はrejectedを作らない。
全件対応が256KiB以内に収まらない場合もblocked。部分対応を採用せず、stateにはbounded blocked理由を保持する。
61件はstateの重複先集合上限であり、元findingを61件へ切り捨てる上限ではない。
集合は対応表からkernelが導出する。対象は存在する通常finding lineageだけとし、欠落・自己参照・overflow対象・alias cycleを拒否する。
対応先のない正当な違反が一つでもあればoverflowを解決しない。
重複先がopen/deferred/pending/staleなら元findingも未解決。元義務リンクとsupplemental open obligationを削除しない。
全体coverage更新には新しいD attemptが必要。

## 6. 候補とcompletion

E2bのobserved_candidatesで凍結policyの全commandを同じキー集合で観測する。
prepare前後・実行直前・実行後・公開直前に再観測し、差異があればrejectedを公開しない。
対象snapshot subsetとfull候補digestを混同しない。markerはE2bと同じdomain/policy bindingのfull_candidate_digestを使う。
A→B→Aを観測したmutationは失効を保持する。completion読取時にも現候補と照合する。
D全originとrepair projectionのunionを評価し、欠落・不正・pending・blocked・deferred・staleを未解決とする。
有効な独立rejectedだけがfinding遮断を外せる。通常receipt・fresh search・coverage・既存gateは維持する。
新reviewの省略、row削除、score、forceは解決証拠にならず、dispositionを通常verification collectionへ追加しない。

## 7. 容量・公開・回復

disposition_reserveを実装し、v4 LEGACY_PRETTYのearly return前に加算する。v4のD dispatch禁止は維持する。
stageはprepared→dispatched→running→terminal、回復観測はbounded slot。
受付時にrequest実bytesと全残段予約を確保し、終端だけが当該itemの残り予約を解放する。
最大形はID/ref/int/時刻/intent/launch/61重複先/blocked fallback/回復slotを含む。
intentにはreservation_idとbudget_classを含め、v4 pretty/v5双方の深いindent・escapeを実encodeして増分を固定する。
D定数は緩めずE3定数を追加する。停止系・takeover後でも予約済みterminalの保存を検証する。

| 観測状態 | 回復 |
|---|---|
| 同一terminalがhead/operationに保存済み | 保存結果を返し再実行しない |
| 起動前の未対応・pin不一致・入力不明 | 未起動blocked |
| 元childが実行中 | 元dispatchをrecover/cancel |
| child終了確認済み、正確な結果あり | binding・候補・fenceを再検査して公開 |
| child終了確認済み、結果なし | publication-result-lost |
| 停止確認不能 | pendingと資源holdを保持しunknownをbounded保存 |
| effects staging失敗 | state-only blockedを試す |
| state-onlyも失敗 | pendingのまま次回reconcileへ |
| 公開後・応答前の停止 | operation readback |
| head CAS敗北 | 最新headから再判定 |
| fence/authority不一致 | 拒否。古い結果を成功にしない |

一時stagingだけで成功を回収しない。既存begin/read/lookup_operation/stage/commitとretry規律を使う。

## 8. 実装・検証・分割

公開CLIはrepair disposition prepare/run/reconcile。保存requestを一回実行し、回復は元実行だけを扱う。caller指定resolvedや結果importは設けない。
回帰群は独立性/pin/input、pending競合/過去群停止、full候補/A→B→A、全件mapping/61と62/alias、v4/v5容量/公開失敗/CAS/旧fence/応答喪失、通常検証とcoverageの独立性にまとめる。
同種入力表はpure validatorへ集め、公開CLIには権威境界・配線の代表例を残す。50入力以上の独立探索は実際の素通り・誤検知を記録する。
既存E2b/Dのfixtureとtransportを再利用し、D dispatch/publishやreconcile直積の複製を避ける。
異系統レビュー・独立Checkerを同headで取得し、ready後CIと最新base/exact-headを確認する。

実装担当の未実測見積は契約/kernel350〜500、application/runtime200〜300、capacity80〜130、tests350〜450、docs/inventory40〜80、reviewed計1,020〜1,460行。
まず一単位として実測し、1,400行を超える場合は保証を削らず安全に分割する。
分割候補は①契約・writer・completion、②adapter・公開CLI。
先行では公開CLI/parser/command dispatcherにE3 commandを登録せず、内部writer/pure validatorだけを検証可能にする。
新collectionがある場合だけcompletionの閉じた検査を通し、不正は未解決または拒否にする。旧state/D/E2経路/receiptの意味は変えない。
後行mergeまでIssue907の公開CLI ACを完了としない。必要な子Issueは分割方針確定後に親子リンク付きで起票する。
完了は現行Issue907全ACと本契約の検証、必要レビュー、CI、通常gateのmerge/readbackまで。
停止証拠が復元できない既存intentは条件緩和で隠さず、識別可能なblockerとして残す。
