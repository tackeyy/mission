# 730: stop guard の所要時間 — 測定結果

**本文書は測定の記録である。検査方式は決めていない。** 決定は #742 で行う。

初版は「検査方式を決める」ものとして書いたが、**依拠していた前提が 2 つとも
誤っており**、8 ラウンドの異系統レビューで順に判明した。**確認できたことだけを
残す形へ改めた。**

## 上限は 3 層あり、実効値は 10 秒である

| 層 | 値 | 条件 |
|---|---|---|
| **ホスト（Claude Code / Codex）** | **10 秒** | hook 全体。`settings.json` の `"timeout": 10` |
| `_mission_state_bounded` | `MISSION_STATE_TIMEOUT`（既定 30 秒） | `stop-verdict` 1 回。**下記の条件付き** |
| なし | — | 上 2 つが効かない場合 |

**hook 全体を縛るのはホスト側の 10 秒である。** 30 秒は `stop-verdict` 1 回あたりの
上限で、しかもホストの 10 秒より緩いため、**実際には先にホスト側が切る**。

公式仕様では、timeout でホストが hook を中止し出力を破棄する。Stop の連続 block も
8 回で打ち切られる。**したがって「Stop が永久に止まる」ことはない。**

### `_mission_state_bounded` の上限は条件付き

```sh
if command -v timeout; then timeout "$MISSION_STATE_TIMEOUT" ...
elif command -v perl;  then perl -e 'alarm shift; exec @ARGV' "$MISSION_STATE_TIMEOUT" ...
else                        python3 ...        # 上限なし
fi
```

- **`timeout` も `perl` も無ければ上限が掛からない**
- **`MISSION_STATE_TIMEOUT=0` では `perl` の `alarm 0` が無効**になり、`perl` 経路でも
  上限が掛からない。値の検証は無い
- 上限が掛からない経路では、「返らなければ `block`」にも到達しない

**測定したホストには `timeout` が無く、`perl` の分岐で動いていた。**

## hook の実行経路

`while` ループで、state の状態によって呼び出し回数が変わる。

**正常系（補助コマンドが成功し、対象 state が 1 件）:**

| state | CLI 呼び出し | 内訳 |
|---|---|---|
| 無し / halted | **1 回** | `stop-verdict` |
| **active / stale / lease 期限切れ** | **3 回** | `stop-verdict` → 補助コマンド → receipt 付き `stop-verdict` |

**正常系から外れると増える。** 異系統レビューが `stop-guard-observe` の 3 試行が
すべて失敗した場合に 7 回、孤児 state 2 件で 5 回を実測している（**こちらでは
再現できていないため引用**）。`mark-halt` / `cleanup-stale` の失敗では 3 回のまま
であり、「補助コマンドが失敗すれば増える」という一般化は成り立たない。

## 測定

active state の hook を 8 回ずつ実行した。負荷は同ホストの `make test`。

| load average | hook 全体の壁時間（中央値 / 最大） | CPU 時間（中央値） |
|---|---|---|
| 1.9 | 0.893s / 0.899s | 0.880s |
| 9.8–11.6 | 1.488s / 1.587s | 1.257s |

**ホスト側の 10 秒に対する余裕は、高負荷側の最大値（1.587s）基準で 6.3 倍。**

### 再現手順

1. `/tmp/hooktest` で `git init -b main` し、`user.email` / `user.name` を設定。
   `MISSION_SESSION_ID=hooktest` を付けて
   `mission-state.py init "<mission 文字列>" --complexity Simple --issue-ref probe-1`
   を実行する（**mission 文字列は必須の位置引数**）。
2. 高負荷側のみ、同ホストで `make test`（`-n auto`）を並行して回す。
3. `MISSION_SESSION_ID=hooktest` を環境に置き、`bash scripts/mission-stop-guard.sh`
   へ `{"cwd":"/tmp/hooktest"}` を stdin で渡す試行を 8 回、間に 1 秒を置いて実行。
   各試行で `time.monotonic()` の差、`RUSAGE_CHILDREN` の `ru_utime + ru_stime` の
   前後差、`os.getloadavg()[0]` を記録する。

回数は `MISSION_STATE_PY` に shim を置き、引数をログへ追記してから実体へ委譲して
数える。

### 生データ

**壁時間しか保存していない。** CPU 時間と試行別 load は集計値だけを控えており、
**表中の CPU 時間は再計算できない**。高負荷側の壁時間も小数第 2 位までしか
控えていないため、中央値 1.488s / 最大 1.587s も上の値からは再現できない。

低負荷（load 1.93）、8 試行（秒）:

    0.896 0.898 0.899 0.893 0.883 0.892 0.888 0.876

高負荷（load 9.8-11.6）、8 試行（秒）:

    1.45 1.53 1.59 1.56 1.33 1.41 1.52 1.46

**再測定する場合は試行ごとに 3 つとも保存する。**

### この測定が示していないこと

- **load 11.6 を超える領域。** 2026-08-30 の観測（load 85）は再現していない
- **伸び方の関数形。** 2 つの load 帯しかなく、線形か否かは決まらない
- **3 回の呼び出しの内訳。** 分けて測っていない
- **旧測定（load 8 → 64 で 6 倍）との関係。** 手順と生データが無く条件を揃えられない。
  **本測定を根拠に旧測定を否定しない**

`load average` は 1 分平均であり、サブ秒処理の瞬間的な CPU 競合を直接は表さない。
**負荷の唯一の指標としては不十分である。**

## #742 で決めること

本文書は決めない。次はすべて #742 が扱う。

- 検査対象（回数か、所要か、両方か）
- 検査の置き場所（job 名）と頻度
- 単価（CPU 時間）の記録先
- 上限が掛からない経路（`timeout` も `perl` も無い / `MISSION_STATE_TIMEOUT=0`）を
  許容するか
- ホストの 10 秒と `MISSION_STATE_TIMEOUT` の 30 秒が食い違っていることの扱い

## 初版が誤っていた点（記録）

| 誤り | 実際 |
|---|---|
| 「hook 1 回あたりの CLI 呼び出しは 1 回」 | active では 3 回。**active 経路を通さずに測っていた** |
| 「回数を検査すれば 30 秒契約を検証できる」 | 上限は `stop-verdict` 単位で、補助コマンドは囲われていない |
| 「30 秒で囲われている」 | **環境依存。`timeout` も `perl` も無ければ掛からない** |
| 「Stop は永久に止まる」 | **ホスト側が 10 秒で切り、連続 block も 8 回で打ち切られる** |

**いずれも、細部を直しながらその細部が乗っている前提を確認していなかった。**
