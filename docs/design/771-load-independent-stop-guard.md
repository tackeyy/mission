# 設計: 実時間に依存する検査が merge gate を止める（tackeyy/mission #771）

## 現象

`test_stop_guard_dedupe.py` と `test_stop_hook.py` が負荷依存で落ちる。
統合ゲートは full suite の失敗で止まるので、**コードが正しくても確率的に merge を止める。**

同じコード・同じツリーで負荷だけを変えた実測。

| load の 1 分値 | `test_stop_guard_dedupe.py` の失敗 |
|---|---|
| 391 | 10 件 |
| 35 | 4 件 |
| 10 | **0 件** |

失敗の形は 1 つで、ガードが返す理由が差し替わる。

```
E       AssertionError: assert '未達一覧' in 'guard-budget-exhausted'
```

## 原因（実測）

### 1. 予算は実時間で、引き上げられない

`bounded_by_guard_timeout` が `resolve_deadline()` から残りを計算し、`SIGALRM` で打ち切る。
予算は既定 8 秒で、受理集合は `{"3".."8"}` として Python と shell adapter の両方に
リテラルで書かれている。host が hook 全体を 10 秒で切るため、**guard の上限はその内側に
収める必要がある。**

### 2. hook は 1 回の実行で CLI を最大 5 回起動する

`scripts/mission-stop-guard.sh` は `_mission_state_bounded` を通じて
`stop-verdict` / `mark-halt` / `cleanup-stale` / `stop-guard-observe` / 再度の `stop-verdict`
を呼ぶ。**それらが 1 つの 8 秒予算を共有する。**

### 3. CLI の起動が重い

`mission-state.py --help` の所要を実測した。load の 1 分値は 24 だった。

```
real 1.50
real 1.28
real 2.41
```

**1 回あたり 1.3〜2.4 秒。** 典型的な dedupe のテストは 2〜3 回起動するので、
**起動だけで予算の半分前後を使う。** 負荷が上がると線形に伸び、8 秒を超える。

**つまりこれはテストだけの問題ではない。** 同じ負荷なら**本番の hook も同じく
`guard-budget-exhausted` へ落ちる。** テストはそれを可視化しているだけである。

### 4. 予算そのものは別のテストが決定論的に固定している

`test_issue742_stop_guard_timeout.py`（40 件）と `test_issue754_guard_budget_reserve.py`
（27 件）が `resolve_deadline(env, now=now)` のように**時刻を注入して**予算の挙動を
固定している。**実時間には依存していない。**

**したがって、予算の挙動を守るために他のテストが実時間で予算を消費する必要は無い。**

## 制約

**タイムアウトの緩和・retry・skip・flaky マークは採れない。**
`docs/design/480-fifo-timeout-diagnostics.md` の「やらないこと」と
`docs/design/516-scaling-load-independent.md` の提案 3 が明示的に禁じており、同型の #714 でも
同じ判断をしている。516 は本筋を「計測方法を wall-clock から負荷非依存の指標へ変える」と
定めている。

## スコープ

### やること

- `test_stop_guard_dedupe.py` と `test_stop_hook.py` が、**予算を消費しきるかどうかに
  依存しなくなる**こと

### やらないこと

- 予算値の変更、`SIGALRM` による打ち切りの削除、retry、skip、flaky マーク
- **CLI の起動コストの削減**。効果は大きいが範囲が別で、#730 が扱っている
- hook が CLI を呼ぶ回数の削減。同上
- `test_issue742` / `test_issue754` の変更。予算の挙動はそこが正典

## 決定

### D1. テスト実行では予算の**起点**を各コマンドの開始時刻にする

**予算そのものを消さない。** 打ち切りの機構も、値も、受理集合も変えない。
変えるのは **「いつからの 8 秒か」** だけである。

現状は hook 全体で 1 つの deadline を共有し、`DEADLINE_ENV_VAR` で引き継ぐ。
**本番ではそれが正しい**（host が hook 全体を 10 秒で切るので、合計を抑える必要がある）。

テストでは、**各 CLI 起動がそれぞれ 8 秒を持つ**ようにする。こうすると、

- **1 コマンドあたりの打ち切りは従来どおり効く**（`SIGALRM` はそのまま）
- **合計時間に対する上限は無くなる**ので、起動コストが積み上がって落ちることが無くなる

**この切り替えは本番へ漏らさない。** 下の D2 で経路を閉じる。

### D2. 切り替えは「継続でないこと」を宣言する既存の env で行わない。専用の env を置く

新しい環境変数 `MISSION_STOP_GUARD_PER_COMMAND_BUDGET` を置く。**値が `1` のときだけ**
`resolve_deadline` が `DEADLINE_ENV_VAR` を読まず、常に `now + budget` を返す。

**既存の変数を再利用しない。** `CONTINUATION_ENV_VAR` や `DEADLINE_ENV_VAR` の意味を
変えると、本番の fail-closed（継続で予算を失ったら止まる）に影響する。

**この変数は hook が設定しない。** テストの `env_extra` だけが渡す。
したがって**本番の経路では常に未設定**であり、挙動は 1 ビットも変わらない。

### D3. 予算を跨ぐ挙動を検査するテストは、この切り替えを使わない

`test_issue742` / `test_issue754` は予算そのものを見るので、**この変数を設定しない。**
`test_stop_guard_dedupe.py` と `test_stop_hook.py` だけが設定する。

**どちらが設定するかを、テスト側の共有ヘルパー 1 箇所で決める。** 各テストが個別に
書くと、新しいテストで付け忘れて元の不安定さが戻る。

### D4. 切り替えが本番へ漏れていないことを検査する

**変数を設定しなければ従来どおりであること**を固定する。具体的には、

- `resolve_deadline` が、変数なしでは `DEADLINE_ENV_VAR` を従来どおり読むこと
- 変数ありでは読まないこと
- **`scripts/mission-stop-guard.sh` がこの変数を設定していないこと**（文字列検査）
- **継続で予算を失ったときの fail-closed が、変数の有無に関わらず保たれること**

最後の 1 つが要点である。**変数が `CONTINUATION_ENV_VAR` の判定を素通りさせてはならない。**

### D5. 予算が尽きた事実は引き続き観測できる

切り替えを使うテストでも、**1 コマンドが 8 秒を超えれば従来どおり `guard-budget-exhausted`
になる。** 起動コストの積み上がりだけが対象から外れる。

**「テスト中は予算が不活性になる」わけではない。**

## 受け入れ条件

1. `MISSION_STOP_GUARD_PER_COMMAND_BUDGET=1` のとき、`resolve_deadline` は
   `DEADLINE_ENV_VAR` を読まず `now + budget` を返す
2. 変数が無いとき、`resolve_deadline` の挙動は現状と同一である
3. 変数が `1` 以外（空・`0`・`true`・未知の値）のときも現状と同一である
4. **継続で予算を失ったときの `GuardBudgetLost` は、変数の有無に関わらず送出される**
5. **`scripts/mission-stop-guard.sh` はこの変数を設定しない**
6. `test_stop_guard_dedupe.py` と `test_stop_hook.py` が、共有ヘルパー 1 箇所でこの変数を
   設定する。**個々のテストが直接設定しない**
7. `test_issue742_stop_guard_timeout.py` と `test_issue754_guard_budget_reserve.py` は
   この変数を設定しない
8. 切り替えを使っても、**1 コマンドが予算を超えれば `guard-budget-exhausted` になる**
9. 上のそれぞれについて、**壊す変異を注入して落ちることを確認する**

## テストリスト

- 純関数: `resolve_deadline` の変数あり / なし / 不正値
- 純関数: 継続の fail-closed が変数に影響されない
- 文字列: hook が変数を設定していない
- 文字列: 対象 2 ファイルが共有ヘルパー経由で設定し、他の 2 ファイルが設定しない
- 統合: 1 コマンドが予算を超えれば従来どおり打ち切られる

**値は設計書に列挙しない。** 契約から導出できるのでテストコードが持つ。

## 検証コマンドの粒度

**反復中は対象テストだけを回す。**

```
cd skills/mission && python3 -m pytest tests/test_issue771_per_command_budget.py -q -p no:randomly
```

関連する既存テスト（実装が一通り終わってから 1 回だけ）:

```
cd skills/mission && python3 -m pytest tests/test_issue742_stop_guard_timeout.py tests/test_issue754_guard_budget_reserve.py tests/test_stop_guard_dedupe.py tests/test_stop_hook.py -q -p no:randomly
```

**`make test`（full suite）は絶対に回さない。** 同じマシンで別セッションが動いている。

## 残る問題（本 Issue では解かない）

**本番の hook も、負荷が高ければ同じく `guard-budget-exhausted` へ落ちる。**
CLI 起動が 1 回あたり 1.3〜2.4 秒（load 24 で実測）で、hook は最大 5 回呼ぶ。

**これは #730（stop guard performance）の範囲である。** 本 Issue はテストが
merge を止めることを解くもので、**本番の余裕そのものは別に扱う。**
