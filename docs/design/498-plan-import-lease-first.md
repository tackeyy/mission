Refs #473 / #475（同種の修正）・#485 の設計中に発見

# 概要

`plan-import` が **lease 検証より前に evidence ファイルを外部公開**している。#475 で artifact 系 4 コマンド（init / render / export / publish）を修正したのと同じ構造が残っており、lease 拒否や kill point で「ファイルは公開済み・state は未 commit」の部分 commit が成立する。

# 一次証拠（現在の main）

`skills/mission/bin/mission-state.py` の `cmd_plan_import`（L12172 付近）:

| 行 | 処理 |
|---|---|
| L12184 | `with StateLock(...), _PublishedFilesTransaction() as published_files:` |
| L12243 | `published_files.add(_publish_review_archive_transaction(cwd, raw_name, raw))` ← 公開 |
| L12245 | `published_files.add(_publish_output_transaction(candidate_path, canonical))` ← 公開 |
| L12258 | `backup_state(sf); atomic_write_json(sf, stamp_metadata(data, cwd))` ← ここで初めて lease 検証 |

関数内に `_enforce_session_lease_for_write` の明示呼び出しが無い。`_PublishedFilesTransaction` により例外時のロールバックは効くが、**公開後〜ロールバックまでの間に SIGKILL されるとファイルだけが残る**。#475 と同じ理由で、事後ロールバックでは要件を満たさない。

# 修正方針

#475 で確立した 4 ステップへ揃える（`cmd_push_score` と同一パターン）。

1. `StateLock` と `_PublishedFilesTransaction` を context manager として開く
2. state 読み込み直後、**公開より前に** `lease_decision = _enforce_session_lease_for_write(sf, data)` を明示的に呼ぶ
3. 公開は従来どおり transaction 経由
4. `atomic_write_json(sf, ..., lease_decision=lease_decision)` に取得済み decision を渡す

## 併せて確認すること

`manual-score-capture`（`cmd_manual_score_capture`、L10393 付近）は `atomic_write_json(out, scoring)` で **state ファイル以外の出力ファイル**を書く（L10449）。この経路に lease 保護が必要か（そもそも state を変更しない read-only + 出力生成なのか）を判断し、必要なら同じ扱いにする。不要と判断した場合はその理由を PR に明記する。

`_publish_output_transaction` / `_publish_review_archive_transaction` を呼ぶ全箇所を grep し、他に同じ構造が残っていないか確認すること（#475 では `render` だけの指摘から調査で 4 コマンドに広がった）。

# やらないこと

- lease / fencing の判定ロジック自体の変更
- plan document のスキーマ・検証内容の変更
- `push-score` 側の変更
- artifact 系 4 コマンド（#475 で対応済み）

# 受け入れ条件

- [ ] `plan-import` が lease 拒否時に evidence ファイルを 1 バイトも変化させない
- [ ] lease 拒否時に公開そのものが起きない（結果的に元に戻ることの確認では不十分。`_publish_*` が呼ばれないことを検証する）
- [ ] 成功時の evidence 内容・state 更新は現行と同一
- [ ] `_publish_*` を呼ぶ他の経路に同じ構造が残っていないことを確認済み
- [ ] plugins ミラー一致・既存テスト全緑

# テストリスト

`skills/mission/tests/` の該当ファイルへ追加。参考実装は `test_artifact_cli.py`（#475 で追加した lease テスト群）と `test_push_score.py::test_push_score_rejects_foreign_lease_before_scoring_archive_publish`。

1. 他セッションの lease を保持した状態で `plan-import` → exit 2 かつ evidence ファイル・state ともにバイト列が実行前と一致（Red になるはず）
2. 同状況で `_publish_output_transaction` / `_publish_review_archive_transaction` が一度も呼ばれない（monkeypatch で呼び出しを記録）
3. 公開後〜state commit 前に例外を注入 → 元に戻る
4. 正常系の出力・state 更新が現行と同一

# 実装上の注意

- TDD（Red → Green → Refactor）
- parametrize に lambda を値として入れない。入れる場合 ids は明示する
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する

