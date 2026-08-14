# Design: publish transaction の失敗診断強化（Issue #468）

## 目的

CI でのみ再発する `output publish changed`（`skills/mission/bin/mission-state.py`）について、**次に落ちたときに一次情報だけで原因を判別できる状態**を作る。現在のメッセージは失敗理由を 1 語に潰しており、inode 不一致とサイズ不一致のどちらで落ちたのかすら分からない。

## 背景

`_publish_output_transaction` の最終検証は次の 2 条件の OR で、どちらが成立したか区別されない。

```python
published = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
if not _same_inode(temporary_stat, published) or published.st_size != len(content):
    raise ValueError("output publish changed")
```

さらに `_same_inode` は `st_dev` / `st_ino` / `st_mode` / `st_nlink` の 4 項目を比較するため、「inode 不一致」と一括りにされている中に少なくとも 4 通りの失敗が畳み込まれている。CI 実測（Issue #468）ではローカル再現に失敗しており、値を見ないと先へ進めない。

## スコープ

やること:

- `_publish_output_transaction` 内の identity 検証失敗時に、比較した実値を例外メッセージへ含める
- 対象は同関数内の以下 4 箇所（同一クラスの検証であり、片方だけ直すと次の失敗でまた情報不足になる）
  - `publish directory changed`
  - `output temporary file changed`
  - `output changed during publish`
  - `output publish changed`
- 診断文字列を組み立てるヘルパーを 1 つ追加し、4 箇所で共有する
- テスト追加（後述）

やらないこと:

- 検証条件そのものの変更・緩和（どの条件で raise するかは一切変えない）
- 成功パスでの追加 stat / 追加ログ（成功時のコストを増やさない）
- テスト側の retry / rerun / skip の導入（flake を隠すと publish transaction の実バグを見逃す）
- `_same_inode` / `_stat_identity` の比較項目の変更
- 例外型の変更（`ValueError` のまま）

## インターフェース定義

### 既存メッセージの接頭辞を保持する

呼び出し側・テスト・運用者が既存の文字列に依存しているため、**メッセージ先頭の語句は変えない**。診断情報は `": "` に続けて付す。

```
output publish changed: reason=size expected_size=1024 observed_size=0 dev=16777232 ino=12345 mode=0o100600 nlink=1
```

### ヘルパー

```python
def _publish_identity_detail(expected: os.stat_result | None, observed: os.stat_result | None, *, reason: str, expected_size: int | None = None) -> str:
```

- `reason`: どの比較で落ちたかの短い識別子（`size` / `dev` / `ino` / `mode` / `nlink` / `identity` / `directory`）
- 出力は `key=value` を半角スペースで連結した 1 行。値は整数と 8 進数表記の mode のみ
- **パス・ファイル名・ディレクトリ名を含めない**（絶対パスがログや Issue へ流出するのを避ける。呼び出し文脈で対象は特定できる）
- `expected` / `observed` が `None` の場合は該当キーを出力しない

### reason の決定

`output publish changed` では、`_same_inode` の 4 項目を順に比較して**最初に不一致だった項目**を reason にする。すべて一致していてサイズだけ違う場合は `size`。実装は次の形に限定する（比較順序を固定し、非決定な出力を作らない）。

```python
for name in ("st_dev", "st_ino", "st_mode", "st_nlink"):
    if getattr(temporary_stat, name) != getattr(published, name):
        reason = name[3:]   # dev / ino / mode / nlink
        break
else:
    reason = "size"
```

`output temporary file changed` / `output changed during publish` は `_stat_identity`（7 項目）の比較なので、同様に最初の不一致項目を reason にする。`publish directory changed` は `_directory_identity` の比較結果を `directory` として扱い、opened / named のどちらが不一致かを reason に含める（`directory-opened` / `directory-named`）。

## 受け入れ条件

- [ ] 4 箇所すべてで、失敗時メッセージから「どの比較項目で落ちたか」と「比較した実値」が判別できる
- [ ] メッセージ先頭の既存語句（`output publish changed` 等）が保持されている
- [ ] 成功パスで追加の stat 呼び出しやログ出力が発生しない
- [ ] 診断文字列にパス・ファイル名が含まれない
- [ ] 検証条件（raise する条件）が変更されていない
- [ ] `plugins/mission/skills/mission/bin/mission-state.py` ミラーが正典と一致
- [ ] 既存テスト全緑

## テストリスト

新規 `skills/mission/tests/test_publish_diagnostics.py`:

1. `output publish changed` のサイズ不一致経路: `published.st_size != len(content)` を成立させ、メッセージに `reason=size` と `expected_size` / `observed_size` が含まれること
2. 同 inode 不一致経路: `_same_inode` が偽になる状況を作り、reason が `dev` / `ino` / `mode` / `nlink` のいずれか正しい項目になること（最初の不一致項目が選ばれること）
3. `output temporary file changed` の経路で `_stat_identity` の不一致項目が reason に出ること
4. 診断文字列にパス区切り `/` とファイル名が含まれないこと
5. 成功パスでは例外が出ず、既存の publish 結果（`_PublishedFile` の各フィールド）が従来どおりであること

異常系の作り方は既存テストの流儀に合わせる（monkeypatch で `os.stat` の戻り値を差し替える等。実ファイルシステムの race を作らない）。ヘルパー単体のテストも上記に含めてよいが、**publish 経路を通した検証を必ず 1 件以上含める**こと（ヘルパーだけのテストはトートロジーになりやすい）。
