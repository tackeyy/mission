# 設計: stop guard が 1 回の判定で CLI を最大 7 回起動する構造を見直す（tackeyy/mission #779）

## 現象

Stop hook は判定 1 回のために `mission-state.py` を複数回起動し、**それらが 1 つの予算を共有する。**

| 条件 | 起動回数 |
|---|---|
| state 無し / halted | 1 |
| active / stale / lease 期限切れ | 3 |
| 孤児 2 件 | 5 |
| observe の 3 試行が失敗 | 7 |

予算は 8 秒（実行 6 ＋ 予約 2）。host は hook を 10 秒で切る
（**この環境の `settings.json` の値であり、既定値ではない**。`docs/design/730-stop-guard-performance.md`）。

超えると `guard-budget-exhausted` になり、`mission Stop guard decision is unavailable` で作業が止まる。
**1 セッション中に 5 回発生したことを実測している。**

## 効果の見積もり（ここを誤らない）

**1 プロセス化は「3〜7 倍の超過を 1 倍にする」ものであり、「負荷に関わらず収まる」ものではない。**

| 負荷 | 1 回の起動 | いま（3 回） | 1 プロセス化後 |
|---|---|---|---|
| 24 | 1.28〜2.41 秒 | 4〜7 秒 | 1.3〜2.4 秒 |
| **767** | **19.4 秒** | **58 秒** | **19.4 秒** |

**負荷 767 では 1 プロセスでも 8 秒の予算を超える。** これは本 Issue では解けない。

**理由**: 19.4 秒の壁時間に対し **CPU 時間は 0.96 秒**で、**約 95% はスケジュール待ち**である。
プロセス数を減らすと待ち時間の回数が減るが、1 回あたりの待ち時間は縮まない。

**したがって、負荷が極端なときに収まらないことは受け入れる。** そこは予算超過が
`guard-budget-exhausted` という終端 verdict を出す既存の仕組みが扱う。

## 対象外

### 起動コストそのものの削減（遅延 import 等）

import は `-X importtime` の合計で 0.349 秒。**ただしこれは CPU 時間側の話**で、
0.96 秒 CPU のうちの 0.35 秒にすぎない。**壁時間に直すと 1 プロセスあたり 0.1〜0.2 秒しか戻らない。**

**#702 が「削減余地 45% は実測したが、壁時間・費用への変換先が無い」として実施しないと判断した対象**
でもある。本 Issue では扱わない。

### 予算値・打ち切り機構の変更

`docs/design/480-fifo-timeout-diagnostics.md` の「やらないこと」と
`docs/design/516-scaling-load-independent.md` の提案 3 が禁じている。
予算の設計は #742 / #749 / #754 が正典で、触らない。

### `stop-guard-observe` だけ stderr を捨てている非対称

`scripts/mission-stop-guard.sh` の `2>/dev/null`。`mark-halt` と `cleanup-stale` は捨てていない。
**この非対称を説明する test もコメントも見つからなかった。** 本 Issue の範囲外として記録に留める。

## D1. `analyze_guard_shell` の代わりに何を置くか（**最初に決める**）

**これを後回しにすると、実装が進んでから「保証が消えている」ことに気づく。**

### いま何が守られているか

`#615 の検査`は `scripts/` にも CI にも無い。**hook の文字列スキャン**である
（`skills/mission/tests/test_issue615_guard_decision.py` の `analyze_guard_shell`）。

検出する違反は 12 種で、**shell が policy 判断を持たないこと**を守っている。

| 分類 | 禁じているもの |
|---|---|
| 時刻と算術 | `$(( ))`・数値比較・`date +%s`・timestamp の比較・policy 由来の数値リテラル |
| JSON | **shell が JSON を組み立てること**（`jq -n`）・**`$GUARD_DECISION` 以外への `jq`**・state の権威フィールドを読むこと |
| 実行 | `eval` / `bash -c` / `sh -c` / 変数からのコマンド実行 |
| 命令集合 | 3 コマンドが dispatch block の外に現れること・許可外コマンド（`resume` / `reactivate`） |

**唯一の肯定的な信号**が `GUARD_DECISION_DISPATCH_BEGIN` / `END` に挟まれた `case` block で、
そのラベル集合が `{none, mark-halt, cleanup-stale, stop-guard-observe}` と**完全一致**することを
要求する（欠けても余っても落ちる）。

**ループを畳むと、この `case` block が消える。検査している対象そのものが無くなる。**

### 決定

**hook は「1 回呼んで、返った文字列を出すだけ」になる。**
そのとき守るべきものは変わり、検査も変える。

| | いま | 本 Issue の後 |
|---|---|---|
| hook が守るもの | policy 判断を持たない（12 種の違反が無い） | **同じ。ただし守る対象が縮む** |
| 肯定的な信号 | dispatch の `case` block が 4 ラベル完全一致 | **`case` block そのものが無いこと** |
| 命令集合の固定 | shell の `case` ラベル | **Python 側の dispatch 表** |

**`analyze_guard_shell` は残す。** 検出する違反 12 種のうち、時刻・算術・JSON・動的実行の分類は
**縮んだ hook にもそのまま効く**。消すのは dispatch に関する 3 つだけである。

**代わりに Python 側へ次を置く。**

1. **命令集合が閉じていることの検査。** 適用できるコマンドの集合が
   `{none, mark-halt, cleanup-stale, stop-guard-observe}` と完全一致すること。
   **欠けても余っても落ちる**（いまの `dispatch-set-mismatch` と同じ強さ）
2. **その dispatch が 1 箇所しか無いことの検査。** 適用経路が複数あると、
   片方だけ検査される状態になる
3. **hook に `case` block が無いことの検査。** 残っていれば、畳んだはずのループが戻っている

**`analyze_guard_shell` から dispatch の 3 検査を消す変更と、Python 側の 3 検査を足す変更を、
同じ PR で行う。** 片方だけ入れると、その間は保証が無い。

## D2. 4 つの結合をどう解くか

調査で、同一プロセスで適用すると壊れるものが 4 件見つかっている。

### D2-a. `MISSION_SESSION_ID` の衝突

`cmd_mark_halt` は `--session-id` を持たず、**環境変数から解決する**。hook は**子にだけ**設定している。
同じ変数を guard 自身の hook-SID 解決も読む。

**決定: 環境変数を経由しない。** 適用側が session id を**引数として受け取る**形にする。
`os.environ` を書き換えて戻す形は採らない（**戻し損ねると、次の判定がどの session を自分のものと
みなすかが変わる**）。

### D2-b. cwd

3 コマンドとも project root を**プロセスの cwd** から導く。`os.chdir()` はプロセス全体の変更で、
guard 自身の `Path.cwd()` fallback と約 40 箇所の `Path.cwd()` に影響する。

**決定: `chdir` しない。** 適用側が project root を**引数として受け取る**形にする。
hook が `cd` を `$( )` の中で行っていたのは、**変更が漏れないようにするため**だった。
同一プロセスにはその隔離が無い。

### D2-c. guard budget の decorator の入れ子

4 コマンドが `@bounded_by_guard_timeout` を持つ。入れ子自体は扱える（`min` を取り、残りを戻す）。
**問題は `_report_exhaustion` が `stop-verdict` 以外で `raise SystemExit(2)` すること。**
同一プロセスでは receipt を作らず **hook ごと落ちる。**

**決定: 適用は decorator を通さない。** 予算は `stop-verdict` の 1 つが持ち、
適用はその内側で**残り時間の中**で行う。**超過は receipt の失敗として表現し、`SystemExit` にしない。**

### D2-d. エラーの封じ込め

補助コマンドは `sys.exit(1/2)` と stderr で失敗を伝え、**receipt の仕組み全体が
「子が死んだ。終了コードはこれ」で組まれている。**

**決定: receipt の形を変えない。** 適用側は例外を捕らえ、**いまの子プロセスと同じ
`(exit_code, stdout)` の組へ変換する。** `resolve_guard_command_receipt` は変えない。

**これにより、`stop-verdict` の側から見た入力は同一になる。**

### lease / fencing は依存が無い

`flock` は open file description 単位なので、同一プロセスの別 fd でも正しく衝突する。
`os.getpid()` は `skills/mission/lib` と `mission-state.py` のどこにも無く、**fencing は PID で
鍵付けされていない。** lease の所有は、渡された hook の SID / PID を事実として照合する。

## D3. ADR-006 との関係

`docs/adr/006-kernel-reducer-adjudication.md` は「**Python で決め、shell で撃つ**」を定めているが、
その論拠は「**guard policy の authority を 1 つにすること**」である。
**プロセスを分けること自体は論拠として書かれていない。**

**application 層の I/O 禁止契約**（`runtime_guard.py` が `os` / `pathlib` / `subprocess` / `sys` /
`importlib` を import できない）は **`runtime_guard.py` を縛るもの**で、
**adapter である `mission-state.py` は元から I/O を行う。**

**したがって本 Issue は ADR-006 を変えない。** 判定は引き続き `runtime_guard.py` が純粋に行い、
効果の適用は adapter が行う。**変わるのは適用の場所が shell から adapter へ移ることだけ。**

**ADR-006 に追記する**: プロセス境界は authority の要件ではないこと、および
命令集合の閉性を Python 側で検査するようになったこと。

## D4. thin-adapter ratchet

`scripts/check-thin-adapter-ratchet.py` は関数ごとの違反予算を持ち、単調である。
**適用の dispatch を adapter へ入れると、分岐が増える。**

**決定: dispatch を 1 つの表として持ち、分岐を関数へ散らさない。**
`{kind: 適用関数}` の写像を 1 箇所に置き、そこから引く。**表の引き当ては分岐ではない。**

**予算を増やす変更を同じ PR に混ぜない。** 増やす必要が出たら、それは設計が誤っている合図として扱う。

## 受け入れ条件

1. **通常経路（state 無し / halted / active / stale / lease 期限切れ）で `mission-state.py` の
   起動が 1 回になる**
2. **observe が 3 試行とも失敗する経路でも 1 回である**
3. **孤児が複数ある経路でも 1 回である**
4. **`stop-verdict` から見た receipt の形が変わらない**（`resolve_guard_command_receipt` を変更しない）
5. **適用の失敗が `SystemExit` にならず、receipt の失敗として表現される**
6. **`MISSION_SESSION_ID` と cwd をプロセス全体で書き換えない**
7. **命令集合が `{none, mark-halt, cleanup-stale, stop-guard-observe}` と完全一致することを
   Python 側が検査する。欠けても余っても落ちる**
8. **その dispatch が 1 箇所しか無いことを検査する**
9. **hook に `case` block が無いことを検査する**
10. **`analyze_guard_shell` の残り 9 種の違反検査が、縮んだ hook に対して引き続き働く**
11. **thin-adapter ratchet の予算を増やさない**
12. **上のそれぞれについて、壊す変異を注入して落ちることを確認する**

## 測らないこと

**「負荷が高くても予算に収まる」ことを受け入れ条件にしない。** 上の見積もりのとおり、
負荷 767 では 1 プロセスでも収まらない。**測るのは起動回数であって、所要時間ではない。**

**所要時間を条件にすると、負荷に依存する検査になり、#771 で閉じた問題を再び開く。**

## テストリスト

- 起動回数: 5 つの経路それぞれで `mission-state.py` の起動が 1 回であること
- 純関数: 命令集合の完全一致（欠け・余り・未知の kind）
- 文字列: hook に `case` block が無いこと・dispatch が 1 箇所であること
- 統合: 適用の失敗が receipt の失敗として `stop-verdict` へ届くこと
- 統合: `MISSION_SESSION_ID` と cwd が適用の前後で変わらないこと
- ratchet: 予算が増えていないこと

**値は設計書に列挙しない。** 契約から導出できるのでテストコードが持つ。

## 検証コマンドの粒度

**反復中は対象テストだけを回す。** `make test`（full suite）は回さない。

```
cd skills/mission && python3 -m pytest tests/test_issue779_guard_one_process.py -q -p no:randomly
```

関連する既存テスト（実装が一通り終わってから 1 回だけ）:

```
cd skills/mission && python3 -m pytest tests/test_issue615_guard_decision.py \
  tests/test_issue742_stop_guard_timeout.py tests/test_issue754_guard_budget_reserve.py \
  tests/test_stop_hook.py -q -p no:randomly
```

**`test_stop_guard_dedupe.py` は負荷依存で落ちる。** 低負荷（load 25 未満）のときだけ回し、
落ちたら負荷を記録して切り分ける。
