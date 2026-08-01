# Goal

`benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md` を読み、Usage note が参照している設定名のうち、テーブルの設定名と一致しない誤記を特定する。誤記を正確に引用し、テーブル上の正しい設定名を示す。

# Result

Usage note 内の `retry_polcy` は誤記である。テーブル上の正しい設定名は `retry_policy` である。

## 確定した誤記（Confirmed）
- 誤記: `retry_polcy`（Usage note 内の参照）
- 正しい設定名: `retry_policy`（テーブルの Setting 列に記載）

## 却下した候補（Rejected candidates）
なし。テーブルに記載された他の設定名（`max_attempts`, `base_delay_ms`）は Usage note 内で参照されておらず、誤記の候補として検討対象にならなかった。

# Evidence

fixture ファイル `benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md` からの引用:

テーブル（設定名の定義、5-9行目）:
```
| Setting | Value |
|---|---|
| retry_policy | exponential-backoff |
| max_attempts | 4 |
| base_delay_ms | 200 |
```

Usage note（誤記箇所、11-12行目）:
```
Usage note: services must reference the `retry_polcy` setting name exactly as
defined in the table above when importing this configuration block.
```

テーブルの設定名は `retry_policy`（`e` を含む正しいスペル）であるのに対し、Usage note の引用は `retry_polcy`（`e` が欠落したスペル）となっており、一致しない。

# Assumptions

- 指定されたfixtureファイル1件（`benchmarks/mission-vs-goal/fixtures/portfolio/retry-config.md`）のみを読み取り対象とした。他のfixtureやベンチマークメタデータは参照していない（指示により参照禁止のため）。
- テーブルに記載された3つの設定名（`retry_policy`, `max_attempts`, `base_delay_ms`）はそれぞれ独立した正しい設定名であるとみなした。テーブル自体に誤記がある可能性は検討していない（未検証）。
- 誤記の判定基準は「テーブルの文字列と完全一致しない」こととした。意味的な等価性の評価は行っていない。

# Stop Condition

以下をすべて満たした時点で完了とする:
- 本artifactが `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-simple-typo-claude_code_goal_command.md` に存在する
- Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含む
- 誤記（`retry_polcy`）と正しい設定名（`retry_policy`）をともに引用している

上記はすべて満たされており、本タスクは完了。
