# Portfolio Simple Lookup — claude_code_goal_command

## Goal

Read exactly the fixture `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md` and report:
1. The `TIMEOUT_MS` value for the `search-api` service.
2. Which service has the largest timeout.

## Result

- `search-api` の `TIMEOUT_MS` は **8500** です。
- 最大タイムアウトを持つサービスは **billing**（12000）です。

## Evidence

Fixture の全内容（`benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md`）を引用する:

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
- 全4サービスの値を比較: ingest=4000, search-api=8500, billing=12000, notify=3000 → 最大値は billing の 12000

## Assumptions

- fixture に記載された4サービス（ingest, search-api, billing, notify）以外のサービスは存在しないという前提で「最大」を判定した。
- fixture 以外のファイル（タスク定義・採点設定・答え合わせ資料等）は本タスクの指示により開いていないため、参照していない。

## Stop Condition

- 本 artifact が `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-simple-lookup-claude_code_goal_command.md` に作成され、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含んでいることを確認した。
- search-api のタイムアウト値（8500）と最大タイムアウトサービス（billing）の両方を Result に明記した。
- この2条件が満たされた時点でタスク完了とする。
