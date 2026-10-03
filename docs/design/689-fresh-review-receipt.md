# 独立した反例探索の request・起動・出力を束縛する設計

設計判断は §9「決定（orchestrator）」で確定。実装・テスト変更・外部登録は行っていない。
対象は [Issue 689: 独立した反例探索の receipt](https://github.com/tackeyy/mission/issues/689)。
照合した head は `93c0833efc55a90f535d7c525ac533c7897b3cbf`。
以下の「現状」はこの head のコードだけを指し、「決定」は追加予定の契約を指す。
引用は末尾の固定 SHA の出典へ結び、各出典に `file:line` を併記する。
GitHub の in-flight 照合は接続エラー、exit 1。open PR・レビュー・CI・merge の外部状態は UNKNOWN。
提供された Issue 本文と専用 worktree の head を用い、Issue/PR は作成・更新しない。

## 1. 採用する境界と既存状態

決定: request と receipt を新しい typed kernel projection に置き、runtime adapter は観測事実だけを提供する。
application が入力構築、adapter 呼出し、候補再取得、replay verifier 実行、公開処理を調整する。
kernel が一回消費・binding・coverage・finding・完了可否を決定する。
CLI は typed request の構築と単一 use case の呼出し、描画、終了コード変換だけを担う。

現状の contract は `fresh-required` 固定、requirement ledger は元要求の全 codepoint 区間を
`obligation/context` に分類するが、coverage import は pending のみである。[S1]
現状の kernel gate は coverage valid、最新の required verification receipt、候補一致を確認した後、
無条件に `acceptance-fresh-review-pending` を返す。force にも同じ gate が先行する。[S3]
現状の contract/verification receipt は FrozenJsonObject を持つ command から evidence document に保存される。
MissionState に専用 fresh-review 型はなく、v4 は legacy_passthrough、closed v5 は extensions と typed finding を持つ。[S4][S5]

決定: この既存 JSON 保存面を保ちながら `MissionState.fresh_review: FreshReviewProjection` を追加する。
v4 document と v5 extensions の予約キー `fresh_review` を同じ閉じた decoder で型へ復元し、
encoder は projection と保存面の一致を検証する。A4 projection の両 codec からの復元方式を参考にする。[S5]
未知 schema・未知 field・不正 shape は理由コード付き拒否とし、空 projection と読み替えない。
キー欠落だけが empty projection。汎用 set・init・downgrade・review import からの上書きを許さない。
contractless legacy の成功条件と score 経路は変更しない。

## 2. Request と一回消費

決定: `mission-fresh-review-request/1` の閉じた構造を使う。
digest は canonical UTF-8 JSON の SHA-256、ID は携帯可能な opaque identifier、
整数は bool を受け付けない。入力の内容と制御情報を分離する。

| 型のまとまり | 必須 field と意味 |
|---|---|
| identity | `request_id: str`, `nonce: str`, `mission_id: str`, `session_id: str`, `schema: str` |
| bindings | `requirement_digest`, `contract_digest`, `verifier_policy_digest`, `candidate_digest`, `input_digest`, `adapter_registration_digest`: digest |
| candidate | `candidate_bindings: tuple[CandidateBinding, ...]`。criterion ID、通常 verifier と登録 replay verifier の command ID・definition digest・snapshot digest |
| search | `criterion_ids: tuple[str, ...]`, `iteration: int`, `perspective: str`, `allowed_tools: tuple[ToolCapability, ...]` |
| budget | `wall_time_sec`, `max_tool_calls`, `max_replays`, `max_output_bytes`: 正の int |
| provenance | `input_ref: ContentAddressedRef`, `created_at: aware timestamp` |

request_id と nonce は application が暗号学的乱数で生成する。caller に指定させない。
prepare の再応答は operation の保存結果から同じ request を返し、乱数を再生成しない。
iteration は現 state と一致、criterion_ids は重複なし・登録済み・非空とする。
一回で全 required criterion を選ぶことを既定にするが、部分探索も記録可能。
部分探索だけでは全体完了できない。criterion ごとの independent opt-out は設けない。

`input_digest` は、request の制御 envelope を除いた immutable input packet の digest。
packet は元要求全文、ledger 全体、criteria 全体、凍結 policy の対象定義、候補 snapshot の
manifest と読取対象内容、指定 perspective、reviewer 指示の版を含む。
実装者の成功説明・採点・既存 review 結論・会話履歴は初期入力に含めない。
nonce・budget・allowed_tools などを含む request 全体の digest を別途計算し、起動 envelope へ束縛する。
自己参照する digest は作らない。

候補は HEAD だけで識別しない。通常 verifier と replay verifier ごとの snapshot digest を
command ID 順に canonical 化した map の digest を `candidate_digest` とする。
同じ tracked bytes でも declared_untracked/external_inputs が異なるため、単一 verifier の
snapshot digest を全 criterion に流用しない。現状の capture は tracked bytes、宣言済み出力、
宣言済み local input を manifest に含める。[S6]
prepare・dispatch 直前・output import 直前・completion 直前に同じ map を再取得する。
adapter へ渡す候補は capture 済みの読取専用 materialization に限定する。

budget の repository 上限は wall time 300 秒、tool calls 64、replays 16、output 256 KiB とする。
host policy はこれより小さい上限に制限できる。入力 packet は 1 MiB 上限とし、超過は blocked。
clock/出力量/replay 数は application が計測し、tool calls と filesystem/network 能力は host adapter が
強制する。能力を強制できない host は起動しない。金額予算・repair 予約は後続 budget 作業へ残す。

公開 command は `fresh-review prepare/run/status/reconcile` と、専用モードの
`review-import --fresh-request <request_id>` とする。任意 receipt を import する command は作らない。
prepare は `PrepareFreshReview` kernel command で request と input_ref を一緒に保存し、fenced commit を使う。
runtime の長い実行中は repository lock を保持しない。

| 状態 | 消費規則 |
|---|---|
| pending / available | 保存済み。`BeginFreshReviewDispatch` が現 lease/fence と binding を照合して operation を予約 |
| dispatch-unknown / reserved | spawn 前に intent を永続化。nonce は他 operation に再利用不可 |
| running / reserved | adapter 由来 launch receipt が一致した時だけ移行 |
| terminal / consumed | `CommitFreshReviewResult` が review import と同じ公開 commit で消費確定。失敗・blocked・abandoned も consumed |

同一 operation・同一 command intent・同一 payload の再応答だけを許可する。
terminal の再応答はその operation が公開した歴史的結果を返し、現 head の状態で作り直さない。
実行途中の同一 operation 再応答は現 checkpoint を返すだけで、再 spawn しない。
operation ID を共有して内容を替えたもの、別 operation による同じ nonce、同じ child identity の再利用、
stale candidate/contract/input/iteration、二重 terminal は拒否する。
reconcile も既存 intent に対する専用 operation として fence を照合する。
既存 evidence 再応答は operation 自身の committed document を用いている。[S7]
fresh run にこの規則を採用するが、既存 verification run の「再実行して receipt 差分を拒否する」挙動は変えない。[T3]

## 3. Runtime adapter の登録と観測

決定: `FreshReviewRuntimeAdapter` は `observe_parent / launch / collect / cancel / recover` の protocol。
mission から adapter へは canonical request bytes と immutable packet/snapshot の handle だけを渡す。
model の回答 JSON に launch receipt を生成させない。adapter は host API/process 観測を別 channel で返す。

登録は user trust root の `$XDG_CONFIG_HOME/mission/fresh-review-adapters.json`、schema
`mission-fresh-review-adapter-registry/1`、installed entry-point group `mission.fresh_review_adapters`。
登録 field は safe `id/entry_point/distribution/version/source_digest` とする。
project registry、shell string、任意 module/file path、URL、組込み private integration は許可しない。
metadata の一意性、distribution/version/source pin を parent と callback child の双方で検査する。
registry 自体も bounded regular file とし、duplicate key・link・途中置換を拒否する。

比較: approval verifier は user registry と installed entry point を pin し、子側で再照合して
callback を実行する。generic distribution validator は既に lib にある。[S8]
command-provider saga は起動の不確実性と fence を扱うが、現 receipt は kind/identity だけで、
fresh context と実際に取り込んだ入力を証明する情報がない。[S9][S10]
従って registry の信頼規則と saga の遷移を再利用し、approval と planning の意味を混ぜない。
approval 用 fork callback を起動しただけでは fresh reviewer と認めない。
source pin は entry-point module の pin であり、host 全体・依存コードの誠実性を証明しない。
host adapter を trust root とする限界を公開仕様に書く。

launch receipt は閉じた `mission-fresh-review-launch/1` とする。
`request_id/request_digest/nonce/operation_id/fencing_epoch/adapter_registration_digest`、
adapter 観測による `parent_identity/child_identity/context_identity/context_mode/received_input_digest/started_at`、
enforced tools/budget を持つ。identity は opaque で再利用判定に使え、PID 単独は採用しない。
parent_identity は `observe_parent` 由来、child/session/context identity と received_input_digest は
adapter が観測した child 側の入力受領情報から採取する。caller flag、モデル名、model の自己申告は禁止。
child 側が実際に読み込んだ packet bytes の digest を取得し、送信 digest の単なる echo と区別する。

| 観測結果 | 決定 |
|---|---|
| 別 child・別 context・入力受領一致・能力強制を host が観測 | kernel が `independent=true` を導出 |
| inline または parent と同じ context | `independent=false`。診断用 output は保存可能、valid receipt にはならない |
| fresh child を開始できない、identity/input 受領/能力が観測不能 | terminal blocked、理由コード。inline fallback で成功しない |
| receipt field 不正、登録 pin/binding 不一致 | terminal rejected または import 拒否。成功証拠を作らない |

同梱は tests 配下の neutral fixture adapter だけとする。fixture は実子 process で packet を読み、
receipt と決定的な output を返す。テストが作る一時 distribution metadata/user registry を通して
公開 CLI から起動し、製品 CLI に `--trust-test-adapter` のような bypass は加えない。
tests 配下は配布 wrapper の対象外である。[S11]
fixture の独立性は protocol/commit の回帰証拠であり、実 host の model context 分離の証明ではない。

実 host adapter の同梱はこの設計では **なし**。host API の起動・context identity・入力受領 digest・
能力強制を同時に確認できる実装の適否は UNKNOWN。本ステップは host probe を実行していない。
外部 package で protocol を満たすことは可能だが、実 host が利用可能だとは主張しない。
Issue の実 host 最小 probe を完了条件に残すなら、対象 host と実行権限の指定が必要。

## 4. Output、coverage、receipt と原子的公開

決定: `mission-fresh-review-output/1` は request 全 binding を持ち、
`criterion_results: tuple[CriterionSearchResult, ...]` と `coverage: CoverageResult` を返す。
CriterionSearchResult は `criterion_id`, `status: searched|blocked`, `reason_code`, `findings`。
要求された criterion の欠落・重複・未知 ID は completed output として import しない。
searched は bounded search を終えた意味であり、反例がないことの数学的証明ではない。

finding の閉じた型は `finding_id`, `criterion_id`, `requirement_ids`,
`prohibited_side_effect_ids`（criterion ID と既存 list の位置で識別）、`severity: High|Medium|Low`,
`summary`, `command_id`, `repro_input: {artifact_kind: str, content: str}`,
`actual: ObservedResult`, `expected: ExpectedResult`, `replay_evidence_ref`。
actual は runner の status/exit/count/output digest 等の観測事実、expected は criterion の expected と
禁止副作用への参照、および比較内容。モデルが書いた実行結果は未確認の仮説として扱う。

command_id は criterion の凍結 `replay.command_id` と厳密一致しなければならない。
repro_input はその replay policy の allowed_artifact_kinds/max_bytes/relative_path を使用する。
現状の B はこの形の input を登録 replay command に materialize し、結果に repro digest と
実行事実を束縛する。runner は output digest 等の事実を返し、全文出力を返していない。[S2][S6]
application は B の実行 primitive を使って replay を行い、その receipt を fresh output の証拠へ埋め込む。
モデルの actual を上書きして検証済みの事実と区別し、kernel が binding を再検証する。
replay の failed は反例の証拠になりうる。launch failure/timeout/stale は探索成功に変換しない。

replay 証拠は fresh-review 専用 collection に置き、通常の verification_receipts に追加しない。
通常完了 gate が期待する verifier definition は通常 command であり、replay の定義とは異なるためである。[S2][S3]
表現不能、replay 未登録、予算不足、観測 actual が主張を裏付けない場合は blocked finding/open obligation として保持する。
任意 command/policy の追加や差替えで補わない。required 義務または禁止副作用に関係する finding は
severity にかかわらず open のまま完了を阻止する。

coverage は元要求全文と ledger **全項目**を照合する。
各 requirement ID に `classification_confirmed`, `criterion_ids`, `status: valid|open`,
`reason_code`, `reason` を持つ。context 判定にも理由が必要。
context に誤分類された義務は元 span/requirement ID を参照する supplemental open obligation とする。
criteria がない義務、optional criteria だけに割り当てた必須義務、禁止副作用の欠落は open。
ledger の omission を valid と呼ぶ自己申告だけでは足りず、kernel が全 ID の網羅、参照整合、
必須 obligation と required criterion の対応、open 数、binding を検査する。
意味上の分類が正しいこと自体は信頼した独立探索に依存する。

契約内 `coverage: pending` は import 時の不変値として残す。
実効 coverage は `FreshReviewProjection.coverage_receipts` から導出し、status command で
`imported_coverage` と `effective_coverage: pending|valid|open` を分けて表示する。
理由: canonical_contract_digest は imported_at/verifier_policy だけを除き、coverage を digest に含める。
契約の coverage を書き換えると B の既存 receipt 全てが stale になる。[S1]
従って C の「contract.coverage が valid」という直接比較を receipt による実効 coverage 判定へ置き換える。
契約の immutable identity/revision を書き換えず、coverage は pending から valid または理由付き open へ進む。
複数部分探索で criterion は覆えても、全 ledger の valid coverage は単一の全体 coverage receipt を必須とする。
新しい coverage receipt が open/blocked なら古い valid へ fallback しない。

`CommitFreshReviewResult` の一つの public state commit で、terminal receipt、output content-addressed ref、
output digest、coverage receipt、open obligations/findings、request 消費を束縛する。
ここでの commit は repository publication の世代であり Git commit ではない。
receipt は request/launch/output digest、候補 map、independent、終了状態、予算消費、時刻を持つ。
公開 artifact の bytes は同じ effect claim に束縛し、公開直前に再読取・digest/size と state を照合する。
public head が参照しない staged bytes は成功ではない。receipt だけ先に公開する経路を設けない。
既存 evidence use case は repository の transition+effects を利用し、v4 の effect publication も保存とまとめる。[S7]
v5 は現 fenced repository を使い、別の transaction system を増設しない。

決定（終端 receipt の variant）: `mission-fresh-review-terminal/1` は `outcome` で分岐する閉じた variant とする。
全 variant 共通の必須 field は `request_id/request_digest/nonce/operation_id/fencing_epoch/outcome/reason/candidate_digest/budget_used/ended_at`。
variant ごとの field は次のとおりで、表にない field の存在・`null` による欠落表現は decoder が拒否する。

| outcome | 到達条件 | launch receipt | output ref/digest | coverage receipt・findings | 完了 gate |
|---|---|---|---|---|---|
| `completed` | adapter が child の終了と output を観測し、output が schema・予算・binding 検査を通過 | 必須 | 必須 | 必須 | `independent=true` の時だけ有効 |
| `failed` | launch 後に child の異常終了、output 不正・予算超過・binding 不一致を観測 | 必須 | output bytes が存在すれば診断用として保持可、無ければ欠落 | 持たない | 無効 |
| `blocked` | 有効な launch receipt を保存できなかったすべての場合。起動前の起動不能・入力超過・登録 pin 不一致に加え、起動後に identity・入力受領・能力強制が観測不能、または adapter の launch 報告が field 不正・binding 不一致の場合を含む | 持たない（`launch_attempted: bool` を必須とし、起動後の場合は true。adapter の `cancel` を呼んだ結果を `cancel_result` に記録） | 持たない | 持たない | 無効 |
| `abandoned-unknown` | dispatch-unknown または running の中断後、exact child と output を観測できない | running に達していれば必須、dispatch-unknown からなら持たない | 持たない | 持たない | 無効 |

`reason` は variant ごとの閉じた理由コード集合から選ぶ。`completed` 以外は理由コード必須で、`completed` は `none`。
どの variant でも terminal commit が request を consumed にし、同じ nonce の再利用・二重 terminal を拒否する。
同一 operation の再応答は保存済み terminal をそのまま返す。再試行は `fresh-review prepare` で新しい request（新しい nonce）を作る。
`independent=false` の inline 実行は `completed` として保存できるが、完了 gate では無効のままとする。
`launch_attempted=true` の `blocked` の後に同じ child から届いた報告・output は、request が consumed のため import を拒否する。
§3 の「rejected」は保存される終端ではなく、command 単位の拒否を指す。state を変えず、request はその時点の状態に残る。
running の request に対する output import が検査で不合格になった場合は拒否で終わらせず、`failed` 終端として保存する。

pending→dispatch-unknown→running→terminal を採用する。spawn 前 durable intent、receipt 後 running、
terminal 前 candidate recapture を守る。dispatch-unknown は既存 saga と同じく自動 redispatch しない。[S9]
interrupt 後に output がなければ failed/abandoned-unknown。旧 writer の遅着は fence で拒否する。
recover は host が exact child と output を観測できる場合だけ候補再確認後に専用 import を再開できる。
caller が reconcile に completed と書いたことは証拠にならない。
terminal commit 後・応答前の停止は同一 operation の再応答で回復する。

## 5. Completion gate と legacy の境界

決定: contract key が存在する session は次の全条件を pure kernel と application preflight の
同じ guard で確認する。既存 score/artifact/specialist/force approval gate は追加条件として維持する。[S3]

1. contract・凍結 verifier policy の shape と identity が有効。
2. required criteria 全てに、最新の通常 verification receipt が passed、現 candidate と定義・policy が一致。
3. 全 required criterion に、現 request/contract/input/candidate の completed fresh review receipt がある。
4. 全て `independent=true`、criterion search 完了、全 ledger の実効 coverage valid、open obligation がない。
5. required obligation または禁止副作用へ束縛された未解決 finding がゼロ。Medium も含む。

無条件 `acceptance-fresh-review-pending` を以上へ置換し、missing/stale/non-independent/
coverage-open/unresolved-finding を区別する理由コードを返す。
一つの review が全条件を覆ってよい。部分 receipt を合成する場合も、各 criterion の最新 attempt が
failed/blocked/non-independent なら古い成功へ fallback しない。running request もその対象 criterion を未達にする。
MarkPass に application が観測した fresh candidate map を typed carrier として渡し、kernel は保存 request と比較する。
kernel 自身で filesystem/adapter を呼ばず、caller boolean の `independent/coverage_valid` を受け付けない。

finding は D では open として導入するだけで、caller による dismiss や一般の score 更新では消えない。
候補変更・後続の finding なし review も以前の未解決必須 finding を暗黙に解消しない。
repair/reverification による typed resolution は [Issue 880: 反例から修復と再検証を追跡](https://github.com/tackeyy/mission/issues/880)
の責務。D の成功経路は初回から必須 finding ゼロで成立させる。Low の単なる表現指摘は obligation と切り分けるが、
required 違反なら Low でも止める。severity だけで免除しない。

公開 CLI の end-to-end 成功は、通常 init→policy/contract import→verification run→
fresh-review prepare/run（登録 fixture child）→専用 review import→既存 score 経路→mark-passes/closeout。
coverage を fixture が state に直書きする成功テストは採用しない。
missing piece はその gate の理由コードで拒否し、terminal flags と公開証拠 bytes の不変を確認する。
これはテストの入口と保証の指定であり、入力値の全面表は実装時に設計する。

公開成功の対象は v4 flat と v5 fenced container の v4 payload。
現状の closed schema 5 は public compatibility projection が unavailable とする回帰で固定されている。[T1]
D では closed v5 の typed codec/pure gate を対応させるが、一般の public bridge は新設しない。
closed v5 の公開成功も要求するなら別の互換性作業が必要であり、範囲拡大の判断事項とする。
contract **キー欠落**の legacy だけが従来成功条件へ進む。

## 6. C から引き継ぐ Low の処理

決定 (a): persisted command_id の型を lookup 前に検査する。凍結 command は
declared_untracked/external_inputs を含む必須 field と各要素を、hash/set/capture 前に閉じた validator で検査する。
shape 不正は `acceptance-contract-invalid` または `verifier-policy-command-invalid` として拒否し、
TypeError/KeyError の internal-error に流さない。validator を prepare/run/completion の共有境界に置く。
live policy の既存 validator も、不正要素を set 化する前に検査する。[S2]
現在の application lookup/capture はこの順序を満たさず、例外 catch も OSError/ValueError に限定する。[S12]
replay 経路も同様の lookup/capture を持つため、そこにも同じ shape 検査を適用する。[S2]

決定 (b): `"acceptance_contract" in document` を presence 判定とし、null は invalid。
kernel gate、candidate capture、already-passed closeout、mark-pass preflight、status の同形箇所へ適用する。
現在 import は key 存在で再登録を拒否する一方、gate/capture/closeout/preflight と status は
None または dict で分岐している。[S4][S12][S13]
契約なしを「null でも可」へ広げない。null を除去して成功させる migration は作らない。

決定 (c): C の bypass inventory の未追跡、commit 前、独立探索未実施等の現在形を更新する。
それらの記述はこの head の文書に残っている。[S14]
過去の Red/途中失敗は履歴として保存し、現時点の producer/gate/CI 配線へ inventory を合わせる。
PR の accepted/独立探索件数/CI/merge 時刻は GitHub の一次記録が必要で、本ステップでは UNKNOWN。
提供された本文の主張を検証済みの実行記録へ格上げしない。

## 7. 変更面、既存保証、実装前の検証方針

決定: 新規 kernel `fresh_review` 型/decoder/reducer、application `fresh_review` use case、
runtime adapter port と host entry-point observer を分ける。kernel→application import は追加しない。
CLI に policy 分岐・retry orchestration・state writes を置かない。
共有 saga primitive を関数として利用する。planning provider 自体を refactor する必要はなく、
fresh-review collection に閉じた intent/receipt を写す command を追加する。
既存 generic receipt の kind/identity 契約を fresh-review 専用 field で緩めない。

inventory impact: `command_owners.py` の A2.review に prepare/run/import/reconcile、R1.query に status を登録し、
対応する kernel command 型・遷移 registry・public schema/help・operation identity を揃える。
既存 owner registry は parser command を列挙し、direct session write allowlist は空である。[S15]
module inventory は再帰 discovery のため手作業の module allowlist は不要。ただし新 lib は
import/互換構文・mirror byte equality の対象へ入る。[S16]
thin-adapter baseline は増やさず、headroom-free scan と base ratchet を維持する。[T7]
canonical skills/scripts と reviewer 指示を変更したら対応 wrapper を同期する。
test fixture adapter は tests 配下に置き配布しない。[S11]
衛生・語彙 scan が tracked-only なので、未追跡のこの設計を既存 suite が検査したとは報告しない。[S17]

既存テストの次の保証を残す。名前はこの head の関数であり、新テストの入力表ではない。

| 固定するテスト（ファイル先頭は `skills/mission/tests/`） | 残す保証 |
|---|---|
| `test_issue879_completion_cli.py::test_pending_contract_rejects_public_completion_atomically` / `test_already_passed_contract_closeout_is_not_a_success_shortcut` [T1] | pending は公開成功しない、既 pass shortcut も契約を再確認 |
| 同 `test_public_completion_revalidates_latest_receipt` / `test_valid_force_approval_cannot_override_acceptance` [T1] | latest failure/stale の遮断、force 前の拒否で承認証拠を増やさない |
| 同 `test_alternate_completion_and_evidence_writes_reject_atomically` / `test_reinitialization_cannot_remove_a_frozen_contract` [T1] | set/advance/init による contract・completion authority の迂回を拒否 |
| 同 `test_contractless_completion_and_already_passed_closeout_remain_usable` / `test_halt_stops_a_contract_session_without_claiming_success` [T1] | legacy pass と停止の区別 |
| 同 `test_observations_and_caller_boolean_cannot_supply_acceptance_evidence` / `test_contract_import_cannot_produce_valid_coverage` / `test_codecs_keep_contract_and_receipt_evidence` [T1] | observation/import が coverage を作らない、codec 保存と closed-v5 境界。coverage 読取元だけ新規 receipt へ更新 |
| `test_issue632_transition_is_the_writer.py::test_mark_pass_rejects_receipt_without_fresh_candidate_observation` / `test_mark_pass_on_v5_repository_commits_projection_and_aggregate_once` [T2] | typed carrier 未配線を拒否、pure transition と公開 writer の一致 |
| `test_issue878_verification_runner.py::test_import_freezes_registered_project_verifier_policy` / `test_public_runner_binds_replay_input_to_frozen_replay_command_and_receipt` / `test_same_operation_retry_runs_the_verifier_again_and_rejects_a_different_receipt` [T3] | policy 不変、typed repro binding、既存 verifier retry 規則 |
| `test_issue878_candidate_snapshot.py::test_snapshot_uses_dirty_tracked_bytes_and_fresh_materialization` / `test_snapshot_materializes_declared_local_input_and_rejects_target_collision` / `test_test_runner_uses_declared_junit_report_not_console_text` [T3] | HEAD 以外の候補内容・外部入力・実 test report の区別 |
| `test_issue877_acceptance_contract.py::test_preserves_unmapped_obligation_as_pending` / `test_contract_is_not_replaceable` / `test_codepoint_spans_accept_non_bmp_and_combining_text` [T4] | 義務を黙って落とさず immutable ledger/codepoint span を保持 |
| `test_issue509_a4_application.py::test_crash_after_intent_before_spawn_is_dispatch_unknown` / `test_crash_after_spawn_before_receipt_never_redispatches` / `test_crash_after_receipt_before_terminal_is_running_only_with_exact_receipt` / `test_stale_fencing_epoch_reconciliation_is_rejected` / `test_receipt_replay_or_identity_mismatch_leaves_original_unknown_record_unchanged` [T5] | crash 境界、no redispatch、exact receipt/fence |
| `test_provider_preflight.py::test_host_verified_receipt_runs_exact_packet_once_and_rejects_replay` / `test_input_byte_mutation_after_approval_blocks_spawn` [T5] | 既存 command provider の one-use packet/approval binding を維持 |
| `test_review_import.py::test_review_import_rejects_lease_before_archive_publish_exactly_once` / `test_review_import_success_reuses_one_lease_decision_and_emits_one_carrier` / `test_review_import_state_publish_failure_rolls_back_new_evidence_and_emits_internal_outcome` [T6] | archive を先行公開しない、lease を二重採取しない、publish failure の rollback |
| `test_issue747_p2b_cli_operation_id.py::TestRealCli.test_a_retry_with_the_same_identity_appends_once` / `test_reusing_the_identity_for_other_content_is_refused` / `test_a_retry_still_replays_after_another_operation_wrote_the_same_slot` [T6] | retry は同一 intent の過去結果、別 intent を拒否 |
| `test_issue626_thin_adapter_guard.py::test_repository_scan_matches_the_headroom_free_baseline_exactly`, `test_command_inventory.py::test_all_parser_commands_have_exactly_one_declared_owner` / `test_c2_repository_commands_have_no_direct_legacy_session_writer_calls`, `test_python_module_inventory.py::test_recursive_inventory_compatibility_gate_imports_from_both_roots`, `test_codex_wrapper_sync.py::test_codex_wrapper_skills_match_canonical_tree` [T7] | adapter 判断の増殖、unowned command、新 module の非互換/mirror omission を遮断 |

実装時は shared kernel/validator の故障検出を中心に Red を作り、既存 C の CLI fixtures を拡張して
成功・公開拒否・atomic publication を確認する。全入力表を CLI ごとに複製しない。
fixture subprocess は host launch/public commit 境界のために使い、validation は pure test に寄せる。
新規テストの費用は UNKNOWN、実装後に対象実行で計測する。
判定/validator の独立した抜け穴探索は 50 入力以上を実装後の別 gate として実施する。本設計で実施済みとしない。
CI は Python shards→make test-shard で tracked tests を選ぶ。test-e2e は名前 filter を持つので
それだけを保証全体の実行と呼ばない。[S18]

## 8. Reviewed lines と分割提案

全体の予測 reviewed lines は **3,100〜3,900 行**（追加+削除、未実測）。
canonical の実装・codec/schema・CLI 配線・テスト・文書・inventory は含め、配布 mirror は除く。
repo の 600 行説明責任・1,400 行分割閾値を越えるため、一括 PR は提案しない。[S19]
本ステップは下表の子を起票せず、orchestrator の範囲確定を待つ。

| 順序・割当 | 予測行数 | 一つの PR で閉じる受入条件と中間状態 |
|---|---:|---|
| 子 D1: typed request と projection | 850〜1,050 | request/nonce/input publication、codec、汎用 writer 遮断、operation 再応答。prepare/status のみ公開。completion は従来どおり拒否 |
| 子 D2: runtime adapter と fenced launch/result | 1,100〜1,350 | host registry/protocol、fixture child、dispatch/recovery、output/replay/coverage receipt と一回消費の原子的公開。D1 依存。receipt を生成できるが completion はまだ拒否 |
| #689 に残す D3: completion 統合と C carry-over | 1,150〜1,500 | D1/D2 依存。全 required receipt/coverage/finding gate、C Low 3件、CLI end-to-end、reviewer 指示/利用文書、bypass inventory 更新 |

各子は #689 を Refs で指し自身を Closes、#689 の最後の PR は D3 の受入条件だけで Closes できるよう
本文を変更する。子の受入条件・依存・除外範囲は親と双方向に明記する。
D3 上限は 1,400 行を越えうるため、実装前見積りで超過したら C Low の validator/status 修正と
inventory 現状訂正を先行子 D0（200〜300 行）へ分け、D3 を 950〜1,200 行にする。
D0 は既存 C gate を維持し D1 と独立、ただし D3 より先に merge する。
docs/plan だけの本設計を実装 PR の完了条件に混ぜない。
各 600 行以上の PR では command family の型・producer・consumer・対応回帰を同時に成立させる必要を
「さらに分けない理由」として記す。行数自体は実 diff を `scripts/pr_size.py` で再計測する。[S19]

## 9. Orchestrator の決定が必要な事項

1. **分割と #689 の本文変更**。推奨は D0→D1→D2→D3、#689 は最終統合を保持。
   子の起票/本文更新は本ステップの権限外。これが未決なら実装 branch ごとの完了条件を確定できない。
2. **実 host adapter と probe の責務**。推奨は default adapter なし・fixture を repository の保証とし、
   実 host 対応を独立子へ割り当てる。元 Issue の実 host probe を D 完了必須のままにするなら、
   host/API/観測手段の一次情報と実行許可待ち。fixture 成功でその条件を閉じない。
3. **closed schema 5 の公開成功範囲**。推奨は既存 v5 container/v4 payload の公開成功と
   closed-v5 pure gate の検証に限定する。closed-v5 public bridge も必要なら先行依存へ分ける。

### 決定（orchestrator）

1. **分割を採用する。** D0（C の Low 3件）→ D1（typed request と projection）→ D2（runtime adapter と fenced launch/result）→ D3（completion 統合、#689 に残す）の順に、各子を 1 PR で閉じる。D0 は D1 と独立だが D3 より先に merge する。各子は自身を Closes し #689 を Refs で指す。#689 の最後の PR は D3 の受入条件だけで Closes する。
2. **実 host adapter は D の完了条件から外し、独立した子 D4 とする。** D1〜D3 の保証は tests 配下の fixture adapter による protocol/commit の回帰証拠に限り、実 host の model context 分離の証明として扱わない。D4 は D2 の protocol に従う実 host adapter と最小 probe を担当し、host の起動・context identity・入力受領 digest・能力強制のいずれかを観測できなければ blocked として記録して閉じない。D4 の adapter は既定で有効にせず、利用者の registry 登録を要する。
3. **closed schema 5 は推奨どおり限定する。** 公開成功は既存 v5 container/v4 payload に限り、closed-v5 は pure gate の検証に留める。closed-v5 の public bridge は本設計の対象外。

この決定は設計レビューの対象であり、実装・CI の受入ではない。

## 固定 head の出典

リンク先は全て `93c0833efc55a90f535d7c525ac533c7897b3cbf`。
テストの引用はコードの保証を指し、本ステップで実行した結果を指さない。

- [S1: acceptance_contract.py:66–155](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/acceptance_contract.py#L66-L155): policy、ledger、pending、digest。
- [S2: verifier_policy.py:36–108](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/verifier_policy.py#L36-L108)、[verification_execution.py:83–149](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/verification_execution.py#L83-L149): command/replay/receipt。
- [S3: transitions.py:985–1100](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/transitions.py#L985-L1100): completion、latest、force。
- [S4: commands.py:254–267](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/commands.py#L254-L267)、[evidence.py:399–437](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/evidence.py#L399-L437): receipt/contract writer と presence。
- [S5: model.py:367–412](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/model.py#L367-L412)、[model.py:474–487](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/model.py#L474-L487)、[codec_v4.py:667–700](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/codec_v4.py#L667-L700)、[codec_v5.py:569–584](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/codec_v5.py#L569-L584): projection と codec 境界。
- [S6: verification_runner.py:147–174](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/verification_runner.py#L147-L174)、[同:408–420](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/verification_runner.py#L408-L420): snapshot と観測結果。
- [S7: application/evidence.py:199–250](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/evidence.py#L199-L250)、[legacy_v4.py:737–764](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_persistence/legacy_v4.py#L737-L764): operation 過去結果と effect publication。
- [S8: mission-state.py:10084–10142](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/bin/mission-state.py#L10084-L10142)、[同:10254–10387](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/bin/mission-state.py#L10254-L10387)、[runtime_guard.py:114–191](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/runtime_guard.py#L114-L191): host 登録/pin。
- [S9: planning.py:1078–1140](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/planning.py#L1078-L1140)、[command_provider.py:310–338](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/command_provider.py#L310-L338)、[同:387–425](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/command_provider.py#L387-L425)、[同:475–508](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/command_provider.py#L475-L508): saga/fence。
- [S10: provider_receipt_contract.py:9–43](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/provider_receipt_contract.py#L9-L43): 携帯可能な identity と fence。
- [S11: test_codex_wrapper_sync.py:14–61](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_codex_wrapper_sync.py#L14-L61): 同期範囲・test 除外。
- [S12: review.py:29–98](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/review.py#L29-L98)、[同:500–518](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/review.py#L500-L518): shape、null、preflight。
- [S13: acceptance.py:78–91](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/acceptance.py#L78-L91): status presence。
- [S14: issue879-completion-bypass-inventory.md:81–84](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/docs/reports/issue879-completion-bypass-inventory.md#L81-L84)、[同:156–177](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/docs/reports/issue879-completion-bypass-inventory.md#L156-L177): 現在形の古い状態記述。
- [S15: command_owners.py:26–94](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/command_owners.py#L26-L94)、[同:151–157](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/command_owners.py#L151-L157): command ownership。
- [S16: mission_python_inventory.py:110–133](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_python_inventory.py#L110-L133)、[test_python_module_inventory.py:29–80](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_python_module_inventory.py#L29-L80): recursive mirror/互換 gate。
- [S17: test_artifact_hygiene.py:1–70](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_artifact_hygiene.py#L1-L70)、[test_vendor_fingerprint.py:76–81](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_vendor_fingerprint.py#L76-L81): tracked scan。
- [S18: ci.yml:112–125](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/.github/workflows/ci.yml#L112-L125)、[Makefile:51–59](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/Makefile#L51-L59)、[ci_shard_targets.py:33–51](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/scripts/ci_shard_targets.py#L33-L51): CI route。
- [S19: AGENTS.md:116–149](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/AGENTS.md#L116-L149)、[同:163–172](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/AGENTS.md#L163-L172): reviewed area、閾値、自己申告。
- [T1: test_issue879_completion_cli.py:146–384](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue879_completion_cli.py#L146-L384)。
- [T2: test_issue632_transition_is_the_writer.py:129–159](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue632_transition_is_the_writer.py#L129-L159)、[同:786–807](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue632_transition_is_the_writer.py#L786-L807)。
- [T3: test_issue878_verification_runner.py:85–342](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue878_verification_runner.py#L85-L342)、[test_issue878_candidate_snapshot.py:18–110](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue878_candidate_snapshot.py#L18-L110)。
- [T4: test_issue877_acceptance_contract.py:42–62](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue877_acceptance_contract.py#L42-L62)、[同:128–137](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue877_acceptance_contract.py#L128-L137)。
- [T5: test_issue509_a4_application.py:176–244](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue509_a4_application.py#L176-L244)、[test_provider_preflight.py:482–575](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_provider_preflight.py#L482-L575)。
- [T6: test_review_import.py:527–566](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_review_import.py#L527-L566)、[同:807–849](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_review_import.py#L807-L849)、[test_issue747_p2b_cli_operation_id.py:383–498](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue747_p2b_cli_operation_id.py#L383-L498)。
- [T7: test_issue626_thin_adapter_guard.py:373–444](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_issue626_thin_adapter_guard.py#L373-L444)、[test_command_inventory.py:1732–1804](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_command_inventory.py#L1732-L1804)、[test_python_module_inventory.py:29–113](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_python_module_inventory.py#L29-L113)、[test_codex_wrapper_sync.py:40–61](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/tests/test_codex_wrapper_sync.py#L40-L61)。
