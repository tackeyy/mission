# Task: portfolio-simple-lookup (arm: claude_code_goal_command)

## Goal

Read the fixture `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md` and report the `TIMEOUT_MS` value for the `search-api` service, and name which service has the largest timeout.

## Result

- `search-api` の `TIMEOUT_MS` は **8500** です。
- 最大タイムアウトを持つサービスは **billing**（12000ms）です。

## Evidence

フィクスチャファイル `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md` の内容全文:

```
# Service Timeout Registry

| Service | TIMEOUT_MS |
|---|---|
| ingest | 4000 |
| search-api | 8500 |
| billing | 12000 |
| notify | 3000 |
```

- `search-api` の行: `| search-api | 8500 |` → TIMEOUT_MS = 8500
- 全4サービスの値を比較: ingest=4000, search-api=8500, billing=12000, notify=3000
- 最大値は `billing` の 12000 → `| billing | 12000 |`

## Assumptions

- フィクスチャの表は正しく整形されており、記載値がそのまま正の値であると仮定した（単位変換やスケーリングは行っていない）。
- 指示に従い、フィクスチャファイル以外の `benchmarks/mission-vs-goal/` 配下（タスク定義・採点設定・答え合わせ用ファイル等）は一切参照していない。
- 未計測の項目はない（4サービス全ての値をフィクスチャから直接確認済み）。

## Stop Condition

本成果物ファイル（`benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-simple-lookup-claude_code_goal_command.md`）が作成され、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含んだ時点で完了とする。ネットワークアクセス・パッケージインストール・commit/push は行っていない。
