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
| state要約 | request ID/ref、full_candidate_digest、supersedes_attempt_id、受付generation/fence、stage、intent、launch ref、terminal ref/generation、判定種別、重複先ID集合 |
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

## 3. 修復との排他と受付

同一lineageで修復pendingとdisposition pendingを同時に保持しない。
既存E2が保存したterminalを運用上の終端として扱う。過去全intentのprocess group停止証明をE3だけの追加受付条件にしない。
terminalを群停止証明と呼ばず、過去実行が絶対に候補を変えないとも主張しない。

| 受付時点 | 判定 |
|---|---|
| open/deferred/実効open | prepare可能 |
| repairing、intentなし | 同一fenced commitで修復をblocked化しdispositionを受付 |
| repairing、intentあり | pending排他により受付拒否。既存reconcile等でterminalになれば受付可能 |
| disposition pending | 同一operationの再読取だけを許可。repair begin/reverifyと別判定を拒否 |
| 現候補で有効なverified/rejected | 冗長prepareとrepair beginを拒否。staleなら実効open |

intentなし修復を終端化する場合、E3 request要約とartifactの`supersedes_attempt_id`へ対象attemptを記録する。通常prepareではabsentとする。
修復terminalはblocked/disposition-requestedとし、terminal.operation_idをE3 prepareの実際のoperationへ一致させる。通常repairのterminal_operation導出を流用しない。
decoderは同じoperation/fenceのrequestがちょうど一つあり、supersedes参照とintent不在のattemptが一致することを双方向に検査する。
両row変更とrequest effectを同じTransition/effects commitへ含め、その追加費用をE3受付予約へ計上する。既存修復terminal予約を流用しない。

deferredは常に未解決。rejectedは現在候補の独立判定、各段階のfull候補再観測、current fence/CAS、候補marker、completion再検証を必要とする。
過去実行等が後から候補を変えれば通常の候補失効規律で無効化する。新attempt開始後に古いdispositionへfallbackしない。
実際のactive/system-recovery holdは既存budget gateが扱い、E3が消去・解放・無視しない。
E2 runner・receipt schema・repair beginの運用境界を変更せず、自然終了を期待した無期限pollや再実行を追加しない。

## 4. Request・adapter・予算

request IDはmission/session・lineage・受付operationからdomain-separated digestで導出する。
同一operationの内容違いは拒否し、同内容なら保存結果を返す。
packetは元要求・ledger・criterion・禁止副作用・元finding/output・元replay観測・候補を含む。
実装者の説明は未検証の提案として識別する。packetは1MiB、outputと個別evidenceは256KiB。超過を切り詰めない。

registryの5fieldとDのrequest/output schemaは変更しない。
登録済みadapterへ任意能力`launch_disposition(request_bytes, input_handle, snapshot_handles, dispatch)`を追加する。
既存5methodだけでE3対応と判断しない。bounded callback内のcallable検査で未対応を未起動blockedにする。
E3専用serializerとpacket materializationを明示分岐し、host callback action許可リストへlaunch_dispositionを追加する。D request_document・decoder・launchへE3を送らない。
既存transport・bounded callbackを再利用し、pinを読込・起動前後・回収時に照合する。
parent/child/context/input受領はhost観測から導出し、callerや子の自己申告で代用しない。
recover/cancelは元dispatchだけを扱い、recoverから再launchしない。

予算entryは既存`repair-disposition-run`、classは`repair`。列挙の新設やactivationは不要だが、現行budget_class/admitはE3未接続なので両方を実装対象とする。
[現行分類器・admission](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/skills/mission/lib/mission_kernel/budget_decisions.py)のE2専用分岐をE3へ接続する。

新規admissionではtarget lineageの最新dispositionがpreparedでterminal/intent/launchなし、同lineageにpending repairなしであることを照合する。
operationは`disposition-dispatch:`とrequest IDのSHA-256部分から導出し、candidate_digestは保存requestのfull_candidate_digestへ一致させる。
分類器はprepared dispositionの存在時だけrepairを返し、admitでtarget/request/operation/candidateを閉じたprojectionから再検査する。対象/最新prepared/request/op/candidate不一致はbudget-disposition-target-invalidで拒否する。
final latchまたはrepair deadline到達後の新規runはbudget-final-latched。final budgetへ読み替えない。
admissionとdispatch intentを同じfenced commitで公開する。同一dispatch再送は保存operationまたは元reservation/terminalをreadbackし、二度目の予約・launchをしない。
既存dispatchの回収・settlementはrecover/system-recover規律に従う。現在のholdを保持する。
policyありのterminal公開ではkernelが当該intentの予算状態を同じfenced commit内で分類する。rejected/deferred/blocked/publication-result-lost/state-only blockedの全経路へ適用する。
- reservationが現在openならSettleDispatchBudgetを同commitで適用し、欠落・reservation/candidate/result/outcome/telemetry不一致をdisposition-settlement-invalidで拒否する。
- 既にclosedで履歴があれば、同intentのreservation_idに対するsettled/charged-full-unknownを検査して参照し、新規SettleDispatchBudgetを要求しない。既存chargeを変更せず二重計上しない。
- closedかつ32件履歴からtrim済みでも、kernel受付済みintentのadmission binding（entry/target/op/fence/reservation）を保持したままterminalを公開できる。履歴の欠落を未精算扱い・ゼロcharge・資源解放の証拠にしない。
- kill-unconfirmedはreservationがopenのままなのでclosed/trim分岐へ入らない。停止確認後の精算で元chargeを維持して解放する。
- 起動前blockedでもopen予約があれば精算し、まだadmissionのないblockedには精算を捏造しない。callerが任意reservationへ付け替えることは全分岐で拒否する。

期限超過や別mutationによるexpire_reservationsが既にcharged-full-unknownを記録した場合も、後続terminalを二重精算で拒否しない。state-only失敗後の遅延retryにも同じ分岐を適用する。
candidate_digestは保存requestのfull_candidate_digest。result_digestはdomain=repair-disposition-result/1と、検証済みverdict・reason code/本文・evidence digest集合・canonical全件mapping digestから導出する。
request/dispatch ID・generation・時刻・terminal ref等の鮮度metadataを進展digestへ含めず、同候補で同じ判定証拠の反復がProgressSignatureへ同じ結果として記録されるようにする。
結果のないblockedは固定reasonと空evidence/mappingから同じ形式を導出するが、進展の連続回数は更新しない。E3 entryのsettleで結果未取得時は既存progressを保持する分岐を追加し、他entryの意味を変えない。公開operation/terminal artifactへの結合は別途kernelが照合する。
停止確認済みはsettledで観測elapsed/telemetryを精算し、独立判定結果が得られたときだけcompleted=trueとする。E3理由codeを予算専用refusal_reasonへ流用しない。
停止確認不能はkill-unconfirmedを同じpending更新で保存しholdを維持する。terminal化・資源解放は元childの終了確認後だけ行う。精算済みreadbackの再送で二重計上しない。
policyなしは非activationを維持し、adapterのwall-time/output上限を適用する。

## 5. 棄却・alias・overflow

通常findingの判定はdeferred/not-violation/duplicate。非違反は義務・禁止副作用との関係と根拠refを持つ。
意味的正しさは独立adapterの信頼境界に依存し、理由の語句や長さで機械認定しない。
overflow対応表は元outputのfindings全indexについてindex・canonical element digest・verdictを持つ。
finding IDだけで対応付けず、重複IDでも全要素を検査する。欠番・重複index・余分要素・digest不一致・未判定はrejectedを作らない。
全件対応が256KiB以内に収まらない場合もblocked。部分対応を採用せず、stateにはbounded blocked理由を保持する。
61件はstateの重複先集合上限であり、元findingを61件へ切り捨てる上限ではない。
集合は対応表からkernelが導出する。存在する通常finding lineageだけをtargetとし、欠落・自己参照・overflow対象・alias cycleを拒否する。
duplicateは存在だけでは解決を移譲しない。各edgeで次を元artifactから再計算し、caller申告を権威にしない。

- sourceとtargetのcontract/requirement/policy bindingが一致し、双方の解決証拠が同じ現在full候補に有効である。
- sourceのcriterion、command、verifier definition、typed repro bindingがtargetと一致する。導出不能なら未解決。
- targetがverifiedなら有効な修復receiptがあり、検証scopeがsourceのrequirement_idsとprohibited_side_effect_idsを包含する。
- targetがrejectedなら有効な独立判定scopeがsourceのcriterion・requirement_ids・prohibited_side_effect_idsを包含する。
- aliasをたどる各edgeを検査し、最終targetの有効な解決を確認する。

別scope/別reproのtargetへ義務を移譲しない。異なる反例についてはsource自身のnot-violation独立判定を要する。
overflowも各elementをsourceとして同じ検査を行い、criterion・義務scope・reproを元outputから導出する。導出不能elementはblockedとする。
対応先のない正当な違反が一つでもあればoverflowを解決しない。全elementが条件を満たした場合だけrejectedを有効とする。
全件mappingが256KiBを超える場合はdisposition-mapping-over-limitを保存し未解決とする。現E3に自動解消経路がないことを利用文書へ明記し、上位設計§9の進行不能条件と並べる。
重複先がopen/deferred/pending/staleならsourceも未解決。元義務リンクとsupplemental open obligationを削除しない。全体coverage更新には新しいD attemptが必要。

## 6. 候補とcompletion

E2bのobserved_candidatesで凍結policyの全commandを同じキー集合で観測する。
prepare前後・実行直前・実行後・公開直前に再観測し、差異があればrejectedを公開しない。
E3 request artifactとstate要約に固定長full_candidate_digestを保存し、E2bの同domain/policy bindingの関数で導出する。snapshot subsetと混同しない。
marker比較基準は①last_candidate_changeのfull候補digest、②最新E3 requestのfull_candidate_digest、③通常findingだけ既存introduced_candidateの保守的判定の順とする。
比較基準のないoverflowは最初の受理済みfull観測でmarkerを初期化する。解決証拠がない初期化を解決にしない。
prepareはfull観測/marker更新の後に受付generation、terminal公開は観測の後にterminal generationを割り当てる。新requestで古いmarkerを消去・巻戻ししない。
invalidate_observedのfinding限定分岐を改め、overflowも更新する。E3 validatorはintroduced_candidateを要求せずrequestとfull候補bindingを使う。
通常verification、fresh-review prepare、repair begin/reverify/commit、E3 prepare/run/commit、mark-passes/closeoutを含むcompletion由来mutationの全観測producerを照合する。
E3履歴があるstateで候補を観測するcommandは完全なobserved_candidates carrierを必須にし、欠落・部分mapを拒否する。halt等の観測しないcommandへは要求しない。
現D terminal commandは候補carrierを持たず候補観測producerではない。上位設計§2のD terminal列挙は、現実装ではprepare時の観測とterminal保存を区別して読む。
E3履歴のない旧stateの互換経路は維持する。現在候補で作ったrejectedを古いintroduced subsetだけで再失効させない。
判定の有効性にはlast_candidate_change.generation < request受付generationかつ現在full候補=request.full_candidate_digestを必要とする。terminal generationで受付後の変更を上書きしない。
A→B→Aを観測したmutationは失効を保持する。同じfull観測の反復では失効させない。読取completionは現候補とのvalidityを再計算し、marker保存とは主張しない。

applicationのobserve_disposition_results相当で、最新判定/alias/overflowに必要なrequest・launch・terminal・output・mappingを安定読取・digest/size検査しephemeral carrierへ載せる。
kernelはclosed shape、origin/scope/repro、independence、request/operation/fence、全件mapping、候補/generation、aliasを再検査する。state要約だけを解決の権威にしない。
D全originとrepair projectionのunionを評価し、欠落・改変・不正・pending・blocked・deferred・staleを未解決とし、古いterminalへfallbackしない。
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
回帰群は独立性/pin/input、pending競合/既存terminal後の到達性と候補失効、full候補/A→B→A、全件mapping/61と62/alias、v4/v5容量/公開失敗/CAS/旧fence/応答喪失、通常検証とcoverageの独立性にまとめる。
同種入力表はpure validatorへ集め、公開CLIには権威境界・配線の代表例を残す。50入力以上の独立探索は実際の素通り・誤検知を記録する。
既存E2b/Dのfixtureとtransportを再利用し、D dispatch/publishやreconcile直積の複製を避ける。
異系統レビュー・独立Checkerを同headで取得し、ready後CIと最新base/exact-headを確認する。

実装は受入条件を保持したまま次の3段階へ分割する。各段階でreviewed行数を実測し、1,400行を超える場合は不可分境界を再評価する。

- [A:閉じた契約と保守的completion（Issue1005）](https://github.com/tackeyy/mission/issues/1005): request/state/artifact契約、pure terminal/mapping/alias validator、旧state互換を扱う。command登録・writer・正の解決接続を含めず、新E3stateを生成しない。非空E3履歴は旧修復解決集合から除外し未解決、欠落・空は旧判定を維持する。
- [B:内部実行と解決証拠（Issue1006）](https://github.com/tackeyy/mission/issues/1006): writer/supersedes、候補世代と一時carrier、artifact再読取と正のcompletion、容量・予算精算・回復を不可分単位として扱う。
- [C:公開CLIとadapter（Issue1007）](https://github.com/tackeyy/mission/issues/1007): adapter/dispatcher/公開CLI、E3専用serializer、runtime能力と全予算の照合を扱う。独立性の検証はtool/replayの実観測保証を代替しない。

各段階で旧state/D/E2経路/receiptの意味を保持する。後行mergeまで[親Issue907](https://github.com/tackeyy/mission/issues/907)の公開CLI ACを完了としない。
完了は現行Issue907全ACと本契約の検証、必要レビュー、CI、通常gateのmerge/readbackまで。
policyありpreparedのrepair分類と一回admission、wrong target/op/既launch/final拒否、policyなし修復terminal後の判定到達性、paired request/terminalの片側欠落を回帰に含める。

policyあり全terminalと精算の同commit、精算欠落/不一致の拒否、未予約blocked、停止不明hold、同候補・同結果の進展反復を回帰に含める。

terminal前に別mutationで予約失効済みの経路と、さらに精算履歴がtrim済みの経路でも、遅延reconcile/state-only retryが終端化し二重計上しないことを回帰に含める。
