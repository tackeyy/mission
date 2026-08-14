親 Issue: #473（Wave 1 / 依存順 6 番目・P2）Related: #468

# 概要

並列フルスイートで FIFO artifact テストが `timeout=1` により 1 回だけ失敗した（同一テスト単独では 6/6 pass）。現時点で分かっているのはここまでで、**機能不具合なのか test harness / scheduler の負荷由来なのかを分離できていない**。

親 Issue の方針どおり、#468（publish inode の flake）とは原因を混ぜず、独立に観測を強化してから分類する。

# 一次証拠（現在の main）

`skills/mission/tests/test_issue351_artifact_lint.py`:

- テスト名: `test_aggregate_fifo_artifact_skips_without_blocking_and_clears_stale_lint`
- L389: `subprocess.run([...,"aggregate-reviews",...], timeout=1, ...)`
- 検証内容: `artifact_path` が FIFO のとき `aggregate-reviews` が open でブロックせず 1 秒以内に終了し、returncode 0 / `WARN #351: artifact lint skipped` / `artifact_lint_status == "skipped"` / 古い `artifact_lint` の削除が起きること

つまり `timeout=1` は「ブロックしないこと」の証明として意図的に置かれている。一方でその 1 秒という閾値の根拠はコード上に記されておらず、CI の負荷が高いときに誤 timeout で落ちうる。

同種の短い timeout をハードコードしている箇所は他に `skills/mission/tests/test_benchmark_package.py` L489 / L494 があるが、こちらは `threading.Event.wait(timeout=1)` によるスレッド同期で性質が異なる。subprocess レベルで「ブロック防止の証明」に使っているのは上記 1 箇所のみ。

# 変更内容

**テストを甘くする（timeout を延ばす・retry を入れる・skip する）のは禁止**。それをやると FIFO ブロックの実バグを検出できなくなる。やるのは、落ちたときに原因を分類できる情報を残すこと。

1. timeout 発生時に、判定に必要な情報を診断として出す
   - 実測の経過時間
   - 子プロセスの状態（生存 / 終了コード / シグナル）
   - 可能なら子プロセスの stderr の内容（`subprocess.TimeoutExpired` の `output` / `stderr` 属性）
2. `timeout=1` という閾値の根拠をコメントで明記する（「FIFO open でブロックした場合は無限に待つため、正常系の実測所要時間より十分大きく、かつ人が待てる範囲」といった意図）。数値の意味がコードから読み取れる状態にする
3. 正常系の実測所要時間をテスト内で計測し、閾値との余裕（マージン）が診断に出るようにする。これにより「CI が遅くて 1 秒を超えた」のか「本当にブロックした」のかを次回の失敗ログで区別できる

## やらないこと

- timeout 値を大きくする、retry を入れる、`flaky` マークを付ける、skip する
- `aggregate-reviews` の FIFO 処理そのものの変更
- #468 の publish inode 診断との統合（別 finding として独立に扱う。親 Issue roadmap 6 の明示指示）

# 受け入れ条件

- [ ] timeout 失敗時のメッセージから、経過時間・子プロセスの状態・子プロセスの stderr が分かる
- [ ] `timeout=1` の根拠がコメントで説明されている
- [ ] 正常系の所要時間と閾値のマージンが分かる
- [ ] timeout 値・retry・skip の変更をしていない
- [ ] 診断出力にパス・ユーザー名などの環境固有情報を含めない
- [ ] 既存テスト全緑

# テストリスト

1. 正常系: FIFO artifact で `aggregate-reviews` が期待どおり skip し、所要時間が計測されている
2. timeout を意図的に発生させた場合（極端に小さい閾値を注入するなど）に、診断情報が期待どおり含まれる
3. 診断文字列に環境固有情報が含まれないこと

# 分類の判断は別ターン

本 Issue のゴールは**観測強化まで**。実際の分類（機能不具合か harness flake か）は、強化後の CI で再発したときのログを見て行う。再発時は #473 にログを添えて報告し、そこで別 Issue を立てるか判断する。

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
