# mission vs goal-only pilot report

Status: 2026-06-27 に package validation を計測済み。20 件の paired benchmark run は
まだ完了していないため、この report では比較性能の主張はしません。

## Executive Summary

benchmark package は実行準備ができています。固定 10 task、2 つの benchmark arm、
result schema、英日 protocol、英日 report template、regression test を定義済みです。

protocol に沿った paired task run はまだ実行していないため、`mission` と `goal_only` の
outcome metric は未測定です。現時点の evidence で言えるのは package readiness であり、
workflow performance ではありません。

## Measured Status

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-27 | current local validation run。 |
| Starting commit | `c5ab7cd` | `git rev-parse --short HEAD`。 |
| Fixed pilot tasks defined | 10 / 10 | `benchmarks/mission-vs-goal/tasks.json`。 |
| Benchmark arms defined | 2 / 2 | `goal_only`, `mission`。 |
| Expected paired runs | 20 | 10 tasks x 2 arms。 |
| Paired benchmark runs completed | 0 / 20 | protocol-compliant result JSONL はまだ存在しない。 |
| Goal-only runs completed | 0 / 10 | 未測定。 |
| Mission runs completed | 0 / 10 | 固定 10 task set では未測定。 |
| Comparative performance claim readiness | 0 / 1 | paired run 完了まで block。 |

## Package Validation Results

| Check | Result |
|---|---:|
| Benchmark + doc consistency tests | 29 passed / 29 |
| Full mission test suite | 394 passed / 394 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |
| Mission package creation score | 4.54 / 5.00 |
| Mission package creation minimum item score | 4.50 / 5.00 |

使用した commands:

```bash
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md skills/mission/tests/test_benchmark_package.py
```

## Results

| Metric | goal_only | mission | Notes |
|---|---:|---:|---|
| Completion rate | 未測定 | 未測定 | 各 arm 10 件の paired task run が必要。 |
| Validator pass rate | 未測定 | 未測定 | protocol-compliant result record が必要。 |
| Average human quality score | 未測定 | 未測定 | blind または label-hidden scoring が必要。 |
| Average intervention count | 未測定 | 未測定 | run log が必要。 |
| Resume success rate | 未測定 | 未測定 | interruption task のみ該当。 |
| Average evidence completeness | 未測定 | 未測定 | artifact review が必要。 |
| Average elapsed minutes | 未測定 | 未測定 | run timestamp が必要。 |

## Current Marketing Summary

現時点で言ってよい:

> `mission` vs goal-only benchmark protocol は実装・検証済みです。
> 10 task の paired pilot を実行できる状態ですが、比較性能の結果はまだ未測定です。

まだ言ってはいけない:

> `mission` は goal-only execution より優れている。

## Next Measurement Step

20 件の paired benchmark execution を実行し、run ごとに JSONL record を保存します:

```text
results/YYYY-MM-DD-run-001.jsonl
```

aggregate completion、validator、quality、intervention、resume、evidence、elapsed-time の
比較を出せるのは、その後です。
