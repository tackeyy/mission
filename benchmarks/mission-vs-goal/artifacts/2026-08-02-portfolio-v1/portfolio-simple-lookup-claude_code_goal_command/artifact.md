# Goal

Read exactly `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md` and report the `TIMEOUT_MS` value for the `search-api` service, and name which service has the largest timeout.

## Result

- `search-api` の `TIMEOUT_MS` は `8500`。
- 最大タイムアウトのサービスは `billing`（`12000`）。

## Evidence

Fixture `benchmarks/mission-vs-goal/fixtures/portfolio/service-timeouts.md` の全内容（as read):

```
# Service Timeout Registry

| Service | TIMEOUT_MS |
|---|---|
| ingest | 4000 |
| search-api | 8500 |
| billing | 12000 |
| notify | 3000 |
```

- `| search-api | 8500 |` → search-api の TIMEOUT_MS = `8500`
- 全4行の値を比較: ingest=4000, search-api=8500, billing=12000, notify=3000 → 最大値は `billing` の `12000`

## Assumptions

- fixture に記載の4サービス（ingest, search-api, billing, notify）以外のサービスは存在しないと仮定。
- 単位は fixture のカラム名どおり `TIMEOUT_MS`（ミリ秒）とし、換算は行っていない。

## Stop Condition

- 本アーティファクトが `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v1/portfolio-simple-lookup-claude_code_goal_command.md` に作成され、Goal / Result / Evidence / Assumptions / Stop Condition の全見出しを含み、search-api のタイムアウト値と最大タイムアウトサービスを明記した時点で完了。
