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

---

# 事後の分類結果と、その結果に基づく方針の更新（2026-08-30 / PR #710・Issue #703）

**本文の「やらないこと」に書いた timeout 引き上げ禁止は、その後の分類結果により解除された。** 現在の実装は `timeout=1` ではなく無限ブロック検出用の watchdog 60 秒であり、あわせて経過時間の assert を削除している。本節はその経緯と、禁止が有効だった前提を記録する。

## 禁止が有効だった前提

本文は「**機能不具合なのか test harness / scheduler の負荷由来なのかを分離できていない**」ことを明示的な前提としていた。分離できていない段階で timeout を緩めると、FIFO ブロックの実バグを検出できなくなる。この前提の下では禁止が正しい。

## 前提が崩れたこと（一次証拠）

本文が義務づけた診断出力は実際に機能した。

- 2026-08-26: 予算 1 秒に対し 1.005 秒（margin -0.005s）で CI が fail、再実行で green。この時点で 1 → 5 秒へ引き上げられている
- 2026-08-29: 予算 5 秒でも full suite で 5.025 秒の `TimeoutExpired` により fail。**このときの stderr には既に `WARN #351: artifact lint skipped` が出ていた**

2 度目の失敗ログが決定的だった。skip の WARN が出ているということは、`aggregate-reviews` は artifact が regular file でないことを検出して lint を skip しており、**FIFO を読みに行ってブロックしてはいない**。すなわち機能は正しく、完了が遅かっただけである。本文が求めた分類はここで完了した。

## 分類後の方針

分離の結果が「負荷由来」であった場合に限り、次を満たす形で timeout を watchdog へ広げてよい。

1. **検出したい失敗を先に定義する。** 本件は「FIFO を読みに行って無限にブロックすること」であり、「実行が速いこと」ではない
2. **実行速度を成功条件にしない。** 経過時間の assert（性能 SLA）はテストから除く。無限ブロック時はそもそも完走しないため、watchdog を広げても検出力は落ちない
3. **検出力を実証する。** 終わらない子プロセスを与えて watchdog が発火することと、遅いだけの子を落とさないことを、それぞれテストで固定する
4. **値の根拠を実測で示す。** アイドル時の実測と、負荷下での膨張率を根拠として記録する

分類が済んでいない flake に対しては、**本文の禁止が引き続き有効**である。まず診断出力を入れて分類する。

## 現在の実装（正典はコード）

`skills/mission/tests/test_issue351_artifact_lint.py`:

- `FIFO_ARTIFACT_BLOCK_WATCHDOG_SECONDS = 60` — アイドル実測 0.44〜0.48 秒に対し約 130 倍。10 並列負荷下では約 11 倍に膨らむ実測を根拠にしている
- 経過時間の assert は削除済み
- `_run_under_block_watchdog` として watchdog を注入可能にし、検出力を `test_block_watchdog_detects_a_child_that_never_finishes` と `test_block_watchdog_accepts_a_slow_child_that_finishes` で固定

同じ扱いを `test_score_provenance.py` の hang 検出（`_HANG_WATCHDOG_SECONDS`）と `test_provider_application_guard.py` の子プロセス待ち（`_PUBLISH_WATCHDOG_SECONDS`）にも適用した。いずれも子 CLI プロセスの起動コストを含む予算で、余裕が実証済み破綻値と同程度だったもの。

**据え置いたもの**: 余裕 20 倍の 10 秒予算（`test_issue543_c2.py`）と、同一プロセス内で待つ `threading.Event.wait`。後者は子 CLI の起動コストを含まないため、実証された失敗モードに当たらない。
