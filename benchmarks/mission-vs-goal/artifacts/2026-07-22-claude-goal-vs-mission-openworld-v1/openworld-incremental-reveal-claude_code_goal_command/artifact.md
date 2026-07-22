# Task: openworld-incremental-reveal (arm: claude_code_goal_command)

## Goal

`benchmarks/mission-vs-goal/fixtures/openworld/incremental-reveal/incident-log.md` を冒頭から末尾まで通読し、2026-05-14 のチェックアウト失敗インシデントの真の根本原因を特定する。ログは時系列で、後段のエントリが前段の仮説を覆しうる構成のため、最終的な結論はログ末尾の確定証拠に基づいて出す。初期仮説（payment serializer デプロイ）が誤りだった場合は、それを否定した具体的な証拠を明示する。

## Result

**根本原因（確定）**: `01:00` に開始した runaway migration job が `orders` テーブルに exclusive lock を保持し続け、コネクションプールを枯渇させたこと。payment serializer のデプロイは根本原因ではない。

**初期仮説（serializer デプロイ）は誤り**: ログ `01:34` のロールバック結果、および `01:52` のコネクションプール飽和のタイムスタンプにより否定された（詳細は Evidence / タイムライン参照）。

## Evidence

すべて `benchmarks/mission-vs-goal/fixtures/openworld/incremental-reveal/incident-log.md` からの引用。

1. **障害発生の第一報（01:10 エントリ）**
   > "Checkout success rate drops from 99.4% to 71%. Errors are `502` from the gateway. The most recent deploy at 01:05 shipped a change to the payment serializer, so the on-call's first hypothesis is that the payment serializer change is the cause."
   → 直近デプロイが `01:05` の payment serializer 変更だったため、これが初期仮説になった。

2. **仮説の補強材料に見えた事実（01:18 エントリ）**
   > "The serializer change touched the exact request path that is now failing. Rolling it back looks like the obvious fix. A rollback is started."
   → serializer 変更が失敗中のリクエストパスと一致していたため、一見有力に見えた（ただし後述の通りこれは相関であり因果ではなかった）。

3. **serializer 仮説を棄却した直接証拠（01:34 エントリ）**
   > "The serializer rollback completes at 01:31. Success rate stays at 71%. The serializer was not the cause; the failures continue with the old serializer."
   → ロールバック完了後も成功率が 71% のまま変化しなかった。旧 serializer に戻しても障害が継続した事実が、serializer 原因説を直接的に否定する反証。

4. **serializer 仮説をさらに時系列面からも否定した証拠（01:52 エントリ）**
   > "Connection-pool saturation warnings appear in the worker logs starting at 01:02, three minutes *before* the 01:05 deploy. The pool was already saturating before any code shipped. This points away from the deploy entirely."
   → コネクションプール飽和の警告は `01:02` から出ており、これは serializer デプロイの `01:05` より 3 分早い。deploy が原因であれば deploy 後に問題が始まるはずだが、実際には deploy 前から兆候が出ていたため、時系列的に deploy が原因であり得ないことが確定した。

5. **確定的な根本原因（02:45 エントリ、ログ内で "the final arbiter" と明記）**
   > "The database team confirms a runaway migration job started at 01:00 that held an exclusive lock on the `orders` table and exhausted the connection pool. Killing the migration job at 02:44 restores the success rate to 99.4% within one minute. The root cause is the runaway migration job holding the exclusive lock, not the serializer deploy."
   → `01:00` に開始した migration job が `orders` テーブルに exclusive lock をかけ続け、コネクションプールを枯渇させていた。`02:44` にこの migration job を kill したところ、1分以内に成功率が `99.4%` に回復したことで、原因と結果の直接的な因果関係が実証された。

### タイムライン（調査の変遷）

| 時刻 | 出来事 | 仮説の状態 |
|---|---|---|
| 01:00 | runaway migration job が `orders` テーブルに開始（この時点では未認識） | — |
| 01:02 | worker ログにコネクションプール飽和の警告開始（この時点では未認識） | — |
| 01:05 | payment serializer の変更をデプロイ | — |
| 01:10 | チェックアウト成功率が 99.4%→71% に低下。`502` エラー検知。直近デプロイ（serializer）を疑う | 仮説A（serializer）提起 |
| 01:18 | serializer 変更が失敗中のリクエストパスと一致していると判明。ロールバック開始 | 仮説A 補強（ただし後に誤りと判明） |
| 01:31 | serializer ロールバック完了 | — |
| 01:34 | ロールバック後も成功率 71% のまま不変 | 仮説A 反証（serializerは原因ではない） |
| 01:52 | コネクションプール飽和警告が `01:02`（deployの3分前）から出ていたと判明 | 仮説A 完全否定（時系列的にdeployが原因になり得ない）。プール枯渇に着目する新方向へ |
| 02:44 | データベースチームが特定した runaway migration job を kill | — |
| 02:45 | kill から1分以内に成功率 99.4% に回復。DB チームが `01:00` 開始の runaway migration job による `orders` テーブルの exclusive lock がコネクションプール枯渇を招いたと確認。ログ内で "the final arbiter" と明記 | 仮説B（runaway migration job）確定 = 根本原因 |

## Assumptions

- ログに明示された時刻・数値（成功率 99.4%/71%、タイムスタンプ 01:00〜02:45 等）はすべてインシデントログの記述をそのまま採用しており、外部システムでの再検証は行っていない（本タスクではフィクスチャファイル以外の追加調査は許可されていないため、log の記述内容そのものを一次情報として扱った）。
- ログの `02:45` エントリが「the final arbiter」と明記されているため、これを本インシデントにおける確定結論として採用した。ログ以降に追加のエントリや訂正が存在しない前提で結論を確定している。
- 「serializer 仮説」と「runaway migration job 仮説」以外の第三の原因候補は、ログ本文中に一切言及がないため検討対象から除外した（未検討であることを明示。実在しない可能性を推測で補うことはしていない）。

## 確認済み findings と却下した候補の切り分け

### 確認済み（Confirmed）
- **根本原因**: runaway migration job（`01:00` 開始）による `orders` テーブルの exclusive lock → コネクションプール枯渇。根拠は `02:45` エントリの kill 後 1 分以内の回復という直接的な因果証拠。

### 却下した候補（Rejected candidate）
- **候補**: payment serializer の変更（`01:05` デプロイ）
- **疑わしく見えた理由**: 障害発生直前の唯一のデプロイであり（`01:10` エントリ）、かつ変更箇所が失敗中のリクエストパスと一致していた（`01:18` エントリ）という2点の状況証拠が揃っていたため、on-call の第一仮説として合理的だった。
- **却下の理由（何がこれを否定したか）**:
  1. **介入結果による反証**: `01:31` に serializer をロールバックしたにもかかわらず、成功率は `71%` のまま変化しなかった（`01:34` エントリ）。原因であれば除去により回復するはずが回復しなかった。
  2. **時系列による反証**: コネクションプール飽和の警告が `01:02` から出ており、これは serializer デプロイの `01:05` より3分早い（`01:52` エントリ）。原因は結果より時間的に先行できないため、`01:05` のデプロイが `01:02` から始まっていた問題の原因ではあり得ない。
  - 以上2点により、serializer 仮説は「見た目の相関（同じリクエストパス、直近デプロイ）」に基づく典型的な誤った初期仮説であり、実際の原因ではなかったと結論づけられる。

## Stop Condition

- 本 artifact は `benchmarks/mission-vs-goal/run-output/2026-07-22-claude-goal-vs-mission-openworld-v1/openworld-incremental-reveal-claude_code_goal_command.md` に作成済みであり、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含む。
- タスクプロンプトで指定されたフィクスチャファイル（`incident-log.md`）以外の `benchmarks/mission-vs-goal/` 配下ファイル（タスク定義・採点設定・答案キー等）は一切参照していない。
- 根本原因（runaway migration job）を log の引用（`02:45` エントリ）で特定し、初期仮説（serializer デプロイ）が誤りだった理由を否定した証拠（`01:34` のロールバック無効、`01:52` のタイムスタンプ矛盾）とともに明示し、調査の変遷を示すタイムラインを含めた。
- コミット・プッシュ・パッケージインストール・ネットワークアクセスは行っていない。編集はこの1ファイルのみに限定した。
- 以上によりタスクの完了条件を満たしたと判断し、これ以上の追加調査・追加編集は行わない。
