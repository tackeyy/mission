# I2a 実装引き継ぎ

Issue: [probe の識別・構成照合 #924](https://github.com/tackeyy/mission/issues/924)

状態: probe 側の round 1 指摘（初期化の arm 境界、state の公開範囲、起動前失敗の schema、path 表現、条件の型照合）を修正し、ローカル対象検証済み。scanner は先行 PR の範囲として変更していない。PR は 2 本に分けた（scanner と単体テストを先行 PR、probe・照合・永続化を本 PR）。設計書は変更していない。

## 変更と完了条件

根拠: [凍結した事前登録](https://github.com/tackeyy/mission/blob/e85e6e24027825144cf8e6aaef1576d609358907/docs/design/884-evaluation-aggregation.md) §2.1〜2.3、§7.4、§9。

| 完了条件 | 実装 / 検証 |
|---|---|
| T・EOF・例外・skill 未観測で識別項目を保持 | `run_native_goal_probe.py`: 外の evidence に確立時点で保存。version 前後取得、送信前 turn counter、返り値との不一致も保持 |
| deadline と EOF を区別 | `AssignmentDeadlineReached` / `RpcEOFError`、`wait_end_reason`。Mission と Goal の deadline は `assignment_deadline_reached`。最後の Goal 観測を保持 |
| pre-turn / post-run の口 | `pre_turn(workspace, thread_id, evidence)` / `post_run(workspace, evidence)`。pre の失敗は turn 0、post の例外でも record を保持。post は host 終了後 |
| harness の初期化と公開範囲 | pre は verified-complex だけ。baseline/Goal の `session_init` は構成不一致。init/state は照合に必要なキーに射影し、課題文・自由文・workspace を公開しない。保持した policy/history の私有 path は既存の秘匿ガードで扱う |
| 全経路で state を読む | `evaluation_integrity.read_evaluated_state`: 正確な `cx-<thread ID>` の head を authoritative reader で読む。mtime・turn 完了に依存しない。新規 v5 と reactivate 後の実 state で bytes 不変を検証 |
| Mission 2 版の識別 | `check_record` が label と source/package の組で識別。Goal が同じ package を使う場合も区別 |
| policy・session の構成照合 | template digest、`reactivate: forbidden`、起点 + T − m_post、init/post の session/mission ID・policy、空の履歴を検査。state 不在・不正・policy なし再初期化・置換は `non_quality` |
| run ごとの deadline の差を許す | digest は deadline を除く正準 JSON。正しい deadline だけが異なる record 群は全件通過 |
| exec stream の保存・走査 | modern item と legacy exec event の command/cwd を順序どおり保存。T・例外でも走査し、検出は `evaluated_session_tampered`。verified の stream 不在・走査不能は `evaluated_session_unverifiable` |
| scanner の除外・再帰・fail-closed | `shell_syntax.py` が quote・語・代入・置換・here-doc の境界を保持し、複合文を単純 command へ分解。記録済み絶対 argv だけを除外。fd 複製/閉鎖は file と区別。8 shell、case の継続、cwd の scope/前の cwd、case-insensitive/glob/brace と未知 write destination を検証。不明な構文は検出 |
| baseline の走査不能を記録 | identity 欠落・scanner 例外・既存の `exec_scan_error` は診断と `unobserved: exec_event_stream` に保持。verified の non_quality 判定は維持 |
| home 配下の record 永続化 | `record_paths.write_probe_record` が home/workspace/package/interpreter の prefix を固定の絶対 placeholder に置換。同じ map を path、command、cwd、入れ子と key に適用し、raw と保存予定 bytes の全走査結果を比較。秘匿ガードは変更しない |
| 保存不能な私有 trace でも record を残す | 未知の私有 path、alias 衝突、走査差は安全に秘匿し、assignment/manifest を保持。Mission は `non_quality / evaluated_session_unverifiable`、Goal は `non_quality / execution_config_mismatch`。消した stream は null にし、baseline/Goal も checker が拒否 |

## 後続が使う API

- `run_codex_assignment(..., evidence=None, pre_turn=None, post_run=None, mission_edition="baseline")` は 1 run の観測 dict を返す。`main` と `probe_codex` も edition を受け取る。harness は verified-complex の package を選んだ場合だけ `mission_edition="verified-complex"` を渡す。Goal と既定の baseline は pre-turn を呼ばない。
- pre-turn は読み戻した `session_id`・`mission_id`・`budget_policy` を持つ dict を返す。probe がその policy の template digest を `session_init.budget_policy_template_sha256` に記録する。runner が実際に渡す policy は `evidence.budget_policy` に保持する。
- post-run は観測の dict を返す。指定しなければディスクの authoritative reader を使う。状態が無い場合は `mission_state: null` と error type を残す。
- reader は全文を返すが、record の init/state は `session_id`・`mission_id`・`budget_policy`・`reactivation_history`・`budget_policy_template_sha256` の存在するキーだけに射影する。未観測の null 項目は保存時に省略する（不在を明示する state/stream、初期化の存在、Goal の skill 非配布は null を保持）。条件の値は型も一致させる。
- live hook と reader は実 path を使う。保存境界だけ `write_probe_record(path, record, roots)` を使い、元の evidence を変更しない。placeholder は `/__mission_paths__/home`・`workspace`・`package`・`interpreter/<basename>`。最長 prefix と path 境界を使う。writer の既存の秘匿・digest 処理も含めた内容を走査し、結果が変わる場合は `record_persistence_error` を保持する。
- `check_record(record, planned_arms, expected_arm=None)` の arm key は `native_goal` / `mission_baseline` / `mission_verified_complex`。仕様値は `conditions`（設計 §2.2 の 9 項目）、`mission_source_commit`、`package_sha256`、`provider_version`、`initial_sha256`。verified は `budget_policy_template_sha256` と `m_post` も必要。
- 返り値の `matches` は構成の適格性だけ。`classification: non_quality` は照合失敗だけに付ける。品質の成否、T/EOF の品質分類、全割当の会計は I1 が担う。baseline の観測不能は `unobserved` に列挙する。

## 検証とテストの検出価値

- 今回の対象 237 件通過（5.30 秒）: `test_issue924_probe_integrity.py` 193 件、既存 probe 44 件。success/deadline/EOF/例外/起動失敗/version 失敗 × 3 arm の main が保存する 18 record を既存 schema で検証。課題文の除外、初期化境界、bool/int の拒否、symlink package の正準 path と匿名化を保護する。fixture/fake とローカル state CLI のみ。実 provider・smoke・pilot は実行していない。
- 関連ガード 85 件通過（6.61 秒）: artifact hygiene、neutral vocabulary、thin-adapter。scanner は今回実行・変更していない。
- 層/import・依存境界の対象 8 件通過（23.73 秒）。未観測値の省略をメモリ上で外した故障注入では、起動失敗 record の `config_matches=null` が schema 検証で拒否され、追加テストの検出価値を確認した。独立確認は照合境界 54 入力と新規対象テストを実施し、差分に追加の欠陥は確認されなかった（正式なレビュー gate の代替ではない）。
- reviewed area は未コミット変更込みで 1,168 行（先行 PR の branch `feat/924-shell-exec-scan` との差分）。生成物の除外はない。
- thin-adapter ratchet と `git diff --check` は通過。ratchet は先行 PR の head `902e8becc7508fd556ae622c2791e2a541816e01` の記録済み baseline と比較した（`base=recorded-baseline`）。
- 前回の独立入力探索: scanner 49 件 + policy/record 7 件を実測。malformed record が例外になる反例を Red にして修正。正式なレビュー accepted の代替にはしていない。
- 前回の永続化の追加 Red: 固定した匿名 home 配下の interpreter/workspace/package path で main の 3 件が元の record を失うことを再現。正常/拒否の正規化 62 件、未知 trace の保存 20 件、既存 writer の秘匿による走査差 1 件、baseline/Goal の拒否 2 件で保護。独立 writer matrix 61 件も全件通過。未引用の空白 path 等で走査差が出た 10 件は、検出結果を変えて保存せず non_quality に落とすことを確認した。
- 既存 #882 テストは Goal 自体の忠実度・候補保持を守る。今回の追加は「timeout を EOF と混同する」「例外で stream が消える」「worker の再初期化・session 置換を見逃す」「読み取り argv の除外が後続 command の書き込みを隠す」不具合を検出する。重複する Goal テストは追加していない。既存 3 fake の signature だけ新しい hook 引数に合わせた。
- 今回の shell 修正は最初の Red で 64 件失敗を確認してから実装。fd・quote・複合文・代入・here-doc・shell argv・cwd scope・case/glob/変数を同じ entry point の正常/拒否 table で守る。独立探索 t1〜t4 の 375 入力を最終 source で再実行。旧期待との差 9 件は一般 command の state 読み取り 3 件（設計では検出）、明示的な対象外の script file 4 件と path 結合 1 件、下記の非 shell 相対 literal 1 件。独立確認は正式な accepted の代替にしていない。
- 多数の拒否入力は同じ scanner entry point の parameter table に集約。実 process を使う追加は v5 reader の 2 ケースのみで、初期状態と reactivate 後の異なる履歴を守る。full suite は実行していない。

## 残作業・制約

- F2c の `init --budget-policy` は未実装なので、有効化・init の実結線・kernel の強制は加えていない。I3 が policy 生成、init、読み戻し、継続入力文面を結線する。init の環境から優先する session ID 変数を除くことも I3 の責務。app-server の側は本変更で除く。
- CLI の G の `outcome` / `fidelity` の語彙と判断は維持。新しい record checker を G の勝敗へ結線していない。I1 がこの checker の結果を使う。
- exec stream の command 全文には worker が渡した課題文が含まれうる。設計が stream の保存を要求するため今回変更していない。公開前の扱いは owner の判断事項として親が PR に記録する。
- scanner は shell の完全な interpreter ではない。未対応の構文を検出し、script file・source/alias の展開は追わない。難読化・process 内部の操作という凍結済みの限界も維持。具体例: `cd .mission-state && python3 -c "open('a','w')"` は検出しない。非 shell 本文は `.mission-state` を含む path の literal のみを走査するという §2.1 の範囲を保ち、Python 等の内部 cwd・API の解釈は追加していない。
- 実 host の event の完全性と全文は未確認。これは fixture 検証で保証できないため、承認された後続 smoke の対象。
- 未知 path を秘匿すると正確な session 操作の再走査を保証できないため、`evaluated_session_unverifiable` を選んだ。package は既に準備済みなので `package_prepare_failed` にはしない。消した stream を空の観測と扱わず、保存失敗の情報を checker が全 arm で拒否する。ディスク自体が書けない場合の record 保存は保証できない。

## PR の分割

reviewed area が分割必須の閾値（1,400）を超えたため 2 本に分けた。scanner 単体の 7 テスト関数（78 ケース）を先行 PR へ移し、移動前後で合計 381 件（test 名と parameter ID の multiset）が一致することを確かめた。

- 先行 PR（scanner）: `shell_syntax.py`・`exec_event_scan.py`・`test_issue924_shell_scan.py`
- 本 PR（probe・照合・永続化。先行 PR の上に積む）: `run_native_goal_probe.py`・`evaluation_integrity.py`・`record_paths.py`・`native_goal_benchmark.py`・`test_issue924_probe_integrity.py`・`test_issue882_native_goal_benchmark.py`・本書
