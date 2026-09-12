# 設計: 統合ゲートが suite の失敗行を surface する（tackeyy/mission #770）

## 目的

`gate-and-merge` が declared suite の失敗で止まったとき、**どのテストがどう落ちたかが
ログに出ない。** 出るのは終了コードだけである。

```python
if result.returncode != 0:
    if logger is not None:
        logger("suite_exit={}".format(result.returncode))
    raise IntegrationGateError(step, "suite-failed", "integrated tree suite failed")
```

`result` は stdout / stderr を捕捉しているのに使っていない。そのため、ゲートが止まっても
**本当の欠陥か環境由来かを区別できない。**

実害: #769 でゲートを 5 回実行し、手元で同じ条件を再現して初めて中身が分かった。
合計およそ 1.5 時間を失った。

## スコープ

### やること

- `run_declared_suite` が非 0 終了を報告するとき、**捕捉した出力から失敗の所在を
  logger へ出す**

### やらないこと

- ゲートの判定ロジックの変更。失敗を失敗と判定すること自体は正しい
- 成功時の出力を増やすこと
- `test_stop_guard_dedupe.py` 等が負荷依存で落ちる問題そのもの（#771 で扱う）
- suite の実行方法・並列度の変更

## 変更対象

| ファイル | 変更 |
|---|---|
| `skills/mission/lib/integration_gate.py` | `run_declared_suite` の非 0 終了時の報告 |
| `skills/mission/tests/test_issue770_gate_failure_lines.py` | 新規 |
| `plugins/mission/skills/mission/lib/integration_gate.py` | byte-identical mirror |

## インターフェース

`run_declared_suite` の signature は**変えない**。`logger` へ渡す行が増えるだけである。

抽出は純関数として切り出す。**そうしないと、抽出の挙動を検査するのに suite の実行が要る。**

```python
def suite_failure_excerpt(stdout, stderr, *, limit): ...
```

- 戻り値は `str`
- `limit` は文字数の上限

## 決定

### D1. 出すのは stdout と stderr の両方から取る

runner は任意の argv なので、失敗の所在がどちらに出るかは決められない。
**両方を対象にし、stdout を先、stderr を後の順で連結して扱う。**

### D2. 抽出は「失敗を示す行」を優先し、取れなければ末尾を出す

**suite の出力形式に依存した抽出をハードコードしない。**
`.mission/suite-contract.json` は任意の argv を宣言できるので、pytest の書式を前提にすると
他の runner で空になる。

1. 失敗を示す行を拾う。**判定は行頭が `FAILED` / `ERROR` / `FAIL` のいずれかで始まること**
   とする。大文字小文字は区別する
2. **1 行も取れなければ、連結した出力の末尾を `limit` まで出す**
3. どちらの場合も、**それが抜粋であることが読み手に分かる形**にする

**「取れなかったら何も出さない」にしない。** それは現状と同じで、本 Issue が直したい状態
そのものである。

### D3. 上限を設け、切り捨てたことを明示する

`limit` は既定 4000 文字とする。**超えたら末尾側を残す**（失敗の要約は末尾に出ることが多い）。
切り捨てたときは、切り捨てた事実を出力に含める。

### D4. 秘匿情報は redaction しない。代わりに出力先を確認済みとする

suite の出力には絶対パスが載りうる。**ゲートのログは logger 経由でローカルの stdout へ出る
だけで、GitHub へ自動投稿されない**（`gate-and-merge` の呼び出し側が明示的に貼らない限り）。

したがって本 Issue では redaction を入れない。**入れると、いま出したい失敗行まで消しうる。**

**ただし受け入れ条件に「ログを GitHub へ貼るときは秘匿情報 precheck を通す」ことを
運用として明記する。**

### D5. 既存の `suite_exit=` 行は残す

終了コードは引き続き必要である。**追加であって置き換えではない。**

## 受け入れ条件

1. suite が非 0 で終わり、出力に `FAILED` で始まる行があるとき、その行が logger へ出る
2. 複数の失敗行があるとき、**すべて**出る（上限に達しない限り）
3. `ERROR` / `FAIL` で始まる行も同じく出る
4. 失敗を示す行が 1 行も無いとき、**末尾が出る**。かつ抜粋であることが分かる
5. 出力が上限を超えたとき、**切り捨てた事実が出る**
6. stdout にも stderr にも失敗行があるとき、**両方**出る
7. 出力が空のとき、例外にならず「出力が無かった」ことが分かる
8. **suite が成功したときの logger 出力は変わらない**
9. 既存の `suite_exit=<code>` 行は引き続き出る
10. 上のそれぞれについて、**壊す変異を注入して落ちることを確認する**

## テストリスト

`suite_failure_excerpt` の純関数テストを主とし、`run_declared_suite` 経由の統合テストを
少数置く。**runner は差し替え可能なので、suite を実際に回す必要は無い。**

- 純関数: 失敗行の抽出（単一・複数・stdout と stderr の両方）
- 純関数: 失敗行が無いときの末尾 fallback
- 純関数: 上限超過と切り捨ての明示
- 純関数: 空入力
- 統合: 非 0 終了で失敗行が logger へ届く
- 統合: 0 終了で logger の内容が変わらない

**値は設計書に列挙しない。** 契約から導出できるのでテストコードが持つ。

## 検証コマンドの粒度

**反復中は対象テストだけを回す。**

```
cd skills/mission && python3 -m pytest tests/test_issue770_gate_failure_lines.py -q -p no:randomly
```

関連する既存テスト:

```
cd skills/mission && python3 -m pytest tests/test_issue735_suite_contract.py -q -p no:randomly
```

**`make test`（full suite）は絶対に回さない。** 同じマシンで別セッションが動いており、
同時に回すと双方の結果が壊れる。full suite は最後に CC が 1 回だけ回す。
