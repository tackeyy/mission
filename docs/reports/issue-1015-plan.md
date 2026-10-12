# Issue1015実装前計画

結論: 単一PRの見積は1,730〜2,620 reviewed行。1,400行以内へ保証を削って収めず、保存・容量・精算と、候補観測・正のcompletionの2子へ分割することを推奨する。これは実装計画であり、独立レビューacceptedでも実装完了でもない。

対象: [Issue1015: 独立判定の保存と精算を有効な解決へ接続する](https://github.com/tackeyy/mission/issues/1015)。調査基点は`2a443a241d86738669414eb4eaac8826ba5b2d02`。対象設計は[独立判定§4〜§7](https://github.com/tackeyy/mission/blob/2a443a241d86738669414eb4eaac8826ba5b2d02/docs/design/907-independent-disposition.md)。実host、adapterの公開CLI、認証、予算policy activationは含めない。

## 現物と設計の対応

以下のパス・行は上記基点での参照位置。ソース参照は`skills/mission/lib/`を起点とする。

| 現物 | 設計との対応・残作業 |
|---|---|
| `mission_kernel/finding_disposition.py:61,105,275,432` | request、保存要約、paired supersedes、terminal、mapping、aliasの純粋検査は存在。`validate_disposition_evidence`は解決を宣言しない。writerと回復slotはない。 |
| `mission_application/disposition_evidence.py:22`、`mission_application/ports.py:14`、`mission_persistence/disposition_evidence.py:12` | stable readerと全証拠carrierは存在。再読取、digest/size、元finding/overflow/aliasの認証を再利用できる。readerによる解決権威はない。 |
| `mission_kernel/commands.py:345`、`:630`、`:704`、`mission_kernel/transitions.py:2527,2759` | E2 commandとeffect sealは存在。E3 command、union/name/table登録、effect claimの閉じた結合は未実装。 |
| `mission_kernel/state_capacity.py:480,657` | `disposition_reserve`は0。現在はv4 pretty早期returnの後で加算するため、単にstubを埋めてもv4は予約しない。 |
| `mission_kernel/budget_decisions.py:73,165,315` | entry列挙は存在するがclassはE3を拒否、admitのprepared/target/request/candidate照合なし。openのsettleとkill holdは既存APIがある。closed/trim済みのE3終端接続はない。 |
| `mission_kernel/repair_reverification.py:12,181,192,200` | full候補digest関数は再利用可能。generationはE3受付/terminalを未計上。marker比較はintroduced subsetを使い、overflowを更新しない。観測は`transitions.decide:2708`でreducer後に適用する。 |
| `mission_kernel/repair_lineage.py:308` | D originとrepair projectionのunion検査、E2解決は存在。dispositionsがあるrowをE2解決から除外し、E3の正の解決は接続していない。 |
| `mission_application/fresh_review_completion.py:63,100`、`mission_kernel/fresh_review_coverage.py:49` | completionは全凍結commandを再captureしfull carrierを既に持つ。ただしdisposition carrierはない。 |

設計の「現D terminalは観測producerではない」は現物と整合する。設計冒頭の古い基点と「実装前」は履歴位置を示し、現実装全体が未実装という意味に使わない。実行用adapter能力・packet serializer・host action許可リストは後続の[公開adapter/CLI Issue1007](https://github.com/tackeyy/mission/issues/1007)との接続契約であり、本変更で実hostやCLIへ迂回接続しない。

## 最小変更設計と再利用

1. `mission_kernel/disposition_commands.py`相当の専用reducerを追加し、既存純粋decoderからwriterを分離する。prepare、dispatch intent、launch、回復観測、terminalのtyped commandを`commands.py`へ登録する。prepareはD origin・request bytes・full候補・operation/fence・最新pending排他を照合し、intentなしrepairのblocked化とrequest保存を一Transitionに含める。任意のresolved/outcomeをcallerから採用しない。
2. request/input、launch、terminal/output/mappingは既存`FreshReviewInputEffectClaim`とcontent-addressed refを再利用し、必要なclaimだけ専用型へ拡張する。`bind_transition_effects`へE3全commandを明示登録し、claim数・kind/target/digest/size不一致を拒否する。`PreparedEvidenceOperation`、`execute_evidence_operation`、repositoryのoperation replayを再利用する。prepare retryは現在headではなく元operationの保存結果を照合する。汎用UoWやCAS形式を作り直さない。
3. `state_capacity.py`へstage別残予約を追加する。requestの実encode増分と、prepared/dispatched/runningからterminalまでの最大encode増分をv4 pretty/v5で計測する。全ID/ref/int/escape/deep indent/61 targets/blocked fallback/回復slot/paired repair追加bytesを含める。E3予約をv4早期return前に加算し、decode不能はSTATE_LIMIT。terminalだけ残予約を解放する。D定数は緩めない。
4. `budget_decisions.py`のclass/admitは最新preparedの閉じたprojectionからentry/lineage/request/op/full候補を再照合する。final latch/deadlineは新runを拒否し、policyなしは非activationを保つ。dispatch reducer内でadmissionとintentを同時保存する。
5. terminal reducerは元intentのadmission bindingからopen/closed/trimを分類する。openは検証済み`SettleDispatchBudget`とterminalを一commit、closed retainedは同reservationの既存精算を参照、closed trimmedは受付済みintentだけを根拠にterminal化しchargeを変更しない。kill-unconfirmedはopen holdを保存しterminalにしない。admission前blockedには精算を作らない。期限expire、公開失敗、state-only fallbackも同じ分類関数へ集約する。結果digestは意味的verdict/reason/evidence/mappingだけから導出し、blockedで結果未取得のとき既存progressを保持する。
6. 候補観測を共通関数へ集約する。E3履歴があるstateでは観測producerのfull carrier欠落・部分mapを拒否し、非観測commandは要求しない。marker→最新request full digest→通常finding introducedの順に比較し、overflowを初回full観測で初期化する。E3 generationを最大値へ含め、同commitの観測をprepare/terminal採番より前に適用する。後処理だけで済ませない。A→B→Aはmarkerのgenerationで失効を維持する。
7. `FreshReviewBindings`へ任意のtyped disposition evidenceを追加し、application completionで`observe_disposition_results`と`FilesystemEvidenceReader`を使う。kernel completionで毎回再検証し、直接not-violationのresolved集合と、現在候補に有効なE2修復target集合を作ってからalias closureを計算する。D origin全体とrepair unionの認証を維持する。最新pending/blocked/deferred/staleなら古いterminalへfallbackしない。通常receipt、fresh search、coverageを除外しない。

候補producerの確認対象は通常verificationの`mission_application/verification_exec.py`・`verification_execution.py`・`verification_budget.py`、fresh prepareの`fresh_review.py`、repair begin/reverify/commitの`repair.py`・`repair_reverification.py`、completion/mark-pass/closeoutの`fresh_review_completion.py`・`review.py`・`acceptance.py`。`RecordVerificationReceipt.candidate`はfull carrierとして使われるが任意fieldなので、E3履歴時の必須化をkernelで行う。read-only completionはvalidity再計算でありmarker保存と主張しない。

## TDDテストリストと検出価値

各群は先にRedを確認し、共有入力表はpure reducer/validatorへ置く。既存fixture`rejected`/`reverified`とD/E2 transport・UoW fixtureを再利用する。sleepと実hostは不要。費用は未測定。

| 群 | Redで検出する現実的故障 | 既存保護との差・配置 |
|---|---|---|
| 保存・排他・seal | requestの内容違い再送、他lineage/古いfence、同時pending、片側supersedes、異なるartifact bytesが保存される | `test_issue907_disposition.py`の純粋shape/双方向検査を保持。新規writer testでaccepted stateとeffect原子性を検査。 |
| 容量 | v4だけ予約漏れ、takeover/停止後terminal不能、running/recovery/61 targetsで最大bytes超過 | `test_issue936_state_capacity_reservation.py`のzero stub前提・v4全予約ゼロ前提をE3なしで成立する形へ更新。E3 stage別最大実encodeとlimit境界へ追加。E2 near-limitの全直積を複製しない。 |
| admission/精算 | target/candidate付替え、二重予約・課金、trim後の誤ゼロcharge、expire後のterminal拒否、kill hold解放 | `test_issue919_budget_decisions.py`の一般予算を再利用。E3 pure tableでopen/closed/trim/kill/admissionなしを全terminal経路に共通適用し、UoWには代表3境界を残す。 |
| 公開と回復 | staging失敗で片側だけ公開、CAS敗北/応答喪失で同operation再launch、state-only失敗後に予約を失う | repository fault injectionでfence/CAS/operation readbackを確認。既存E2のrunnerをコピーせず、内部command＋fake effectsで検査。再launch禁止の公開経路は後続CLIで検証。 |
| 全候補とgeneration | 非replay commandの変更やoverflow変更を見逃す、A→B→Aが復活、terminal採番で受付後変更を覆う | `test_issue906_reverification.py`のfull-map/往復失効を拡張。E3履歴ありの各producerに欠落carrier拒否の代表wiring test、full入力表は共通関数1箇所。旧stateのsubset互換は保持。 |
| 正completion | summaryだけでpass、古いrejectedへfallback、別scope alias、未解決target/overflowを解除 | `test_issue1014_disposition_reader.py`のファイル安定性・全件mapping・independence・scope検査は保持。`test_issue907_disposition.py`の「completion authorityなし」期待はcarrierなしで保持し、有効carrierありの正例だけ新設。直接kernelで改変carrier再検証も検査。 |
| 独立gate | E3 rejectedで通常verification/fresh search/coverageを省略できる | 各必須gateを1つずつ欠いたcompletion拒否と、全gate成立時だけpassの代表ケース。既存一般gateの全表は複製しない。 |

50入力以上の探索はwriter/精算/候補/completionの新しい権威境界を対象に、正常・不正双方を用意し、件数・素通り・誤検知を記録する。既存reader検査の再実行だけで置換しない。

CI経路は`.github/workflows/ci.yml:125`→`make test-shard`→`scripts/ci_shard_targets.py`→`skills/mission`のpytest。新規testはこの木へ置く。mirrorは`scripts/sync-codex-plugin-wrapper.sh`と`test_codex_wrapper_sync.py`、変更モジュールのstate-writer/budget-spawn inventory fragmentとAPI/command登録contractも検証対象。inventoryは随伴でもreviewed行へ数える。コード変更PRでdocs-only fast pathを根拠にしない。

## reviewed行数見積と安全な分割

数値は追加＋削除の概算で、実測ではない。testsを含み、byte-identicalな`plugins/mission/skills/**`・`plugins/mission/scripts/**`のmirrorだけ除外する。通常inventory fixture・計画docsは除外しない。

| 変更群 | 子A:保存・容量・精算 | 子B:候補観測・正completion |
|---|---:|---:|
| command/reducer/seal/operation readback | 300〜370 | 30〜60 |
| 容量最大形・残予約 | 100〜140 | 0〜20 |
| 予算admission・terminal分類・progress | 140〜180 | 0〜20 |
| 候補marker/generation・mutation producer配線 | 30〜50 | 180〜300 |
| completion carrier/union/alias解決 | 0 | 140〜230 |
| tests | 340〜480 | 310〜500 |
| inventory・設計/検証記録 | 90〜130 | 70〜140 |
| **合計** | **1,000〜1,350** | **730〜1,270** |

両PRとも600超の説明が必要。Aは「同じrequestの将来保存保証・admission・終端精算を切り離すと受付済みoperationを安全に終端化できない」、Bは「観測の全mutation配線と正completionを切り離して有効化すると失効が抜ける」が説明の中心となる。Issue1015は親trackingへ変更し、[A:保存・容量・精算Issue1019](https://github.com/tackeyy/mission/issues/1019)と[B:候補観測・正completion Issue1020](https://github.com/tackeyy/mission/issues/1020)を公式sub-issueで接続した。A merge後の最新baseからBを開始する。

子AのAC:

- prepare/paired supersedes/request effectsとstage別将来容量を同fence/CASで保存。
- dispatch admission/intent、launch、bounded回復観測、terminalの閉じたwriterを登録。
- open/closed/trimの精算・hold・progressを全終端経路に適用し、公開/CAS/state-only失敗と遅延retryを検証。
- **正completionは無効のまま**。E3履歴rowは常に未解決。公開run/CLI未接続を維持。

子BのAC:

- 全producerのfull carrier、E3を含むgeneration順序、overflow初期化、A→B→A失効を原子的に接続。
- stable evidence carrierをcompletion消費者へ渡し、kernelでorigin/候補/scope/独立性/mapping/aliasと現在target resolutionを再検証。
- 全既存gateを維持した正completionと、欠落/改変/最新未解決/trim後retryの拒否を検証。

中間不変条件: A後にrejectedが保存されてもpassを許可しない。BまでE3履歴を持つrowは従来のE2 fallbackを使わない。Aに最低限のE3 generation採番は必要だが、候補失効が完成したという主張はしない。Aの保存したrequestとterminalは後続Bが再検証できる形式にする。

Aが実測1,400行を超えた場合は保証を削らず、A1「prepare/paired supersedes/全stage容量・閉じたschema、dispatchを拒否」→A2「admission/dispatch/launch/回復/terminal精算、positive off」へさらに分ける。A1は将来最大形を予約し、preparedをpendingとして排他維持し、公開CLIから到達させない。A2の実測も閾値を超えるなら実装を止め、再計画する。例外を自動適用しない。

## 独立設計レビューへ渡す仮決定

以下は選択肢の列挙ではなく、実装で採用する推奨契約。既存保証の例外は設けない。新規field・関数名は提案であり、現物に存在するものとは区別する。

### 1. 回復slotは最新1件の診断で、停止・解決の権威にしない

disposition要約へ任意field`recovery`を追加する。欠落は「回復観測なし」と同義で読み、旧rowの読取だけでは追加しない。新prepareは`absent('not-observed')`を保存する。その他のnull・unknown field・未知enumは拒否する。具体値は次のclosed objectとする。

| field | 型・上限・照合 |
|---|---|
| `operation_id` | writerの回復operation。既存`_identifier`、最大128文字。prepare/dispatch/launch/terminalのoperationと混同しない。 |
| `fencing_epoch` | writerの現在epoch。boolを除くint、1〜2^63−1。command処理時に現在leaseへ一致。 |
| `dispatch_operation_id` | 元intent.operation_idと完全一致するidentifier。 |
| `dispatch_fencing_epoch` | 元intent.fencing_epochと完全一致するint。takeoverで書き換えない。 |
| `observed_at` | 既存timestamp decoderのUTC wire形式、最大27文字。intent.started_at以降。時刻だけで停止・結果有効性を認めない。 |
| `status` | `running` / `stop-unconfirmed` / `exited-result-available` / `exited-result-unavailable`の4値。 |

配列・count・自由記述・raw output・追加refは持たない。全体の最大bytesは上記最大値をv4 pretty/v5双方で実encodeした値として容量予約へ入れる。preparedではabsenceのみ、dispatched/runningでは元intentを必須にし、同dispatchに対する新しい観測だけでslotを置換する。terminal化時は最後のslotを保持できるが再更新しない。同operation再送はrepository operation readbackを優先し、slotの最新値から過去operationの結果を作らない。

`stop-unconfirmed`はpending/hold維持。`exited-*`もslotだけではterminal/解放を許可しない。terminal commandは後述の独立したhost観測carrierを必須にする。回復slotは安全性の証明ではなくboundedな再開診断なので、古いstateにslotがないことも停止証明にはならない。根拠: `finding_disposition.py:105`のclosed要約、`fresh_review_dispatch.py:239`以降の元dispatch回復、`fresh_review.py:23,138,150`の既存上限。

### 2. E3観測対象はreducer前にmarkerを更新し、その後に採番する

新pure関数`observe_candidate_state(state, command)`はfull carrierの抽出・完全性・selected subset一致・marker更新だけを行い、`(observed_state, changed)`を返す。verification receiptをまだ保存していない段階で末尾receiptを書き換えない。対象は①E3 prepare（初回を含む）②E3 dispatch/launch/terminal③E3履歴を持つstateの既存candidate producer。halt/単なる回復slot記録など観測しないcommandは対象外。

`transitions.decide`の呼出し順は、rule/type guard選択→元stateを保持→対象commandなら`observe_candidate_state`→選択reducerへobserved_stateを渡す→最終projection再検査→`_register_transition(transition, original_state, command)`とする。pre観測対象には現在の`invalidate_observed`後処理を重ねない。E3履歴なし・E3 prepare以外の旧経路は現在の後処理を維持する。reducer/容量/CAS拒否なら観測stateも保存せず、両方同commitで成立させる。

E3 prepareは観測済みstateから`next_generation`を呼びrequest受付generationを付ける。terminalも観測済みstateから採番し、request受付generationとmarkerを変更しない。`next_generation`はmarker、receipt、repair terminalに加えて全dispositionの受付generation・terminal generationの最大値+1とし、2^63−1到達時は拒否する。`RecordVerificationReceipt`はpre観測後にreceiptを保存し、新receiptのrepair_generationをその時点の`next_generation`で付ける（存在しない末尾receiptへ書かない）。この順序により「観測marker<受付<terminal」と、受付後の候補変更markerを同時に保持できる。

full digestが同じならmarkerを更新しない。比較基準はmarker full digest→最新request full digest→通常finding introducedの保守的比較。overflowで基準なしならmarkerを初期化してから受付採番する。A→B→Aでdigestが戻ってもmarker.generationが受付以後なので失効する。根拠: `transitions.py:2680,2708,2723`、`repair_reverification.py:12,181,200,233,241`と`finding_disposition.py:275`。

### 3. trim後は保存intentからadmission projectionを再構成し、再精算しない

新pure関数`disposition_admission_binding(document, row, item, request)`は、decoderとorigin/request認証を通した入力から次の閉じたprojectionを導出する。commandが同名fieldを持っても権威にしない。

| projection field | 管理元 |
|---|---|
| `entry` | 固定`repair-disposition-run` |
| `target` | 認証済みrow.lineage_id=request.lineage_id |
| `operation_id` | 保存intent.operation_id=`disposition-dispatch:`+request IDのdigest部 |
| `fencing_epoch` | 保存intent.fencing_epoch（現在writer epochへ付替えない） |
| `reservation_id`,`budget_class` | 保存intent。classは固定`repair` |
| `candidate_digest` | 認証済みrequest.full_candidate_digest=item.full_candidate_digest |

policyありのreservation IDは既存`_reservation_id(entry,target,operation,fence,ordinal)`で再導出する。保存IDから`reservation:<1〜19桁正整数>:<32桁hex>`をparseし、ordinalがpolicy.max_dispatches_per_phase以下、repair phaseの累積reservation_count以下であること、再導出IDとの完全一致を検査する。hashはentry/target/op/fence/ordinalへの結合であり、candidateを直接認証するものではない。candidate/requestの認証は保存request refと受付時の閉じたprojectionが担う。policyなしは予算admission/精算を作らず、非予算reservation識別子はreservation:にcanonical_digestでdomain=repair-disposition-dispatch/1、operation_id=保存intent.operation_idをencodeしたdigest部を連結する。operation ID自体はrequest IDに結合され、既存Dの導出形式をE3専用domainで使う。保存IDの形でpolicy有無を勝手に読み替えない。

terminal処理は既存clock/expire処理を適用したledgerで分類する。openならreservationのentry/target/op/fence/classとprojectionを完全照合し、kernelが導出したcandidate/result/outcome/telemetry/completedを`SettleDispatchBudget`と照合して同commitで保存する。closed retainedなら同IDの`settled`/`charged-full-unknown`を参照し、追加settlementなし、既存charge不変。closed trimmedなら同IDのopen/system-recovery reservationがないことと、上記受付済みprojectionを検査し、terminalだけを保存する。閉じた履歴に`kill-unconfirmed`があり対応openがない状態は不整合として拒否する。

既存`Settlement`にはcandidate/result digestを保持しないため、retained/trim分岐で「過去result digestまで照合できた」とは主張しない。terminal結果は現在の証拠から検証する。trim分岐の安全性はkernel受付済みintentとledgerの永続整合を信頼する既存契約に依存し、履歴欠落やhashだけで新admissionを認めるものではない。新prepare/dispatchにこの分岐を流用せず、同requestの元intentがあるterminal/reconcileだけに限定する。根拠: `budget_decisions.py:94,165,298,315`、`finding_disposition.py:125,275`。これにより期限expire済みchargeを維持し、trim後の遅延retryを新課金にもゼロchargeにも変えない。

### 4. commandは結果の提案だけを運び、kernelが証拠からoutcomeを導出する

prepareはrequest/inputの実bytesとref/effect claim、D origin evidence、完全な候補carrierを運ぶ。dispatchは保存request IDとadmission要求、launchは元dispatchに結び付くlaunch receiptを運ぶ。terminalは`DispositionEvidence`、full候補、host観測、精算の提案だけを運ぶ。caller指定`resolved`、`independent`、`duplicate_targets`を受付fieldにしない。outcome/reasonを持つterminal artifactを提案として渡す場合も、kernel導出値との一致だけを許す。

host観測のE3専用closed carrierは`operation_id`,`fencing_epoch`,`request_id`,`nonce`,`child_identity`,`process_exited`,`exit_code`を必須とする。前5fieldは検証済みlaunch receiptへ型と値を完全一致、process_exitedはbool、exit_codeはint32または未終了時のnull。正のterminalはprocess_exited=trueかつexit_code=0を要求し、未終了/null/異常終了でrejectedを作らない。起動済みblockedの終端にも結合済みの終了確認を要求し、停止不明は回復slotとkill holdだけを保存する。未起動blockedはlaunch/実childがないことを確認して別経路で保存する。元dispatchと異なる報告・保存launchと違うreceiptは拒否する。

rejected/deferredのprospective terminalを組み立てたdocumentに対し既存`validate_terminal`を呼び、request・input・元source・launch・terminal・output・supporting evidence・mappingのref/実bytes、origin/scope/repro、hostの独立性観測、候補/generation/fenceを再検証する。blockedは同関数が未解決として拒否するため、専用reducerでclosed blocked reason/ref・元intent・終了または未起動・effect・精算を検査し、解決を一切導出しない。duplicate_targetsは`validate_mapping`の返値からのみ保存する。state要約、slot、childの自己申告だけではこのcarrierを代替できない。effect sealで今回公開bytesを結合し、後のcompletionでもstable readerで再読取してkernel再検証する。

既存`validate_output_sender`はDの`FreshReviewRecord`専用なので、E3へ型を偽装して流用しない。上記5field照合という共通規則だけをE3専用pure関数で実装する。host観測は内部application/adapterが取得する事実であり、署名された外部証明ではない。任意内部callerがhost観測を偽造できる脅威までは既存契約で防げないが、公開CLIに結果importや観測fieldを露出しないことで権威境界を維持する。根拠: `fresh_review_output.py:239,276`、`finding_disposition.py:275,319,352`、`mission_persistence/disposition_evidence.py:12`。

公開実行・recover/cancel・host pin/action許可の配線はIssue1007。子Aは上記carrierの純粋拒否/受理とtransaction保証をfixtureで検証し、実host接続を完了と呼ばない。子Bが正completionを有効にするまで、保存rejectedも未解決のままにする。

新たな保証変更・外部操作について、現時点でownerへ上げる真の未決事項はない。Issue1015の親tracking化と子Issue1019/1020への分割は、既存の再分割方針に沿って確定した。上記4点は既存保証を変えない実装上の仮決定として独立設計レビューに渡し、技術的反例が出た場合に修正する。実host・activation等の個別承認はこの計画の対象外で、今この時点の着手待ち事項にはしない。

次の推奨: この計画の分割と4契約を新規セッション設計レビューへ渡す。accepted後に保存子Issue1019からTDDを開始する。この計画にソース・testの変更は含めない。設計検討段階のためテストは実行していない。
