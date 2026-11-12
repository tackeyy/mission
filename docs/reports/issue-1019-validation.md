# Issue1024 prepare保存の検証

内部prepare、paired supersedes、全stage将来容量とbounded recovery定義の局所スライス。dispatch/launch/terminal writer、公開起動入口、正completion、request/2は含めない。独立runtimeレビュー・Checker・CIは未実施。

[PR1021](https://github.com/tackeyy/mission/pull/1021)のCloses対象は[A1 Issue1024](https://github.com/tackeyy/mission/issues/1024)。[親Issue1019](https://github.com/tackeyy/mission/issues/1019)はtrackingとして残し、残runtimeは[兄弟A2 Issue1025](https://github.com/tackeyy/mission/issues/1025)へ分離済み。

## TDDテストリストと検出価値

- [x] 正常prepareのrequest/input実bytes保存と元義務の未解決維持。既存decoderテストはwriterを通らず受付欠落を検出しない。
- [x] intentなしpending repairを同Transitionでblocked化。intentあり拒否、fence、origin、full候補、pending排他、effect個数/kind/target/digest/sizeをpure共有表で検査。
- [x] 現在有効なverified/rejectedの冗長prepareを拒否し、stale後の受付を維持。full候補のrequest bindingはsubsetへ縮めない。既存E2のverified比較だけは既存intentのsubset契約を使う。
- [x] 全E3履歴を含むgeneration採番。既存E2/receipt/markerの採番を維持する。
- [x] 両encodeの最大形実bytes、near-limit、blocked fallback、61 targets、ref/ID/int上限、深いindent、escapedな周辺stateを検査。最大形と現在形の差をstage残予約へ計上し、terminalだけ解放。paired終端の実追加bytesはprepareのproposed stateへ含まれる。
- [x] recoveryは閉じた最新1slot。欠落互換と明示absence、元dispatch binding、27文字UTC時刻、enum/型/上限を検査。exited観測も解決・停止の権威にしない。
- [x] UoWのafter-stage/after-prepare/after-head-replaceでpaired保存の原子性と同operation再読取。application seamでは現在headが空・現在fenceが進んだ場合にも元operationの保存結果を返す。

新規suiteはpure主体で、実host/model、sleep、外部probeは使わない。UoW障害3件もローカルfixtureを使い、D実行fixtureを重複起動しない。既存のdecoder/E2容量・UoW検査は保持し、入力表の入口別複製はしていない。CI経路は`skills/mission/tests/`→`scripts/ci_shard_targets.py`→`make test-shard`。新規40件の実測は1.76秒（最後の小修正前）。保守費用は未測定。

## RedからGreenの証跡

1. 最初の型欠落Red後、typed commandだけを定義し、`decide`が`unknown-command`で拒否する契約Redを確認。最小reducerで正常保存1件Green。
2. paired supersedesは`repair-lineage-shape-invalid`でRed、同operation/fenceのblocked修復終端とrequestを一Transitionへ含め2件Green。
3. 過去E3 terminal generation=41に対し新受付が1となるRedを確認。履歴を含む採番で42へGreen。
4. recovery欠落、E3予約stub=0のRedを確認。closed slotとv4早期return前の予約追加でGreen。
5. near-limitで最大形を丸ごと残予約し続けるとstage成長を二重計上して拒否するRedを確認。両encodeの最大形−現在形へ変更しGreen。
6. 現在有効なverified/rejectedの冗長prepareがacceptedとなるRedを確認。現在候補/markerによる拒否とstale後到達性でGreen。
7. replay元のfence=2と現在fence=3の不一致でreadbackを拒否するRedを確認。再送は元operationの保存fenceを返し、初回だけ現在fence照合する形でGreen。

## 検証結果と面積

- 変更前baseline: `test_issue907_disposition.py`と`test_issue906_repair_attempts.py`、108件成功、129.76秒。
- 新規suite、writer inventory、module inventory、artifact hygiene、neutral vocabulary: 56件成功、7.94秒。
- 登録contract、既存容量/decoder、spawn inventory、wrapper同期群: 初回557件成功・writer inventoryのみ不一致。`execute_evidence_operation`の新しいdefensive copyをinventoryへ1件追加。修正後の最終再検証は下記のとおり。
- 最終関連suite: 613件成功・1件除外、51.34秒。除外は既存CLI全体corpus検査（`-k not actual_cli`）であり、CLI入口を追加していない本スライスでは対象外。新規40件、既存decoder/容量、command table登録、writer/spawn inventory、mirror/module inventory、artifact hygiene、neutral vocabularyを含む。
- `git diff --check`成功。mirrorは同期scriptで反映。
- 面積: 計画180行とuntrackedを含むreviewed行数は782行。byte-identical mirrorだけ除外する。`pr_size.py --base`は未commit変更を含めないため、その180行だけの出力を総面積と扱わない。

最終runtime受入はrootの独立レビュー・Checker・exact-head/CI検証待ち。全producer観測と旧request移行は[Issue1022](https://github.com/tackeyy/mission/issues/1022)、正completionは[Issue1023](https://github.com/tackeyy/mission/issues/1023)の後続範囲であり、この保存専用/1を解決証拠へ昇格させない。


## A1/A2分割の停止判定

保存済み`c4b338b1131d92a33823f4901b9f2cf25b803e5f`の実測は782 reviewed行（raw 1,011、同期mirror 229行除外）。残枠618行に対し、残実装は以下の640〜910行見込み。見積であり実装diffの実測とは区別する。1,400行へ収めるために精算保証やテストを削らず、残runtime実装を開始する前に停止した。

| 追加対象 | reviewed行見込 | 再利用しても残る固有契約 |
|---|---:|---|
| typed commands・seal・dispatch admission/intent | 110〜150 | prepared/target/request/op/full候補の閉じた照合、同commit予約、policyなし非activation |
| launch・bounded recovery・host結合 | 90〜130 | E3専用5field照合、int32 exit、元dispatch固定、最新slot置換と停止不明hold |
| terminal/outcome・effect保存 | 120〜170 | prospective terminalの既存証拠検査、blocked専用経路、state-only fallback、duplicate集合導出 |
| open/closed/trim精算・progress | 100〜140 | ordinal/hash再導出、retained/trim分類、expire/kill hold、結果なしprogress保持 |
| tests | 200〜280 | 上記共有pure表、代表UoW/CAS/公開失敗/遅延retry。E2の全表は複製しない |
| inventory・検証記録 | 20〜40 | 追加writer/command契約、mirror同期証跡 |
| **追加合計** | **640〜910** | **現在分との合計1,422〜1,692** |

見積のソース根拠: [既存E2 reducerと予算接続](https://github.com/tackeyy/mission/blob/c4b338b1131d92a33823f4901b9f2cf25b803e5f/skills/mission/lib/mission_kernel/transitions.py#L1858)は40行、[既存E2 mutation](https://github.com/tackeyy/mission/blob/c4b338b1131d92a33823f4901b9f2cf25b803e5f/skills/mission/lib/mission_kernel/repair_reverification.py#L108)は56行。[E3 terminalの純粋検査](https://github.com/tackeyy/mission/blob/c4b338b1131d92a33823f4901b9f2cf25b803e5f/skills/mission/lib/mission_kernel/finding_disposition.py#L301)は82行で再利用できるが、blockedを解決不可として拒否するためblocked writerの代用にはならない。既存settleにはE3のretained/trim admission projectionと結果なしprogress分岐がない。上表は既存82行のコピーを含めない。

### 元Issue1019の受け入れ条件の移管

- A1: prepare/paired supersedes/request effects、全stage将来容量、closed recovery slot定義、E3採番、request/1限定、未解決維持。dispatch/admissionは使用不可。preparedはpending排他を維持し、公開run/CLIは未接続。
- A2（A1 merge後の最新baseから開始）: typed dispatch admission/intent、launch、回復slot writer、terminal writerとhost観測、open/closed/trim精算、hold/progress、公開/CAS/state-only失敗と遅延retry。新規dispatchはA2の全writerと精算経路が揃った候補headまで有効にしない。
- 両PR: request/1限定とrequest_schema追加拒否、正completion無効、公開CLI/host/policy activationなし。親Issue1019はA2まで未完了とする。全producer観測・旧request移行・正completionの後続範囲を取り込まない。

### A1境界の補足検証

- 内部preparedに正規のpolicy fixtureを与えても、既存`admit`は`budget-repair-attempt-missing`でdispatchを拒否し、予約を追加しない。preparedからrepair beginも拒否し、completionの未解決集合から元義務を外さない。request/2は既存共有拒否表、state要約のrequest_schema追加は代表decoder検査で拒否を確認する。
- 時刻decoderの具体境界: intentの`budget._at`は20文字の秒精度だけを受理し、27文字を拒否する。recoveryの`_timestamp`は27文字のマイクロ秒精度だけを受理し、20文字を拒否する。したがって最大形のintent2時刻は20文字、recoveryは27文字。未受理の27文字intentを+16の余裕で補うという根拠にはしない。
- recovery拒否表の正常baselineが20文字時刻で先に拒否され、別field変異を遮る検証欠陥を確認。正常baselineの先行validateで7件Redを確認後、baselineを27文字へ修正して7件Green。before-start変異も27文字へ直し、時刻形式ではなく前後関係の拒否を検査する。runtime契約は変更していない。
- A1補足後suite: `test_issue1019_disposition_prepare.py`の41件成功、2.61秒。`git diff --check`成功。補足後総面積842 reviewed行（既存計画・tests・記録込み、同期mirrorのみ除外）。この補足後にA2を同PRへ加える見込は1,482〜1,752行。runtime変更はなく、独立探索・レビュー・Checker・CI・commit/pushはroot担当。

## 既知の入力検査不具合の局所修正

- [x] 直接構築した600段の`FrozenJsonObject`をprepareのrequest/packet/observed_candidatesへ渡し、例外漏出をコード付き拒否へ変換し、元state不変を検査する。
- [x] disposition要約のduplicate_targets内のdict/listを、集合化より先にdigest要素として拒否する。
- [x] 同じファイルのalias graph検査でもdict/list要素の集合化先行を解消する。同ファイルの集合化箇所だけを確認し、認証済みlineage IDから導出する集合化は変更しない。

現象は、再帰的thawがprepareの`FreshReviewError`だけのcatchを越えることと、要素型の検査前に`set`がhashを要求すること。prepare reducerで`RecursionError`だけを`disposition-invalid`へ正規化し、2箇所の重複検査をdigest検査後へ移した。既存の不正digest拒否コード`fresh-review-digest-invalid`と正規入力の契約を維持する。decoder全体や他commandの例外処理は変更しない。

Redはprepare3ケースが実際の`RecursionError`、要約2ケースが実際の`TypeError`で失敗（5件、45.27秒、再帰traceback整形を含む）。同形alias2ケースも実際の`TypeError`で失敗（2件、0.25秒）。Greenは7件成功、0.20秒。既存の正常writer/decoderとalias閉包・cycle検査はこの非hashable要素と直接構築Frozen入力を通らないため、例外漏出の回帰を検出できない。新規ケースはpure関数と既存fixtureを再利用し、host・subprocess・sleepは追加しない。保守費用は未測定。

関連検証はprepare/既存disposition decoder/writer inventory/module inventory/mirror同期の252件成功、7.11秒。artifact hygiene/neutral vocabularyの9件成功、4.52秒。mirror同期と`git diff --check`も成功。今回差分はsource10行・tests35行・記録16行の61 reviewed行、同期mirror10行を含むraw71行。計画180行を含むbaseからの総面積は901 reviewed行、raw1,140行、同期mirror239行。独立探索は部分観測の段階で停止しており、50入力以上の独立探索gateは未達。この既知不具合修正は探索の再実施・代替・gate受入を意味しない。独立runtimeレビュー、別Checker、CIも未実施のまま。
