# Issue #918: writer 容量検査の修正記録

対象: [PR #946](https://github.com/tackeyy/mission/pull/946) の既存退行と独立探索の指摘。
作業基点: `df2b9b0c1f1a2f8643bb0ab5cb0547d066cc852b`。未コミット差分で検証する。

- F1: halt / goal / lease / epoch の上限を変更値に適用する。変更しない部分 lease も保持する。
- F2: CLI が生成・転記する fallback reason を 128 文字に収める。
- F3: lease の所有・期限を先に判定し、期限切れ takeover の空 token は生成する。過去 epoch は `int()` で正規化する。
- F4: `halt --all` は session ごとの容量拒否を `errors` に記録して継続する。読み取りだけの箇所から容量例外の再送出を除く。
- F5: takeover と halt の中間・最終状態を両方 kernel で判定し、保存は一括で行う。無関係の変更は停止系として通さない。
- F6: legacy init は容量・lease の事前検査後に archive / assumptions を出力する。終端済み状態の縮小置換は新状態の全予約を genesis 判定で検査する。
- F8: legacy writer / janitor の backup は容量・lease の検査後に作成する。

kernel の変更理由: 停止系の分類にも変更値だけの上限を適用する必要がある。takeover 履歴には旧 token の正確な文字列コピーを許し、計測された `next_takeover_cost` で増分を制限する。新 token / reason / epoch の上限は維持する。

回帰テストは `skills/mission/tests/test_issue918_capacity_writers.py` の表を拡張した。
旧 epoch の正規化は `test_issue936_state_capacity_reservation.py`、kernel の過去 token コピーは `test_issue933_state_capacity_verdict.py` でも固定する。
既存 `test_lifecycle_usecases.py::test_mark_halt_remains_available_for_malformed_legacy_v4_state` の期待は変更しない。`test_issue2_init_archive.py` の I/O 失敗テストには正しい lease token を渡し、旧 state 保持の期待を維持する。

Red: 初期追加表は 24 failed / 47 passed。既存 malformed legacy halt は 1 failed / 2 passed。
独立チェック: 84 入力で旧 token の超過状態 halt 拒否を追加発見し、回帰テストと修正に反映した。数値の履歴コピーも文字列への正規化を必須にした。最終再確認では High / Medium の残存指摘なし。
検証: 容量・kernel 528 passed、層境界 / mirror / ratchet 等 98 passed、lifecycle 指定ケース / #895 / init archive 50 passed、追加 init 事前検査等 5 passed（重複あり）。origin/main 比の reviewed 行数は未コミットの本記録を含め 1,730。full suite と正式な同一 head のレビュー / Checker / CI は親の担当。

テストの検出価値: 過去値の誤拒否、新規値の素通り、停止時の容量枯渇、拒否時の backup / archive / assumptions 残留、session 間の停止伝播を検出する。共通の入力表は gate で確認し、CLI は必要な保存境界だけを確認する。
mirror は既存同期スクリプトで更新する。ratchet は減少分だけを反映する。

分割案: raw writer inventory の AST 検査と基点の保存口 manifest を先行変更に分離し、容量 gate の配線・今回の回帰修正・manifest 更新を後続に置く。先行側は基点の inventory と一致させ、容量 gate への到達チェックは後続に残す。
