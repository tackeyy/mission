あなたは異系統レビュアーです。この worktree の PR #745（Issue #742）をレビューしてください。

## 対象 head

最初に `git rev-parse HEAD` を実行し、**`a17aba6e`** で始まることを確認してください。
一致しなければ中止して報告してください。

対象範囲は `git diff origin/main...HEAD`（reviewed 321 行）です。

## この PR がやること

Stop guard の上限が環境依存だった問題（#742 の D2 / D3）を直します。

**変更前**: `scripts/mission-stop-guard.sh` は `timeout` があればそれを、無ければ `perl` の
`alarm` を使い、**どちらも無ければ上限なしで実行**していました。その環境で hook が返らなければ
Stop は永久に止まります。

**変更後**:
- `stop-verdict` 自身が `signal.alarm` で上限を掛ける（`mission_application/guard_timeout.py`）
- hook からは上限に関する記述を**全部落とした**（`timeout` / `perl` / 既定値 / 値の検証）
- 既定を 30 秒 → **8 秒**（ホスト側の hook timeout 10 秒の内側に置く）

## 設計上の判断（レビューしてほしい点）

**hook から外側の上限を撤去したこと**が最大の判断です。理由は 2 つあります。

1. #615 が hook を judgment-free と定めており、`analyze_guard_shell` は数値比較を policy 判断
   として拒否する。値の検証を hook に置くと抵触する
2. #742 D3 が「ホスト側の hook timeout を契約とし、guard の上限はその内側」と決めている。
   ホスト側が外側の上限そのものなので、hook が二重に掛ける必要がない

**この判断が妥当か、批判的に見てください。** とくに「インタプリタが自分の alarm を設置する前に
固まった場合」に何が守るのかを検証してください。

## 検証してほしいこと

1. **D2 が本当に達成されているか。** `timeout` も `perl` も無い環境で上限が掛かるか
2. **D3 の値の解決に穴がないか。** `0` / 負値 / 非 ASCII 数字 / 前置ゼロ / 空文字
3. **追加された検査が「落ちるべきときに落ちる」か。** 変異を注入して確かめてください。
   とくに `@bounded_by_guard_timeout` を外したときに検出されるか
4. **`signal.alarm` の使い方に問題がないか。** ハンドラの復元、ネスト、非 POSIX 環境
5. デコレータ方式が `cmd_stop_verdict` の既存の失敗経路（block + exit 2）と整合しているか

## 判定基準

- 実装・記述の欠陥（誤り・矛盾・空回りする検査・裏付けのない主張）→ **Medium 以上**
- 「さらに厳密にできる」「別の設計もありうる」→ **Low**
- **Low のみなら accepted**

## 出力形式

各指摘に severity と一次情報（ファイル名と行番号）を付けてください。
**最終行に必ず次のいずれかを単独行で**出力してください。

VERDICT: accepted
VERDICT: changes-requested
