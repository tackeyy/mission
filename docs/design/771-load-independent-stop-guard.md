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
リテラルで書かれている。**同梱の Claude hook 設定**（`claude-hooks/hooks.json`）が hook 全体を
10 秒で切るため、guard の上限はその内側に収める必要がある。

**10 秒は全ホスト共通の値ではない**（round 1 の Low）。Codex や利用者の設定では違いうる。
対象ホストの限定は `docs/design/730-stop-guard-performance.md` の整理に合わせる。

### 2. hook は 1 回の実行で CLI を最大 5 回起動する

`scripts/mission-stop-guard.sh` は `_mission_state_bounded` を通じて
`stop-verdict` / `mark-halt` / `cleanup-stale` / `stop-guard-observe` / 再度の `stop-verdict`
を呼ぶ。**それらが 1 つの 8 秒予算を共有する。**

**回数は固定ではない。** `docs/design/730-stop-guard-performance.md` は observe の 3 試行が
失敗すると 7 回、孤児 2 件で 5 回になると記録している（round 1 の Low で指摘された。
当初「最大 5 回」と書いたのは不正確だった）。**共有する 1 つの予算に対して回数が変動する**
という点が本 Issue に効く。

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

### 4. 予算そのものは別のテストが直接固定している

`test_issue742_stop_guard_timeout.py`（40 件）と `test_issue754_guard_budget_reserve.py`
（27 件）が、予算・reserve・alarm・hook 全体での共有を**直接**固定している。
時刻を注入する検査（`resolve_deadline(env, now=now)`）が主だが、
**`time.sleep` / `time.monotonic` / 実 deadline / `SIGALRM` を使う検査も含む**
（round 1 の Low で指摘された。当初「実時間に依存していない」と書いたのは不正確だった）。

**要点は「実時間を使っていないこと」ではない。** 予算の契約を**直接**固定する場所が
別にある以上、**機能テストが偶発的に総予算を消費して同じ契約を重ねて検査する必要は無い**、
ということである。

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

### D2. 本番の hook が変数を**明示的に除去**する（継承を塞ぐ）

新しい環境変数 `MISSION_STOP_GUARD_PER_COMMAND_BUDGET` を置く。**値が `1` のときだけ**
`resolve_deadline` が carried deadline を**予算の起点として採らず**、常に `now + budget` を
返す（読むこと自体はやめない。D4 を参照）。

**既存の変数を再利用しない。** `CONTINUATION_ENV_VAR` や `DEADLINE_ENV_VAR` の意味を
変えると、本番の fail-closed に影響する。

#### 「hook が設定しない」では隔離にならない

round 1 の High。`scripts/mission-stop-guard.sh` は **親の環境をそのまま継承**して
`python3` を起動する。したがって **利用者や起動元がこの変数を持っていれば、本番でも
効いてしまう。** 「hook が設定しない」は「本番では未設定」を保証しない。

**本番の hook は、この変数を明示的に取り除いてから CLI を起動する。**

```
_mission_state_bounded() {
  ... env -u MISSION_STOP_GUARD_PER_COMMAND_BUDGET python3 "$MISSION_STATE_PY" "$@"
}
```

**これで隔離の向きが逆になる。** 変数が有効になるのは
**hook を経由せずに CLI を直接起動した場合だけ**であり、それがテストの経路である。

`env -u` が使えない環境では、shell の中で `unset` してから起動する。
**どちらにせよ、除去は hook の内側で行う。**

### D3. 予算を跨ぐ挙動を検査するテストは、この切り替えを使わない

`test_issue742` / `test_issue754` は予算そのものを見るので、**この変数を設定しない。**
`test_stop_guard_dedupe.py` と `test_stop_hook.py` だけが設定する。

**どちらが設定するかを、テスト側の共有ヘルパー 1 箇所で決める。** 各テストが個別に
書くと、新しいテストで付け忘れて元の不安定さが戻る。

### D4. 継続の検証は続け、**値だけ**採らない

round 1 の Medium。現行の `resolve_deadline` は carried deadline の**存在・形式・上限**を
検証したうえで、無効なら `CONTINUATION_ENV_VAR` を見て `GuardBudgetLost` を上げる。

**「変数ありでは carried deadline を読まない」と書くと、この検証も消える。** そうすると
継続で予算を失っても止まらなくなる。

**決定を 1 つに揃える。**

| 段階 | 変数なし | 変数あり |
|---|---|---|
| carried deadline を読む | 読む | **読む（検証のため）** |
| 無効なら継続で `GuardBudgetLost` | 上げる | **上げる（同じ）** |
| 有効なら予算の起点として採る | **採る** | **採らない。`now + budget` を返す** |

**変わるのは最後の 1 行だけである。** 継続 token の妥当性検査は従来どおり働く。

### D5. 切り替えが本番へ漏れていないことを検査する

- `resolve_deadline` が、変数なしでは従来どおり carried deadline を採ること
- 変数ありでは採らず `now + budget` を返すこと
- **継続で予算を失ったときの `GuardBudgetLost` が、変数の有無に関わらず送出されること**
- **`scripts/mission-stop-guard.sh` が CLI 起動時にこの変数を除去すること**

最後の 1 つが round 1 の High への答えである。**「設定しない」ではなく「除去する」を
検査する。** 親から継承した値が子へ届かないことを、hook を実際に走らせて確かめる。

### D6. 終端理由の統一は本 Issue の範囲外

round 1 の Medium。予算超過で `guard-budget-exhausted` という**終端 verdict** を作るのは
`cmd_stop_verdict` だけである。`mark-halt` / `cleanup-stale` / `stop-guard-observe` は
stderr へ理由を出して非ゼロ終了し、shell はその stderr を receipt へ渡さない。

**受け入れ条件 8 の対象は `stop-verdict` に限る。** 補助 command の超過が別の最終理由に
なることは、本 Issue では変えない。**変えるなら伝播の設計が要り、それは #730 の範囲である。**

**この限定を書かないと、実装者が伝播まで作ろうとする。**

## 受け入れ条件

1. `MISSION_STOP_GUARD_PER_COMMAND_BUDGET=1` のとき、`resolve_deadline` は
   `DEADLINE_ENV_VAR` を読まず `now + budget` を返す
2. 変数が無いとき、`resolve_deadline` の挙動は現状と同一である
3. 変数が `1` 以外（空・`0`・`true`・未知の値）のときも現状と同一である
4. **継続で予算を失ったときの `GuardBudgetLost` は、変数の有無に関わらず送出される**
5. **`scripts/mission-stop-guard.sh` は CLI 起動時にこの変数を除去する。**
   親の環境に `MISSION_STOP_GUARD_PER_COMMAND_BUDGET=1` があっても、hook 経由の CLI には
   届かない（hook を実際に走らせて確かめる）
6. `test_stop_guard_dedupe.py` と `test_stop_hook.py` が、共有ヘルパー 1 箇所でこの変数を
   設定する。**個々のテストが直接設定しない**
7. `test_issue742_stop_guard_timeout.py` と `test_issue754_guard_budget_reserve.py` は
   この変数を設定しない
8. 切り替えを使っても、**`stop-verdict` が予算を超えれば `guard-budget-exhausted` になる**。
   補助 command の終端理由は本 Issue では変えない
9. 上のそれぞれについて、**壊す変異を注入して落ちることを確認する**

## テストリスト

- 純関数: `resolve_deadline` の変数あり / なし / 不正値
- 純関数: 継続の fail-closed が変数に影響されない
- 統合: hook 経由の CLI に、親から継承した変数が届かない
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
