親 Issue: #473（Wave 0 / 依存順 1 番目・P0）

# 概要

`artifact render` は **lease 検証より前に artifact ファイルをディスクへ書き出す**。lease 拒否（exit 2）が起きても artifact ファイルは既に更新済みで、state は draft のまま残る。同一コマンド内で「外部可視の効果は commit されたが state は未 commit」という部分 commit が成立しており、fencing authority を state だけが持つ現行モデルが破れている。

同じ順序問題が `artifact init` と `artifact export` にも存在する（監査時点では未指摘。本 Issue で併せて修正する）。

# 一次証拠（現在の main）

`skills/mission/bin/mission-state.py`:

| 対象 | 行 | 状況 |
|---|---|---|
| `cmd_artifact_render` | L6529–L6554 | (a) L6549 `_write_artifact` でファイル直書き → (b) `atomic_write_json` 内部（L1254 `_enforce_session_lease_for_write`）で初めて lease 検証 → (c) L6552 state 書き込み |
| `cmd_artifact_init` | L6488 | 同じく `_write_artifact` 直書きが lease 検証より前 |
| `cmd_artifact_export` | L6575 | 同上 |
| `_write_artifact` | L6402–L6407 | 実体は L6406 の `path.write_text(...)`。`_publish_output_transaction` を経由せず、ロールバック機構なし |
| `cmd_artifact_append` | L6504–L6526 | state のみ更新でファイルを書かない。**対象外** |

隔離 probe（親 Issue 記載）: token 無しの `render` は exit 2 / state は draft のまま / artifact digest は変化。

# 模範となる既存実装

`cmd_push_score`（L13209 付近）が正しい順序を実装している。本 Issue はこのパターンへ揃える。

1. L13209: `StateLock` と `_PublishedFilesTransaction` を同時に context manager として使う
2. L13212: `lease_decision = _enforce_session_lease_for_write(sf, data)` を**ファイル書き込みより前に明示的に呼ぶ**
3. L13274: ファイル書き込みは `published_files.add(_publish_output_transaction(...))` 経由でトランザクション管理下に置く
4. L13317: `atomic_write_json(sf, data, lease_decision=lease_decision)` に取得済み decision を渡し、内部の二重チェックを避ける

lease 拒否時は `_PublishedFilesTransaction.__exit__` が `_rollback_published_file` を逆順実行し、state も公開ファイルも変更前に戻る。

# 変更内容

`cmd_artifact_render` / `cmd_artifact_init` / `cmd_artifact_export` の 3 コマンドを上記 4 ステップの順序へ揃える。

- `_write_artifact` を `_publish_output_transaction` ベースへ変更するか、トランザクション対応の新関数を追加して 3 コマンドから使う（既存の `_write_artifact` を直接呼ぶ経路を残さないこと）
- 失敗・例外・lease 拒否のいずれでも、artifact ファイルが呼び出し前の内容（新規作成の場合は不在）に戻ること

## やらないこと

- artifact のフォーマット・スキーマ・`redaction_status` の意味論の変更
- `artifact append` の変更（ファイルを書かないため対象外）
- lease TTL・fencing の判定ロジック自体の変更
- `push-score` 側の変更

# 受け入れ条件

- [ ] `render` / `init` / `export` のいずれも、lease 拒否時に artifact ファイルが 1 バイトも変化しない
- [ ] 同じく、書き込み後〜state commit 前に例外が発生した場合も artifact ファイルが元に戻る
- [ ] 成功時の artifact 内容・state 更新は現行と同一（既存テスト全緑で担保）
- [ ] `_write_artifact` の直呼び出しが残っていない
- [ ] `plugins/mission/skills/mission/bin/mission-state.py` ミラーが正典と一致
- [ ] 既存テスト全緑

# テストリスト

新規または `skills/mission/tests/test_artifact_cli.py` へ追加。**現状 `test_artifact_cli.py` / `test_artifact_wiring.py` には lease / `MISSION_LEASE_ID` の参照が 1 件も無い**ため、lease 観点のテストはゼロから作ることになる。

1. `render`: 他セッションの lease を保持した状態で実行 → exit 2 かつ artifact ファイルのバイト列が実行前と完全一致
2. `init`: 同上（新規作成の場合、ファイルが**作られていない**こと）
3. `export`: 同上
4. 3 コマンドそれぞれで、ファイル公開後〜state commit 前に例外を注入 → artifact ファイルが元に戻り、state も未変更（`test_push_score.py::test_push_score_rejects_foreign_lease_before_scoring_archive_publish` L116 の assertion 方式に倣う: `state_path.read_bytes() == state_before` とファイル内容の突合）
5. 正常系: 3 コマンドの成功時出力・artifact 内容・state 更新が現行と同一

参考にする既存テスト: `skills/mission/tests/test_push_score.py::test_push_score_rejects_foreign_lease_before_scoring_archive_publish`（L116–L168）

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。先に失敗するテストを書くこと
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/bin/mission-state.py` を変更したら `plugins/mission/skills/mission/bin/mission-state.py` へ `cp` でミラー同期する
