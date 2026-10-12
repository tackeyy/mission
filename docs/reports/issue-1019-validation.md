# Issue1019 prepare保存の検証

内部prepare、paired supersedes、全stage将来容量とbounded recovery定義の局所スライス。dispatch/launch/terminal writer、公開起動入口、正completion、request/2は含めない。独立runtimeレビュー・Checker・CIは未実施。

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
