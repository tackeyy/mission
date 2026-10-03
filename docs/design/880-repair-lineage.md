# 反例から修復・再検証までを受入条件別に追跡する設計

決定案: 既存 findings を置換せず、型付き修復 projection を追加する。同じ反例の旧候補での失敗と、新候補での成功を結ぶ receipt だけが修復を確定する。必須義務・禁止副作用に関係する未解決 finding は severity にかかわらず strict completion を止める。

対象: [Issue 880: 反例から修復と再検証を追跡する](https://github.com/tackeyy/mission/issues/880)。本書は設計のみ。実装、テスト追加、入力値の列挙、Issue 起票、Git 操作による公開は行わない。
照合 head: `8f40f542f8fed8660728234d41295ee3212deeab`。開始時の HEAD とローカル `origin/main` はこの値で一致した。以降の「現状」はこの固定 head のソースを指す。GitHub in-flight 照合は接続エラー、exit 1。remote の最新状態、open PR、CI は **UNKNOWN**。取得失敗を「PR なし」と解釈しない。

引用 [S*] / [T*] / [D*] は末尾の固定 head の `file:line` とリンクへ結ぶ。テスト引用は保持するソース上の契約であり、本ステップのテスト実行結果ではない。

## 1. 現状と依存境界

| 照合した現状 | E の選択 |
|---|---|
| A は requirement の全 codepoint 区間、criterion の required・禁止副作用・command ID を保持し、imported coverage は pending のみ。canonical contract digest は coverage を含む。[skills/mission/lib/acceptance_contract.py:66-155][S01] | contract を修復状態の保存先にしない。criterion の意味・義務分類・expected を修復の途中で緩めない |
| B は凍結した通常 command の replay 定義を選び、typed `repro_input` を materialize して receipt を返す。receipt 自体には command_id がなく、definition digest・candidate・repro digest がある。[skills/mission/lib/mission_application/verification_execution.py:85-167][S02][skills/mission/lib/mission_application/verification_runner.py:147-420][S03] | B の実行 primitive と receipt shape を再利用し、E envelope で command ID と lineage を結ぶ。B receipt schema を変更しない |
| C/D0 の pure gate は key presence と shared frozen-command validator を使い、required の最新 receipt と候補を確認し、最後に fresh-review-pending を返す。force より先にこの gate が走る。[skills/mission/lib/mission_kernel/transitions.py:985-1115][S04] | D3 の gate 契約を拡張する。現在の main に D3 の成功経路があるとは扱わない |
| D0 は `frozen_verifier_commands` と純粋な command/link validator を持ち、lookup/hash/capture 前に shape を閉じる。[skills/mission/lib/acceptance_contract.py:158-200][S05] | E の prepare/run/commit/completion もここを共有し、独自の緩い policy parser を作らない |
| 現 findings は OpenFinding / ResolvedFinding と generation・証拠参照を持つ。v4 は LegacyFindingsUnloaded、closed v5 は MaterializedFindings。[skills/mission/lib/mission_kernel/model.py:352-487][S06][skills/mission/lib/mission_kernel/codec_v5.py:561-584][S07][skills/mission/lib/mission_kernel/codec_v4.py:667-700][S26] | 既存 union は維持。新しい状態は別 projection に置き、既存 resolved を E の verified と読み替えない |

**未merge依存（依頼条件）**: D1–D3 の実装には依存するが、本 head で存在を前提にしない。D の設計が定める `FreshReviewProjection`、request/launch/terminal receipts、open finding の原子的導入、実効 coverage、最新 attempt の選択、completion reason codes を契約として参照する。[docs/design/689-fresh-review-receipt.md:11-128][D01][docs/design/689-fresh-review-receipt.md:132-237][D02][docs/design/689-fresh-review-receipt.md:239-273][D03]
実装は D3 merge 後の最新 main で型・保存キー・API を再照合する。以下の D 型を利用する箇所は全てこの未merge依存を持つ。実 host adapter の利用可能性は **UNKNOWN**。D の fixture による公開 CLI 経路と実 host の保証を区別する。[docs/design/689-fresh-review-receipt.md:106-130][D04]

## 2. Finding lineage と保存場所

決定: `MissionState.repair: RepairProjection` を追加し、保存 schema を `mission-repair-lineage/1` とする。v4 document の予約キー `repair_lineage` と closed v5 の `extensions.repair_lineage` を同じ閉じた decoder で復元する。v5 top-level schema に新しい JSON field は増やさない。現 MissionState は a4 projection を持ち、v4/v5 codec がそれぞれ passthrough/extensions から復元・投影している。[skills/mission/lib/mission_kernel/model.py:352-487][S06][skills/mission/lib/mission_kernel/codec_v5.py:561-584][S07][skills/mission/lib/mission_kernel/codec_v4.py:667-700][S26][skills/mission/lib/mission_kernel/codec_v4.py:862-925][S27][skills/mission/lib/mission_kernel/codec_v5.py:721-765][S28]

| 型 | 必須内容・決定 |
|---|---|
| `FindingLineage` | `lineage_id`, `criterion_id`, `requirement_ids`, `prohibited_side_effect_ids`, severity、元の D finding への `FreshFindingRef`、original terminal receipt ref、original replay evidence ref、repro binding、introduced candidate、observations/attempts への append-only な ref・digest・状態（本文は effects 側。下記「容量と durable な記録」）、typed lifecycle |
| `FreshFindingRef` | mission/session、original request ID、terminal/output digest、D の local finding_id。D row を一意に指す。既存 FindingIdentity が実在する場合だけ optional link として持ち、D row を既存 review row と偽装しない |
| `ReproBinding` | 登録 replay の `command_id`、`verifier_definition_digest`、`repro_input: {artifact_kind, content}` の content-addressed ref、`repro_input_digest`、`repro_digest`、B の実行用 `runner_repro_digest` |
| `CandidateBinding` | D の canonical command/snapshot map とその digest、criterion/replay ごとの snapshot digest、contract/requirement/policy digest、iteration。introduced candidate は元 request の候補。repair candidate は attempt ごとに保存 |
| `RepairAttempt` | stable attempt ID、lineage ID、before candidate、repair candidate（変更観測後）、plan/evidence ref、通常 receipt の baseline refs、実行 intent、reverification receipt refs、result/reason、比較履歴 refs |
| `ReverificationReceipt` | `mission-repair-reverification/1`。finding/attempt/criterion/repro/command binding、候補 map、B receipt の immutable ref/digest、実行 operation/fence、公開 operation/fence、観測結果、公開順序 |
| `DispositionReceipt` | §3 の独立判定。修復 receipt と別型・別 collection。passed replay の代用にはならない |

stable ID は kernel が canonical な mission/session + original request ID + local finding_id + criterion_id の組から domain-separated SHA-256 で導出する。summary、severity、iteration、現在候補を ID に使わない。同一 origin の再取込は同一 ID・同一内容だけを許す。別 reviewer の同じ反例は別 origin を保存し、明示的 alias link で関連付ける。文字列類似による自動統合はしない。

`repro_input_digest` は canonical UTF-8 JSON の SHA-256、`repro_digest` は canonical `{command_id, repro_input_digest}` の domain-separated SHA-256 とする。B の `repro_input_digest` は artifact_kind・relative_path・content を NUL 区切りで hash しているので、こちらは `runner_repro_digest` として別に保存する。[skills/mission/lib/mission_application/verification_runner.py:147-420][S03]
kernel は保存した typed input と凍結 replay.relative_path から B digest を再導出し、両方を照合する。command ID だけが同じでも definition/policy/contract が違えば非互換。finding の repro を後から上書きしない。別入力への縮約は別 observation とリンクを追加し、元の反例を解決する証拠には使わない。

original replay が表現不能・未登録・blocked の場合、original replay ref は理由付き absent variant とする。lineage と open obligation を保存するが、失敗→成功の修復を主張できない。D の blocked/open 保持規則を維持する。[docs/design/689-fresh-review-receipt.md:132-237][D02]

**既存 findings との関係**: v4 の legacy review decoder は自己申告 status にかかわらず OpenFinding に正規化し、元 payload を保存する。closed v5 の resolved は prior_identity と resolution_evidence_ref を要求するが、E の同一反例 replay を検査する型ではない。[skills/mission/lib/mission_kernel/codec_v4.py:714-742][S08][skills/mission/lib/mission_kernel/codec_v5.py:390-453][S29]
E はこの保証を残し、E に link のない legacy findings の migration/自動criterion割当は行わない。E の権威ある状態は repair projection と全 D origins。UI は既存 finding と lineage の状態を別 field で描画する。E から ResolvedFinding を新規生成せず、汎用 resolve-finding command も作らない。既存 open row が表示上残っていても、D3 gate の「未解決」の導出は E の有効な resolution を参照する。legacy score の open_high は別 gate として残る。[skills/mission/lib/mission_kernel/transitions.py:985-1115][S04][skills/mission/tests/test_issue500_codec_v5.py:513-526][T01]

**codec と writer**: キー欠落だけが empty projection。null、未知 schema/field、破損 ref、重複 ID、順序逆転、origin/receipt 不一致は理由付き拒否。typed projection と保存面が異なる場合 encoder も拒否する。state bytes の既存上限を緩めず、[skills/mission/lib/mission_kernel/json_codec.py:12-12][S38] 超過は拒否し履歴を切り捨てない（履歴本文の置き場と終端の予約は直後の「容量と durable な記録」の決定に従う）。
**決定（容量と durable な記録）**: state に置くのは上限付きの要約と参照だけとする。lineage ごとの observations/attempts の本文、比較履歴、`mission-repair-event/1` の event 本文、baseline の manifest は、D と同じ content-addressed な immutable effects（公開 commit と同じ effects commit で保存）に置き、state の lineage は各 attempt・event の ref・digest・状態・順序だけを持つ。
終端を必ず記録できるよう、`repair begin`（と E3 の disposition 開始）は、開始時点で「その attempt の終端要約と終端 event ref を追記した後の state bytes」が既存上限（[skills/mission/lib/mission_kernel/json_codec.py:12-12][S38]）から固定の予約分（終端要約 1 件分の上限サイズ）を引いた値以下であることを kernel が確かめ、満たさなければ command 単位で拒否する（state を変えず理由コード `repair-state-capacity-exhausted`）。開始済みの attempt は、この予約分の中で `verified`/`failed`/`blocked`（容量起因を含む）のいずれかの終端要約を必ず追記できる。effects への保存が容量・IO で失敗した場合は、予約分の中で `blocked` 終端（理由付き）を記録し、過去の証拠や履歴を切り捨てない。予約分が使えない状態（上限付近で開始済みの attempt が無い）では新しい attempt を開始できないことを、status で理由付きに表示する。
新予約キーとその descendants は generic set、init/reinit、compatibility delta、review/score import、specialist evidence、downgrade の authority 注入から保護する。現 generic-set 専用 field と specialist authority 遮断を拡張する。[skills/mission/lib/mission_kernel/commands.py:397-455][S09][skills/mission/lib/mission_kernel/transitions.py:1837-1861][S30]
全 D terminal finding を同じ commit の reducer で lineage に導入する（未merge D writer への追加）。E 導入前の保存済み D rows は専用初回 mutation で決定的に取り込む。読取や completion で projection が欠落していても **D の全 origin と union して open とみなす**。最新 review だけから再構成しない。不正な projection を空とみなして通す経路を作らない。

## 3. Typed reducer と解決の権威

状態は `open | repairing | verified | deferred | rejected` の閉じた variant。各変更は generation を増やし、旧状態と receipt は残す。`stale` は証拠の有効性であり、履歴を消す terminal state ではない。

| 遷移 | 必要な証拠・効果 |
|---|---|
| D origin → open | original request/terminal/output に束縛された finding。元の反例・候補・関係する義務を保存 |
| open/deferred/実効open → repairing | `BeginFindingRepair`。現 candidate を再取得し、before と baseline refs、plan ref、operation identity を保存。plan は提案であり成功証拠ではない |
| repairing → verified | `CommitFindingReverification`。下の全条件を満たす新しい replay receipt と effect の原子的公開のみ |
| repairing → open | replay failed/blocked、unsupported、予算切れ、候補変更、実行不明。receipt/理由を attempt に残す。失敗を成功に変換しない |
| open/repairing → deferred | 理由と独立 disposition receipt 必須。対応保留を記録するだけで、必須違反は遮断を続ける |
| open/repairing/deferred → rejected | 独立 disposition receipt 必須。false-positive、重複先、非義務との関係を具体的に束縛。正当な義務違反の受容を意味しない |
| verified/rejected/deferred → 実効open | §4 の stale、判定 binding 不一致、または同じ反例の新しい失敗。履歴と元の ID を保持し、再修復可能 |

verified の必須条件:

1. 元反例の実行が同じ criterion/command/typed repro で `failed` と観測されている。blocked、自己申告 actual、未実行を失敗と数えない。D の original replay が未確認なら、まず保存された introduced snapshot で baseline replay を観測する。snapshot が保存されていなければ UNKNOWN/open のまま。
2. 新候補はその失敗候補から **replay 対象 snapshot digest が変化**している。iteration、HEAD、説明文、別 command の map 変更だけでは修復扱いにしない。最新の同一反例 failed receipt を baseline とし、同じ bytes の失敗後の成功は観測履歴には残すが repair_verified にしない。
3. 新候補で、同じ criterion、元 `repro_digest`、登録 replay.command_id、definition/policy/contract、B runner digest が一致する **新規の passed replay receipt** がある。通常 verifier の成功で代替しない。caller の `resolved=true` / `status=verified` を受け付けない。
4. prepare、実行直前、実行後、公開直前の候補観測が一致し、公開時の現 operation/lease/fence を通る。receipt は immutable artifact ref と同じ state commit に束縛される。過去 operation の receipt を新 attempt として流用しない。
5. 当該 attempt に後続 failed/blocked/unknown replay がない。最新実行の失敗から古い passed に戻らない。repair candidate の変更後は §4 に従う。

B の runner が passed を導出する処理を再利用する。test command では fresh report と正の executed_count を含む成功条件があり、timeout/toolchain/candidate drift は blocked となる。[skills/mission/lib/mission_application/verification_runner.py:147-420][S03] kernel は receipt の閉じた shape・binding と producer 専用 carrier を検査する。既存 B receipt の shape validator だけで実行出所が証明されたとは扱わない。[skills/mission/lib/mission_application/verification_execution.py:85-167][S02]

**disposition の独立性**: `mission-finding-disposition-request/1` と terminal `mission-finding-disposition/1` を E に追加する。D runtime adapter の登録/pin、parent/child/context/input 受領観測、一回消費、terminal variant、fence の契約を再利用する（未merge依存）。E専用の追加protocol `FindingDispositionRuntimeAdapter` は同じ観測・pin・回収規律を使い、`launch_disposition` を持つ。Dの既存search adapterがこの能力を提供すると仮定せず、未対応なら起動前blockedとする。Dのrequest/output schemaやregistryの閉じたfield集合を緩めない。D search/coverage receipt に disposition を後付けしない。[docs/design/689-fresh-review-receipt.md:11-128][D01][docs/design/689-fresh-review-receipt.md:132-237][D02]
immutable packet は元要求/ledger/criterion/禁止副作用、元 finding、原 replay の観測、対象候補を含む。実装者の棄却説明を判定済み事実として渡さない。kernel が adapter 観測から independence を導出し、caller 指定の判定者名やモデルの自己申告では許可しない。
receipt は origin/lineage/repro/contract/candidate、判断対象の evidence refs、理由コードと理由、scope decision、重複先 ID（該当時）、request/launch/output refs、dispatch/commit fence を束縛する。adapter が使えなければ disposition は blocked で finding は open。独立判定も意味上の正しさを数学的に証明しないという D の trust-root 限界を継承する。[docs/design/689-fresh-review-receipt.md:106-130][D04]

`deferred` は全て未解決。`rejected` が strict blocker を外せるのは、有効な独立証拠が「この finding は当該義務/禁止副作用の違反ではない」と確認した場合だけ。重複棄却は canonical target が未解決なら遮断を引き継ぐ。循環 alias は拒否。accepted-risk、予算不足、severity 引下げ、再現失敗だけでは required 違反を棄却できない。
元の義務リンクは履歴に残す。coverage の supplemental open obligation をこの判定で削除しない。coverage を変えるには D の新しい全体 attempt が必要。削除、最新 review の行欠落、supersede、score 更新は resolution event を生まない。[docs/design/689-fresh-review-receipt.md:132-237][D02][docs/design/689-fresh-review-receipt.md:239-273][D03]

## 4. 候補変更・staleness・resume

決定: E 初版は依存関係を **unknown** とし、候補 map の変更後は全 required criteria の通常検証と、全ての既存 verified finding の replay を再実行する。依存 graph の新 schema や caller の `unaffected` 指定を導入しない。精密な影響解析による再利用は後続設計とする。

現 B snapshot は全 tracked bytes、declared_untracked、declared local input を含み、通常 command ごとに capture する。[skills/mission/lib/mission_application/verification_runner.py:147-420][S03][skills/mission/lib/mission_application/review.py:30-101][S10] E は D の通常/replay command ごとの候補 map を使う（未merge依存）。HEAD 不変・dirty bytes 変更も候補変更になる。[docs/design/689-fresh-review-receipt.md:11-128][D01]

| 変化 | 必ず無効化する現在の主張 |
|---|---|
| candidate map が変化 | 通常 receipt の現在候補への有効性、verified finding の現在候補への有効性、candidate-bound disposition、D の現在候補に対する search/coverage。元 receipt は歴史として残す |
| 対象 replay snapshot/definition/input が不一致 | 当該 finding の reverification。別 repro の成功は一切流用しない |
| contract/requirement/policy/toolchain binding が変化・読取不能 | 関連する全証拠。凍結 contract を差替えず理由付き blocked。migration で成功を付与しない |
| 新しい D attempt が準備・実行・失敗 | D の最新 attempt 規則をそのまま適用。E の修復成功が古い search/coverage の fallback を許可しない |

stale は status/completion の **読取時にも現候補との差から導出**する。mutation がなかったから valid とはしない。次の mutation は invalidation event を一度だけ append し、実効open を durable に反映する。候補が一度変化したと観測されたら、元 bytes に戻しただけでは invalidation を消さず、再実行が必要。D も現在候補に束縛された新 attempt を要する。過去候補でverifiedだった事実と現在未検証を表示上分離する。

既存 resume/reactivate は control を更新する reducer である。[skills/mission/lib/mission_kernel/transitions.py:869-947][S11] E はそれを第二の lifecycle に置き換えず、repair projection と保留・未実行 attempt・履歴をそのまま codec から復元する。resume 時に候補を再観測し、unrepaired/unverified と stale を返す。iteration 増加で collection を空にしない。

reverification 実行は短い fenced intent 保存→lock を放して B 実行→候補再取得→fenced terminal publication。execution intent は D と同じ `dispatch-unknown/running/terminal` の共有規律で、finding lifecycle とは別の実行観測とする。中断後の unknown を自動実行し直さない。exact 保存済み結果を回収できれば current fence で reconcile、不明なら open/blocked で終端化し、新しい operation で再実行する。旧 writer の遅着は拒否。公開後・応答前の停止は同一 operation の保存結果を返す。
既存 operation replay が歴史的 state を返す仕組み、fenced lease の旧 token 拒否、transition/effects の公開を再利用する。[skills/mission/lib/mission_application/evidence.py:199-250][S12][skills/mission/lib/mission_persistence/legacy_v4.py:714-773][S13][skills/mission/lib/mission_persistence/fenced_commit.py:1465-1500][S36] v4 flat は既存 lock/lease/effect publication を維持し、v5 と同じ世代CASを持つと主張しない。公開経路は v4 flat と v5 container/v4 payload、closed v5 は typed codec/pure reducer に限定し、公開bridgeを新設しない。[skills/mission/lib/mission_persistence/legacy_v4.py:714-773][S13][skills/mission/lib/mission_persistence/fenced_commit.py:1465-1500][S36][docs/design/689-fresh-review-receipt.md:239-273][D03]

## 5. Completion gate

決定: D3 の条件5を pure `effective_unresolved_findings` に接続する。D が導入した全 finding origins と E lineage を照合し、対応 lineage 欠落、open、repairing、deferred、stale verified、無効 disposition は未解決。required obligation または禁止副作用に関連すれば **High/Medium/Low 全て** `acceptance-unresolved-finding`。severity は優先順位と報告にだけ使う。[docs/design/689-fresh-review-receipt.md:239-273][D03]

有効な verified または §3 の独立 rejected がある finding だけを除ける。関係の shape が不正なら拒否、関係が不明なら open obligation として保守的に遮断する。optional criterion でも required requirement を指す・禁止副作用に関係する場合は免除しない。元 ledger に context と記された義務の発見も D の supplemental open obligation を保持する。[docs/design/689-fresh-review-receipt.md:132-237][D02]

通常 receipt、fresh search、実効coverage、unresolved finding は独立した必要条件。repair replay を `verification_receipts` に足さず、D の専用 replay evidence と同じく repair collection に置く。[docs/design/689-fresh-review-receipt.md:132-237][D02] 通常検証の最新成功、新候補での fresh review と全体 coverage valid、open obligation ゼロ、既存 score/artifact/specialist gate がなお必要。imported coverage は pending のまま保持する。[docs/design/689-fresh-review-receipt.md:132-237][D02][docs/design/689-fresh-review-receipt.md:239-273][D03][skills/mission/lib/mission_kernel/transitions.py:985-1115][S04]

reason code の優先順は D の既定を維持し、fresh-review-missing/stale/pending/non-independent・coverage-open を acceptance-unresolved-finding より前に評価する。主理由が別でも status は全 blockers を返す。E 用の `repair-repro-mismatch`、`repair-candidate-not-new`、`repair-reverification-stale`、`repair-disposition-invalid`、`repair-replay-unsupported` 等は command 拒否/attempt理由であり、新しい completion 成功条件を作らない。
mark-passes、closeout（既 passes を含む）、force preflight と最終 kernel transition は同じ guard を使う。現 application は pure preflight の後に force approval を呼び、closeout shortcut は contract presence で拒否する。[skills/mission/lib/mission_application/review.py:30-101][S10][skills/mission/lib/mission_application/review.py:503-523][S31] E でもこの順を守る。repair completion は mission の terminal flags を直接設定しない。

## 6. 修復・回帰・改悪履歴と外部 metrics

決定: 修復による改善と他出力の悪化を別々に残す。finding が verified になっても、他の criterion が回帰した可能性を消さない。I の [Issue 884: 全割当から検出・修復・改悪を集計](https://github.com/tackeyy/mission/issues/884) は独立評価と全割当の分母を持ち、E は観測履歴を供給する。

attempt 開始前に「最後に有効な通常 passed receipt の ref」だけでなく、その候補 map/manifest・出力artifact refをbaselineに固定する。after では全 required criterion と関連consumerの観測 receipt を列挙する。consumer は criterion/凍結 verifier が宣言した範囲のみ。未登録consumerはUNKNOWN、観測していない範囲を無回帰と書かない。保存できないbaselineは absent理由を保持し、後から現在の出力で埋めない。
before/after の candidate、command/definition/policy/toolchain、repro、観測 status・exit・count・output digest、通常/replay/evaluator evidence refs、changed artifact manifest、比較可否を保持する。B は全文出力を返さずdigest/countを返すため、digestだけから意味上の正誤・不要変更を決定できない。[skills/mission/lib/mission_application/verification_runner.py:147-420][S03]
read-only snapshot/artifact refs は D の入力・候補保存を再利用し、E の比較に必要なbaselineをimmutable effectsとして保留する（未merge依存）。public head が参照する lineage/event から reachable な証拠を保存対象とし、GCで孤立扱いしない。容量超過はblockedとして記録し、過去証拠を黙って落とさない。

| 比較 | E が記録するもの |
|---|---|
| 同じ反例 failed→新候補passed | receipt pair と `repair-verified`。他の出力が正しいという主張は付けない |
| 同じ条件の通常/既検証replay passed→failed | `regression-observed` と before/after refs。再発なら元lineageを実効open、新反例ならDが新originを導入 |
| passed→blocked/未実行/候補不一致 | `comparison-unmeasured`。回帰ゼロとも回帰確定とも数えない |
| definition/policy/toolchain/reproが異なる | `comparison-incomparable`。比較条件の変更を記録 |
| 同じ候補のfailed→passed | `inconsistent-observation`。修復件数へ入れない |
| 正しかった出力の変更 | manifestの差と独立 evaluator のbefore/after参照を渡す。不要変更・改悪の判定はI。output digestの差だけでは判定しない |

外部用 schema は `mission-repair-event/1`。`event_id`（lineage/attempt/transition generation/type の domain-separated digest）、mission/session、criterion、lineage/attempt、origin receipt、before/after candidate refs、repro、receipt/disposition/comparison refs、outcome/reason、observed wall time・実行回数、commit順序を持つ。実測不能な時間・costはnullと理由。raw transcript、credential、私用パス、無制限出力は含めない。
event種類は finding-introduced、repair-started、reverification-observed、repair-verified、verification-invalidated、finding-disposition、regression-observed、comparison-unmeasured/incomparable、inconsistent-observation。events はその状態遷移と同じeffects commitで保存する。replay再応答は同じevent IDを返すだけで追記しない。
`repair events --after <cursor>` はimmutable eventの順序付き読取/export。外部送信やmetrics判定をkernelに入れず、Iがevent IDで重複排除し、未試行・blocked・interruptedを含む割当とjoinする。Eの観測だけで全割当のrepair率や品質倍率を算出しない。現 planning_provider_metrics はplanning専用schema/母集団を持つため、そこへrepair件数を混ぜない。[skills/mission/lib/planning_provider_metrics.py:1-34][S14]

## 7. 再計画と scoring provenance の再利用

現 `mission_application/retry_plan.py` は **ContextManifestRetryPlan**。now/iteration/publication_pathと一度生成したoperation IDを固定するデータ契約で、finding repairの状態機械ではない。`run_with_base_retry` は公開前のbase移動だけを予算内でretryする。[skills/mission/lib/mission_application/retry_plan.py:53-170][S15][skills/mission/lib/mission_persistence/retry_loop.py:18-51][S32]
E はcanonical intent/operation identity、base-CAS retry、historical replay responseを再利用する。ContextManifestRetryPlanを継承してrepair planと呼ばず、replay processそのものをCAS retry callbackで再実行しない。reverificationは公開済みintentに対して一度観測し、publicationのやり直しと実行のやり直しを分ける。Bの既存公開 `verification run` が同一operationでも再実行して差分receiptを拒否する契約は変更しない。[skills/mission/tests/test_issue878_verification_runner.py:235-342][T02]

修復計画は既存mission plan/executor handoffのbounded stepとして実行し、lineage/attempt IDとplan/evidence refを関連付ける。criticの新scope有無は現review集計で別の観測値として使われているが、修復の成功権限ではない。[skills/mission/lib/mission_application/review_aggregation.py:327-333][S16] Eから第二の計画→実装→採点ループを起動しない。executor completeはreverificationを要求する次actionを提示するだけ。
scoring provenanceはsource evidenceとrevision scopeを検査し、保存bytesからscore claimを再導出している。[skills/mission/lib/scoring_provenance.py:42-73][S17][skills/mission/lib/scoring_provenance.py:159-221][S33] その参照/immutable artifact方式を使うが、score上昇、open_high=0、authoritative score、force approvalでlineageを解決しない。Eはscoreを書き換えず、最新candidateで既存score provenanceを再取得する手順へ戻す。

## 8. 公開command・層・保守面

全て追加予定。現CLIで利用できると主張しない。

| command | kernel / application の責務 |
|---|---|
| `repair begin --finding <id> --plan-ref <ref>` | applicationが現候補/baselineを捕捉し、`BeginFindingRepair`がattemptとintentを保存。任意status入力なし |
| `repair reverify --attempt <id>` | 保存reproを読取、B primitiveで凍結replayを実行し、`CommitFindingReverification`。repro/command/receiptをcallerに差替えさせない |
| `repair reconcile --attempt <id>` | 保存intentの実行状態を観測し、exact結果の回収か理由付き未解決終端。再spawnの別名にしない |
| `repair disposition prepare/run/reconcile --finding <id>` | 独立判定専用request/receiptとfenced terminal。prepareのdefer/reject希望は要求であり判定結果ではない |
| `repair status [--finding <id>]` | 保存状態、実効状態、stale、latest attempt、未解決理由、通常/fresh/repair receipt参照を分けて表示 |
| `repair events --after <cursor>` | committed historyの読取。metrics分類や外部送信を行わない |

kernelは閉じた型・decoder・reducer・binding・effective unresolved導出、applicationは入力/候補構築・B実行・D独立adapter・intent/recovery/publication、adapterはprocess/context/出力の観測だけ。CLIはtyped request構築、1 use case呼出し、描画と終了コード変換。kernelからfilesystem/applicationを呼ばない。
新kernel commandはcommands union/type encoder・transition registry・effect binding・operation replay復元を一緒に配線する。既存commands/transitionのreceipt writerに同じ規律がある。[skills/mission/lib/mission_application/verification_execution.py:85-167][S02][skills/mission/lib/mission_kernel/transitions.py:1448-1482][S18] 新しいdatabase/transaction systemは作らず、prepared transition operationと現repositoryを使う。[skills/mission/lib/mission_application/evidence.py:199-250][S12][skills/mission/lib/mission_persistence/legacy_v4.py:714-773][S13][skills/mission/lib/mission_persistence/fenced_commit.py:1465-1500][S36]

inventory: `command_owners.py`で修復mutationをA2.review、status/eventsをR1.queryへ登録し、review owner表とpublic schema/help/dispatchを一致させる。C2専用集合への対象追加は実装時のconsumerを照合する。現direct writer allowlistは空のまま維持。[skills/mission/lib/mission_application/command_owners.py:26-94][S19]
recursive module inventoryは新kernel/application moduleを自動発見する。canonical/mirrorのimport・互換構文・byte equalityに入る。[skills/mission/lib/mission_python_inventory.py:102-170][S20][skills/mission/tests/test_python_module_inventory.py:95-103][S39] CLIのbranch/business logic/state writeを増やさず、thin-adapterのheadroom-free baselineとbase ratchetを維持する。[scripts/check-thin-adapter-ratchet.py:17-37][S21][skills/mission/tests/test_issue626_thin_adapter_guard.py:366-389][T03]
canonical `skills/`・`scripts/`の変更は配布mirrorへ同期する。tests/pytest.iniとsync script自身には現除外があり、fixtureはtests配下に置く。[skills/mission/tests/test_codex_wrapper_sync.py:14-61][S22] docs本書をmirrorへ複製する必要はない。
completion bypass inventoryのproducer/guard/全writer一覧をEの新規経路に更新し、repairを汎用set経由で触れないことを明記する。artifact hygiene/neutral vocabulary scannerはtracked files対象のため、未追跡の設計書を通常suiteが検査済みとはしない。[skills/mission/tests/test_artifact_hygiene.py:35-51][S23][skills/mission/tests/test_vendor_fingerprint.py:76-81][S37]

### 保持する既存テスト（入力値の追加設計ではない）

以下は削除・緩和しない。変更が必要なのはD3でcoverageの読取元と成功経路が変わる部分であり、拒否・原子的公開・legacy互換の保証は保持する。

| `skills/mission/tests/` 配下の file::test | 保持する保証と出典 |
|---|---|
| `test_issue500_v4_decoder.py::test_legacy_review_statuses_all_normalize_to_open_and_preserve_payload` | 自己申告resolvedをtyped解決にしない。[skills/mission/tests/test_issue500_v4_decoder.py:281-296][T04] |
| `test_issue500_codec_v5.py::test_v5_resolved_finding_requires_all_resolution_fields` / `test_v5_resolved_finding_rejects_invalid_resolution_authority` / `test_v5_resolved_document_round_trips_canonically` | 既存resolvedの証拠・generation・round-tripを保持し、Eのreducerと混同しない。[skills/mission/tests/test_issue500_codec_v5.py:290-338][T05] |
| `test_issue500_codec_v5.py::test_production_entrypoint_ast_graph_and_parser_have_no_v5_producer_route` | CLIからResolvedFinding生成・汎用resolve-findingを導入しない。[skills/mission/tests/test_issue500_codec_v5.py:513-526][T01] |
| `test_issue878_verification_runner.py::test_public_runner_binds_replay_input_to_frozen_replay_command_and_receipt` / `test_same_operation_retry_runs_the_verifier_again_and_rejects_a_different_receipt` | frozen replay/input binding、Bの既存retry semanticsを保持。[skills/mission/tests/test_issue878_verification_runner.py:235-342][T02] |
| `test_issue878_candidate_snapshot.py::test_snapshot_uses_dirty_tracked_bytes_and_fresh_materialization` / `test_snapshot_materializes_declared_local_input_and_rejects_target_collision` / `test_test_runner_uses_declared_junit_report_not_console_text` | HEADだけの候補判定、input衝突、console成功の誤認を防ぐ。[skills/mission/tests/test_issue878_candidate_snapshot.py:18-110][T06] |
| `test_issue877_acceptance_contract.py::test_preserves_unmapped_obligation_as_pending` / `test_contract_is_not_replaceable` | unmapped義務を落とさず、repairでcontractを差替えない。[skills/mission/tests/test_issue877_acceptance_contract.py:42-62][T07] |
| `test_issue879_completion_cli.py::test_public_completion_revalidates_latest_receipt` / `test_already_passed_contract_closeout_is_not_a_success_shortcut` / `test_valid_force_approval_cannot_override_acceptance` | latest/stale、既pass/forceによる迂回を拒否。D3成功経路へ拡張しても保持。[skills/mission/tests/test_issue879_completion_cli.py:486-704][T08] |
| `test_issue879_completion_cli.py::test_alternate_completion_and_evidence_writes_reject_atomically` / `test_reinitialization_cannot_remove_a_frozen_contract` / `test_codecs_keep_contract_and_receipt_evidence` / `test_contractless_completion_and_already_passed_closeout_remain_usable` | set/init/codecで権威を失わずlegacy成功を保持。[skills/mission/tests/test_issue879_completion_cli.py:486-704][T08] |
| `test_issue879_completion_cli.py::test_shared_validator_closes_live_and_frozen_command_fields_before_sets` / `test_runner_and_replay_reject_malformed_frozen_commands_atomically` | D0 shared validatorとlookup前拒否を保つ。[skills/mission/tests/test_issue879_completion_cli.py:335-416][T09] |
| `test_issue747_retry_loop.py::test_a_final_authority_move_is_not_retried` / `test_the_plan_identity_is_the_same_on_every_attempt` | stage後の再公開を防ぎ、CAS retryで別operationを作らない。[skills/mission/tests/test_issue747_retry_loop.py:71-125][T10] |
| `test_issue503_fenced_commit.py::test_same_operation_and_intent_returns_one_result_and_different_intent_rejects` / `test_head_replacement_is_the_crash_authority_boundary` | exactly-once publicationとcrash authority境界。[skills/mission/tests/test_issue503_fenced_commit.py:703-835][T11] |
| `test_command_inventory.py::test_all_parser_commands_have_exactly_one_declared_owner` / `test_c2_repository_commands_have_no_direct_legacy_session_writer_calls` | unowned command・direct writerの増殖を拒否。[skills/mission/tests/test_command_inventory.py:1732-1804][T12] |
| `test_issue626_thin_adapter_guard.py::test_repository_scan_matches_the_headroom_free_baseline_exactly` / `test_ratchet_allows_only_same_or_decreasing_baselines` / `test_python_module_inventory.py::test_recursive_inventory_compatibility_gate_imports_from_both_roots` / `test_codex_wrapper_sync.py::test_codex_wrapper_skills_match_canonical_tree` | CLI判断増殖・非互換・mirror不一致を防ぐ。[skills/mission/tests/test_issue626_thin_adapter_guard.py:366-389][T03][skills/mission/tests/test_codex_wrapper_sync.py:14-61][S22] |

D1–D3の保持対象テスト名は未mergeのため **UNKNOWN**。実装開始時にDのrequest/terminal一回消費・旧writer拒否・coverage最新attempt・unresolved finding保証の実名を追記し、同じ意味を維持する。入力値や新test名の列挙は本書の対象外。
CIはPython shards→`make test-shard`→tracked testsの選択で配線される。`test-e2e`は名称filterがあるため、それだけで全保証を確認したとはしない。[.github/workflows/ci.yml:106-119][S24][Makefile:51-59][S34][scripts/ci_shard_targets.py:33-51][S35] E実装ではshared reducer中心のRed→Green、公開CLIとrepository effect境界の検証、独立validator探索、レビュー/Checkerを行う。本設計で実行済みとはしない。新テストの実行費用は **UNKNOWN**。

## 9. Reviewed lines・分割・orchestrator判断

予測reviewed linesは **2,900〜3,800行**（追加+削除、未実測）。canonical実装、codec/command配線、対応回帰、inventory、利用文書を含み、配布mirrorを除く。repoは600行で分割しない理由、1,400行で分割を要求する。[AGENTS.md:116-172][S25] 一括実装PRは提案しない。各600行以上の子は閉じたcommand familyのproducer/consumer/codec/拒否保証を同時に成立させる必要を説明し、実diffを再計測する。

子の番号は未起票。以下は各1PRで閉じられる提案で、起票・本文変更は本ステップの範囲外。

| 順序 | 見積 | 子の受入条件と安全な中間状態 |
|---|---:|---|
| E1: durable lineageとunresolved gate | 750〜1,000 | D3依存。全D originsのtyped lineage、v4/v5 codecs、汎用writer保護、resume/stale、status、completion unionを一つのPRで完結。解決commandは未公開なので必須findingは全て遮断 |
| E2: repair/replayの原子的再検証 | 850〜1,100 | E1依存。begin/reverify/reconcile、同一反例failed→新候補passed、immutable receipt/effects、latest/unknown/fence、最小CLI失敗→修復→成功経路。独立disposition未実装なので棄却は不可 |
| E3: 独立disposition | 650〜850 | E2とD2 runtime契約依存。専用request/terminal、deferred/rejected、scope/alias/trust判定を一つのPRで完結。adapter未対応はblocked、required risk受容を成功にしない |
| E4: 比較履歴・event・最終統合 | 650〜850 | E3依存。before/after normal/consumer/evaluator refs、regression/unmeasured、不変event/export、保持証拠と再開、公開利用手順とinventoryを完結。Iの集計ロジックは変更しない |

E2以前もattempt/receiptの最小履歴は保存する。E4まで比較が未測定であることを明示し、親880の「改悪を測れる履歴」を未完了として残す。子をRefsで親へ結び自身をCloses、最後のE4を#880本体に残す場合は親本文を最終統合の条件へ更新する。ここでは本文を変更しない。

orchestratorの未決事項は次の3点。型・reducer・gateの上記選択はこの案に固定し、未決を実装者に自由選択させない。

1. **分割と親の完了条件**。推奨はD3→E1→E2→E3→E4。子起票と#880本文の再編をorchestratorが決定する。未決のままではbranchごとの受入条件が確定しない。
2. **独立dispositionのruntime範囲**。推奨はDと同じfixture公開経路をrepo保証とし、実host対応はD4/外部adapterに委ねる。実host成功をE必須にする場合、host/API/観測能力と権限の指定待ち。fixture成功を実host成功とは報告しない。
3. **Iとのevent契約とbaseline保持**。推奨は§6のrefs/status/comparison可否をEが供給し、全割当・正誤/不要変更・費用の集計はIが担当。I側が必要とするevaluator artifact形式と保管期間/容量の合意が必要。未合意でも通常/replay receipt履歴は保持し、意味上の改悪はUNKNOWNとする。

### 決定（orchestrator）

1. **分割を採用する。** D3（#689）→ E1 → E2 → E3 → E4 の順に、各子を 1 PR で閉じる。E1〜E3 は #880 の子として起票し、E4（比較履歴・event・最終統合）を #880 本体に残す。各子は自身を Closes し #880 を Refs で指す。#880 の最後の PR は E4 の受入条件だけで Closes する。
2. **独立 disposition の runtime は D と同じ扱いとする。** E3 の保証は tests 配下の fixture adapter を通した公開経路の回帰証拠に限り、実 host の独立性の証明として扱わない。実 host 対応は D4（#897）または外部 adapter に委ね、E の完了条件に含めない。
3. **I（#884）との event 契約と証拠保持は推奨どおりとする。** E は §6 の refs・status・比較可否を `mission-repair-event/1` で供給し、全割当の分母・正誤・不要変更・費用の集計は I が担う。証拠は公開 head の lineage/event から到達できる限り保持し、GC の対象にしない。容量超過は blocked として記録し、過去の証拠を黙って落とさない。独立 evaluator の artifact 形式は I で決め、E は ref を受け取る欄だけを持つ（未合意の間は意味上の改悪を UNKNOWN とする）。

この決定は設計レビューの対象であり、実装・CI の受入ではない。

closed-v5 public bridge、精密依存graph、contract差替え、汎用resolved setter、実装者の棄却自己申告、metricsによる完了許可は対象外。

## 出典（全て照合headに固定）

リンクは全て `8f40f542f8fed8660728234d41295ee3212deeab`。各ラベルのfile:lineはこのheadで読んだ範囲。

[S01]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/acceptance_contract.py#L66-L155 "acceptance_contract.py:66-155 — A ledger、criteria、coverage、digest"
[S02]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/verification_execution.py#L85-L167 "mission_application/verification_execution.py:85-167 — B replay selection/binding/receipt"
[S03]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/verification_runner.py#L147-L420 "mission_application/verification_runner.py:147-420 — materialization、runner repro digest、観測passed"
[S04]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/transitions.py#L985-L1115 "mission_kernel/transitions.py:985-1115 — C/D0 gate、latest、force、score gate"
[S05]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/acceptance_contract.py#L158-L200 "acceptance_contract.py:158-200 — shared frozen validator; verifier_command.py:23-101 のcommand/link検査を呼ぶ"
[S06]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/model.py#L352-L487 "mission_kernel/model.py:352-487 — findings/identity/lease/MissionState"
[S07]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v5.py#L561-L584 "mission_kernel/codec_v5.py:561-584 — typed findings/extensions/A4; codec_v4.py:667-700 と863-925も照合"
[S08]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v4.py#L714-L742 "mission_kernel/codec_v4.py:714-742 — legacy findingはopen; codec_v5.py:390-453でresolved shapeを照合"
[S09]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/commands.py#L397-L455 "mission_kernel/commands.py:397-455 — generic-set dedicated fields; transitions.py:1837-1861 specialist authority遮断"
[S10]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/review.py#L30-L101 "mission_application/review.py:30-101 — capture/presence/closeout; 503-523 — preflight-before-force"
[S11]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/transitions.py#L869-L947 "mission_kernel/transitions.py:869-947 — reactivate/resume control更新"
[S12]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/evidence.py#L199-L250 "mission_application/evidence.py:199-250 — historical operation replay response"
[S13]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_persistence/legacy_v4.py#L714-L773 "mission_persistence/legacy_v4.py:714-773 — transition/effects/compatibility; fenced_commit.py:1465-1500 lease拒否"
[S14]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/planning_provider_metrics.py#L1-L34 "planning_provider_metrics.py:1-34 — planning専用metrics schema/母集団"
[S15]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/retry_plan.py#L53-L170 "mission_application/retry_plan.py:53-170 — ContextManifestRetryPlan; mission_persistence/retry_loop.py:18-51"
[S16]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/review_aggregation.py#L327-L333 "mission_application/review_aggregation.py:327-333 — critic scope observation"
[S17]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/scoring_provenance.py#L42-L73 "scoring_provenance.py:42-73 — immutable source/revision; 159-221 score再導出"
[S18]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/transitions.py#L1448-L1482 "mission_kernel/transitions.py:1448-1482 — evidence reducers; commands.py:254-267 commands"
[S19]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/command_owners.py#L26-L94 "mission_application/command_owners.py:26-94 — owners; 124-157 C2 inventory/empty allowlist"
[S20]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_python_inventory.py#L102-L170 "mission_python_inventory.py:102-170 — recursive module discovery/import"
[S21]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/scripts/check-thin-adapter-ratchet.py#L17-L37 "scripts/check-thin-adapter-ratchet.py:17-37 — rules/source/baseline"
[S22]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_codex_wrapper_sync.py#L14-L61 "test_codex_wrapper_sync.py:14-61 — byte equality、除外"
[S23]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_artifact_hygiene.py#L35-L51 "test_artifact_hygiene.py:35-51 — tracked scan; test_vendor_fingerprint.py:76-81も同様"
[S24]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/.github/workflows/ci.yml#L106-L119 "ci.yml:106-119 — shard; Makefile:51-59、scripts/ci_shard_targets.py:33-51"
[S25]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/AGENTS.md#L116-L172 "AGENTS.md:116-172 — 600/1400 reviewed area、mirror allowlist、自己申告"
[D01]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/docs/design/689-fresh-review-receipt.md#L11-L128 "docs/design/689-fresh-review-receipt.md:11-128 — D projection/request/candidate/runtime契約（未merge実装依存）"
[D02]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/docs/design/689-fresh-review-receipt.md#L132-L237 "docs/design/689-fresh-review-receipt.md:132-237 — typed反例、coverage、terminal/publication/recovery契約（未merge実装依存）"
[D03]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/docs/design/689-fresh-review-receipt.md#L239-L273 "docs/design/689-fresh-review-receipt.md:239-273 — D3 gate理由/未解決finding/closed-v5境界（未merge実装依存）"
[D04]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/docs/design/689-fresh-review-receipt.md#L106-L130 "docs/design/689-fresh-review-receipt.md:106-130 — independence、trust root、fixture/実host限界"
[T01]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue500_codec_v5.py#L513-L526 "test_issue500_codec_v5.py:513-526 — public-v5 producer境界"
[T02]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue878_verification_runner.py#L235-L342 "test_issue878_verification_runner.py:235-255 replay; 327-342 retry"
[T03]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue626_thin_adapter_guard.py#L366-L389 "test_issue626_thin_adapter_guard.py:366-389 — baseline/ratchet; test_python_module_inventory.py:95-103 imports"
[T04]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue500_v4_decoder.py#L281-L296 "test_issue500_v4_decoder.py:281-296 — legacy status open正規化"
[T05]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue500_codec_v5.py#L290-L338 "test_issue500_codec_v5.py:290-338 — resolution field/generation/round-trip"
[T06]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue878_candidate_snapshot.py#L18-L110 "test_issue878_candidate_snapshot.py:18-110 — snapshot/local input/test report"
[T07]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue877_acceptance_contract.py#L42-L62 "test_issue877_acceptance_contract.py:42-62 — unmapped obligation/immutable contract"
[T08]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue879_completion_cli.py#L486-L704 "test_issue879_completion_cli.py:486-704 — latest/shortcut/force/writers/codec/legacy"
[T09]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue879_completion_cli.py#L335-L416 "test_issue879_completion_cli.py:335-416 — D0 shared validation/replay拒否"
[T10]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue747_retry_loop.py#L71-L125 "test_issue747_retry_loop.py:71-125 — authority境界/operation identity"
[T11]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_issue503_fenced_commit.py#L703-L835 "test_issue503_fenced_commit.py:703-728 identity; 786-835 crash"
[T12]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_command_inventory.py#L1732-L1804 "test_command_inventory.py:1732-1804 — owner/direct writer"

[S26]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v4.py#L667-L700 "skills/mission/lib/mission_kernel/codec_v4.py:667-700"

[S27]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v4.py#L862-L925 "skills/mission/lib/mission_kernel/codec_v4.py:862-925"

[S28]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v5.py#L721-L765 "skills/mission/lib/mission_kernel/codec_v5.py:721-765"

[S29]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/codec_v5.py#L390-L453 "skills/mission/lib/mission_kernel/codec_v5.py:390-453"

[S30]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/transitions.py#L1837-L1861 "skills/mission/lib/mission_kernel/transitions.py:1837-1861"

[S31]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_application/review.py#L503-L523 "skills/mission/lib/mission_application/review.py:503-523"

[S32]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_persistence/retry_loop.py#L18-L51 "skills/mission/lib/mission_persistence/retry_loop.py:18-51"

[S33]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/scoring_provenance.py#L159-L221 "skills/mission/lib/scoring_provenance.py:159-221"

[S34]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/Makefile#L51-L59 "Makefile:51-59"

[S35]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/scripts/ci_shard_targets.py#L33-L51 "scripts/ci_shard_targets.py:33-51"

[S36]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_persistence/fenced_commit.py#L1465-L1500 "skills/mission/lib/mission_persistence/fenced_commit.py:1465-1500"

[S37]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_vendor_fingerprint.py#L76-L81 "skills/mission/tests/test_vendor_fingerprint.py:76-81"

[S38]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/lib/mission_kernel/json_codec.py#L12-L12 "skills/mission/lib/mission_kernel/json_codec.py:12-12"

[S39]: https://github.com/tackeyy/mission/blob/8f40f542f8fed8660728234d41295ee3212deeab/skills/mission/tests/test_python_module_inventory.py#L95-L103 "skills/mission/tests/test_python_module_inventory.py:95-103"
