# PR 950 round 1 修正・検証記録

[PR 950: inert dispatch kernel の契約](https://github.com/tackeyy/mission/pull/950) の指摘を局所修正。
[PR 951: application 側の呼出し](https://github.com/tackeyy/mission/pull/951) は参照のみ。
Git 操作、レビュー CLI、GitHub 書込み、full suite は実施していない。

## 修正と回帰テスト

すべて `skills/mission/tests/test_issue912_dispatch_kernel.py` にある。

| 指摘 | 修正・検出対象 | テスト名 |
|---|---|---|
| Medium 1 | blocked / abandoned-unknown の終端後予約残量は 0 | test_terminal_without_lineage_reservation_has_zero_remaining_capacity_reservation |
| Medium 2 | operation の domain-separated 予約 ID、閉じた 5 class、D2c で kernel が導出する verification class | test_dispatch_decoder_uses_the_e0_closed_shape |
| Low L1 | absent な dispatch / launch / independent の key を省略。main の prepare fixture は 1,863 bytes と SHA-256 を固定 | test_pending_projection_keeps_main_wire_bytes_and_omits_absent_dispatch_fields |
| Low L3 | launch command を保存済み dispatch operation に束縛 | test_kernel_binds_begin_launch_and_terminal_to_one_dispatch_operation |
| Low L5 | dispatch-unknown 残量の running 分を shared max から導出 | test_dispatch_statuses_release_only_the_completed_stage_reservation / test_actual_maximum_dispatch_records_fit_e0_reservations |
| Low L4 | Begin → Launch → Commit、pending launch・重複 begin / launch / commit の拒否 | test_kernel_binds_begin_launch_and_terminal_to_one_dispatch_operation |

既存の field 上限用 ASCII envelope と最大 encode 長の pin は維持。
保存 record の decode / Begin で reservation と class の authority を検査する。
D2c は予算 policy 導入前なので verification とする。設計の class は planning / implementation / verification / repair / final。

## テストの検出価値

既存 table の固定部分だけでは見えなかった終端後の lineage 予約残留を、正しく decode できる 2 終端の総予約量で検出する。
予約 ID の別 operation、任意 class・保護 class への変更を同じ decoder test にまとめた。
main の出力 bytes は同じ fixture を read-only の main 実装で encode して取得した固定 length / hash で比較する。
遷移は純粋な kernel 呼出しで検査し、子 process・sleep・ネットワークを追加していない。
修正前 source の該当関数をプロセス内だけに注入すると、終端 2 種・予約/class・bytes・launch 束縛の回帰が失敗し、現行関数に戻すと通る。
この検出証跡は `issue950-round1-regressions.log` に保存した。

## 同期・範囲・行数

- source の 3 module と配布 mirror は byte-identical。
- writer inventory 全体を AST から数え直し、キーをソート。変更は projection_document:dynamic の 4 → 3 のみ（64 module）。
- thin-adapter ratchet: current-only 成功、457 functions。Git を使う base 比較は実施していない。
- kernel の I/O・時計・乱数 import、時計読取り呼出しの追加なし。
- 参照した PR 951 の application / kernel / test ファイルの hash は作業前後で不変。
- 今回修正は +180 / -16 = 196 reviewed lines。開始時 466 行との合計による保守的上限 662 行（1,400 未満）。
- 行数は開始前 file snapshot と現行 source の SequenceMatcher 差分。PR 全体の Git diff 再計測はしていない。

## PR 951 の追従事項

1. application の独自予約 ID 導出を、kernel の `reservation_id_for_operation(operation)` の呼出しへ置換する。domain と wire ID は既存式を維持する。
2. `budget_class='review'` を `budget_class_for_fresh_review_dispatch()` の呼出しへ置換する（現段階は verification）。
3. reconcile の `RecordFreshReviewLaunch` は保存済み `record.dispatch['operation_id']` を渡す。writer fence は現行 lease のものを渡し、launch receipt の元 dispatch fence と区別する。
4. reconcile の Commit は reconcile operation / 現行 writer fence を維持する。

## 検証結果

- kernel 対象単独: 23 passed、2.04s（実装担当の実測）。
- 指定関連テストと mirror 同期検査: 920 passed、194.93s。対象は issue912 kernel、issue501 parity、issue909、issue895、issue918、issue933、plugins_in_sync、codex_wrapper_sync。
- formal review / CI の再実行は今回の依頼範囲外。
