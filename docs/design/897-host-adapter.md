# 実host adapterの観測と実行管理

状態:実装前の設計案。実host受入はblocked。
対象:[実host adapterと最小probeのIssue897](https://github.com/tackeyy/mission/issues/897)。
基点:[独立レビュー契約§3](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/docs/design/689-fresh-review-receipt.md#3-runtime-adapter-の登録と観測)。
fixture成功を実hostの独立性・入力受領・能力強制の観測へ昇格させない。

## 1. 観測済みと残条件

[許可済み最小probeの記録](https://github.com/tackeyy/mission/issues/897#issuecomment-6108192772)はCLI版2.1.296、exit0、success、1ターン、native initのsession ID、tools=[]を観測した。
API相当推定額は0.0070355USDで、実請求額ではない。費用設定は推定停止閾値であり厳密な請求上限ではない。
入力受領digest、親との会話context分離、要求能力・全予算の強制は未観測。adapterは未実装・未登録。
追加probe・SDK install・auth/settings変更・hardening解除はこの設計案の作成では実行しない。

## 2. 構成と信頼境界

新しいmanaged TypeScript SDK parent query、限定in-process MCP、独立guardian/watchdog、Python登録adapter、reviewer CLIを使う案とする。
Missionはstate・予算・receipt検証・完了判定を所有し、adapterはhost観測と実行だけを担当する。
信頼対象はOSユーザー、明示登録adapterと依存、SDK/CLI、guardian/watchdog。モデル出力・MCP引数・外部入力は信頼しない。
同uidの悪意あるprocessからの隔離は保証しない。socket権限/capabilityは同uid隔離やidentityの証明ではない。
既存5method protocolと閉じた5field registryを維持し、既定無効・利用者の明示登録を要する。個人providerや設定を既定に含めない。

## 3. 親identityと新しい会話context

parent queryごとのclosureでnative SessionStart.session_idを保持し、同queryのsystem/init IDとの一致後だけMission MCPを使用可能にする。
SessionStart内でinitを待たず保存して戻り、queryイベント処理側で一致を確定する。欠落・重複・別query混入は拒否する。
caller引数・env・PID・任意file・モデル出力から親identityを採用せず、既存任意sessionを事後認定しない。
[SDK hooks仕様](https://code.claude.com/docs/en/agent-sdk/hooks)と[session仕様](https://code.claude.com/docs/en/agent-sdk/sessions)に基づく結合案で、実hostの結合は未観測。

会話履歴の非継承と、起動時の共通policy/context読込みを区別する。
公式session仕様は新queryで新sessionを作り、continue/resumeで既存履歴を再開し、forkでは新IDへ履歴をコピーすると定める。新ID単独ではfreshの証明にならない。
採用案は新規one-shot childの起動経路をadapterが所有し、resume/continue/fork・parent history/transcript転送を拒否した事実と、native startup/initの別identityを束縛すること。
[SessionStart仕様](https://code.claude.com/docs/en/hooks#sessionstart)はstartup/resume/forkを区別する。startupだけを受付条件とする。
既存契約はglobal policy・hook・memoryの全除外を明記していないため、ここでは親の会話履歴を継承しない境界と解釈する。この解釈も設計レビュー対象である。
[共通instructions/memory](https://code.claude.com/docs/en/memory)やhookのadditionalContextは起動時に入ることがあり、それらの不存在は主張しない。
既存hardening/hooksを維持し、--bare/--safe-mode・設定sourceの除外で回避しない。--restrictedも全context除外証明にはしない。
new startup・起動経路・履歴非転送をhost由来の証拠へ束縛できなければprelaunch blocked。未確認をfresh宣言で埋めない。

parent builtin toolsは空、限定Mission MCPだけを公開する。availabilityとpermissionを別に制限し、allowedTools指定だけで未列挙toolが消えると仮定しない。
[MCP custom tools仕様](https://code.claude.com/docs/en/agent-sdk/custom-tools)をSDK版へ照合する。MCP引数は登録済みrequest refと操作種別だけとする。
任意command/env/cwd/identity/receiptをモデルから受け付けない。

## 4. IPCとadapter能力

guardianはquery closureで確定したparent identity、boot ID、operation、request digestから短命operation capabilityを発行する。
private local socketはowner専用directoryで保護し、capability/socket情報をモデル・argv・公開artifact・ログへ出さない。
messageはschema/boot/method/operation/request digest/nonceを閉じて検査し、duplicate key・未知field・過大入力を拒否する。
capabilityは認証用であり親identityの出所ではない。

| method | 契約 |
|---|---|
| observe_parent | query closureの確定identityだけを返す |
| launch | 保存request/dispatch/pin/fenceを照合し同operationを最大1回起動 |
| collect | 元childの観測と結果だけを取得 |
| cancel | 元childだけ停止し未確認を成功にしない |
| recover | 元operationの保存結果またはunknown。再launchしない |

期限切れ・別boot/requestのcapabilityは拒否する。応答喪失後の再要求は保存済み結果かunknownで、二重起動しない。
既存pinはadapter moduleのbytesを固定する保証で、依存やhostの誠実性までは保証しない。

## 5. callbackとchildの寿命

[既存callback transport](https://github.com/tackeyy/mission/blob/3cd23821512711d216e83be7a17ffe44f0fa5751/skills/mission/lib/fresh_review_host.py)はcallbackのprocess groupをcleanupする。そのgroupからreviewerを起動しない。
guardian/watchdogをcallback外で先に起動しreviewerのprocess custodyを所有させる。
request/snapshotはcallback終了前にguardian private領域へ安定読取・digest照合付きでcopyする。元temporary領域へ依存しない。
親生存は実query ownerとの専用pipeで観測し、callback終了を親EOFと解釈しない。

| 事象 | 処理 |
|---|---|
| callback正常終了 | childを維持し後続collect/recoverを許可 |
| 親EOF/query終了 | guardianが元childを停止 |
| guardian終了/期限 | 独立watchdogが元childを停止 |
| watchdog終了 | guardianのcustodyから停止可能なら実行、不明ならunknown |
| 両監視喪失/再起動 | journalだけで停止を主張せず再launchしない |
| TERM後残存 | KILLしgroup終了確認後だけ停止確定 |

停止対象は起動から所有する元process groupだけ。PID/PGID/開始時刻だけを再利用排除の強い証拠にしない。
watchdog故障時のcustody移譲とgroup終了確認は未実装で、model-free実process fixtureで成立しない故障経路はunknownとする。
停止不明はabandoned-unknownと既存budget holdへ写像する。receipt公開失敗・親終了から資源解放を推定しない。

## 6. 実入力受領と上限

canonical packetを単一user textとして送り、固定system prompt/制御設定は別に版とdigestを束縛する。
received_input_digestは実hostのraw user acknowledgmentから抽出したtextのUTF-8 bytesだけから計算する。JSON envelope全体や再整形したJSONを使わない。
session/message ID、単一text、順序、一回性を照合し、Unicode/改行/escape変換後の抽出bytesがrequest bytesと違えば拒否する。
[CLI仕様](https://code.claude.com/docs/en/cli-reference)の--replay-user-messagesはstream-json入出力でstdin ackを返すが、実hostのbytes対応は未観測。
送信buffer、SDK引数、モデルhash回答から受領digestを作らない。child SDKのackも未観測のため初期案はCLIとする。

childは実行tool/MCP/subagent/追加入力/再実行を許可しない。初期対応はallowed_tools=[]だけで、他要求は未起動blocked。
現schemaの予算最小値1を維持しmax_tool_calls=1/max_replays=1とし、実能力はtool0/replay0へ制限する。
packetは起動前、outputは読取中、wall timeは外部watchdogで制限する。stream envelope/ackにも別の累積bytes上限を置く。
起動前に証明できない能力はblocked。起動後のack欠落/identity不一致/監視喪失は未起動扱いへ戻さず停止・unknownへ進む。
launch receiptは入力・identity・能力・予算の実観測が揃った場合だけ発行する。

## 7. 検証と追加probe案

設計レビュー→model-free fixture→不足条件再評価→具体的追加probe申請の順とする。現時点で追加実行の許可はない。
fixtureはidentity交差、IPC改ざん/再送、callback終了後のchild生存、監視故障、PID再利用、partial/duplicate/過大ack、Unicode、output超過、未登録拒否を検出する。
偽hostの自己申告を受け入れるテストだけで完了にせず、fixture由来と実host由来を分ける。

| probe | 観測 | 実行条件 |
|---|---|---|
| A | parent1回+child1回。native親結合、別startup、raw ack bytes、tool制限 | 設計レビュー/fixture後に個別申請 |
| B | parent1回+child1回。callback終了後の寿命、指定crash/recover | A成功後に別承認。別故障を自動追加しない |

入力は固定synthetic packet。query45秒/probe全体90秒/TERM→KILL cleanup最大3秒を申請条件とする。
費用設定はparent0.05USD+child0.05USD、A/B合計0.20USDの推定停止閾値であり、厳密な請求capではなく超過しうる。
失敗や自然終了で狙った故障を観測できなければ未観測と記録し、自動再試行しない。

## 8. 証跡と完了

公開artifactは版、source/probe digest、匿名化identity、観測可否、bytes、時間、推定費用だけとする。home path/capability/credential/raw transcriptを含めない。
必要最小限のhost events/ack/custody証跡はアクセス制限された非公開領域へ保存し、値非ログとTTL/cleanupを設計レビューで確定する。
残条件はcontext境界の採否と実host結合、raw ack bytes、監視故障custody、能力/予算強制、SDK版/API適合である。
既定無効adapter、registry未登録拒否、全ACの実host観測、所定レビュー/Checker/CIまで完了としない。未観測が残ればIssue897をblockedのまま維持する。
