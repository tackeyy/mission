# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-simple-typo-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Read exactly `benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md`. The usage note references a setting name that does not match the table. Identify the misspelled reference, quote it exactly, and state the correct setting name from the table.

# Result

- 誤記（Confirmed）: Usage note 内の `retry_polcy` は誤記であり、テーブルの設定名と一致しない。
- 正しい設定名（Confirmed）: `retry_policy`（テーブルの1行目に定義されている）。

# Evidence

フィクスチャファイル `benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md` を読み取った結果、以下の記述が確認できた。

テーブル（5-9行目）:
```
| Setting | Value |
|---|---|
| retry_policy | exponential-backoff |
| max_attempts | 4 |
| base_delay_ms | 200 |
```

Usage note（11-12行目、原文引用）:
```
Usage note: services must reference the `retry_polcy` setting name exactly as
defined in the table above when importing this configuration block.
```

上記引用の通り、Usage note は `retry_polcy` という文字列を参照しているが、テーブルに存在する設定名は `retry_policy` であり、`retry_polcy` はテーブル中のどの行とも一致しない（`max_attempts` や `base_delay_ms` とも異なる）。両者を文字列比較すると `retry_polcy` は `retry_policy` から "i" が1文字欠落した誤記であることが確認できる。

# Assumptions

- フィクスチャファイルの内容は読み取った時点（本セッション実行時）のものであり、その後に変更されていないことを前提とする。
- タスク指示に従い、`benchmarks/mission-vs-goal/` 配下でアクセスしたのは指定フィクスチャファイルと本アーティファクト出力ファイルのみであり、他のベンチマークメタデータ（タスク定義・採点設定・正答キー等）は未確認・未参照である。
- テーブルとの照合以外の妥当性検証（例: 実際のコードベースでの `retry_policy` の使用箇所確認）は本タスクの範囲外であり、未実施・未測定である。

# Stop Condition

本アーティファクトの作成をもって、要求された Goal、Result、Evidence、Assumptions、Stop Condition の5見出しを含む状態が満たされた。誤記の特定（`retry_polcy`）と正しい設定名の提示（`retry_policy`）が完了し、これ以上のアクションは不要であるため、作業を終了する。
