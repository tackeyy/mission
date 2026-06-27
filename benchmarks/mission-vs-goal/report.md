# mission vs goal-only pilot report

Status: package validation measured on 2026-06-27. The 20 paired benchmark runs
are not complete yet, so this report does not make comparative performance
claims.

## Executive Summary

The benchmark package is ready to run: it defines 10 fixed tasks, 2 benchmark
arms, a result schema, English/Japanese protocols, English/Japanese report
templates, and regression tests.

Actual `mission` vs `goal_only` outcome metrics are not measured yet because no
paired task runs have been executed under the protocol. The current evidence
supports only a package-readiness claim, not a workflow-performance claim.

## Measured Status

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-27 | Current local validation run. |
| Starting commit | `c5ab7cd` | `git rev-parse --short HEAD`. |
| Fixed pilot tasks defined | 10 / 10 | `benchmarks/mission-vs-goal/tasks.json`. |
| Benchmark arms defined | 2 / 2 | `goal_only`, `mission`. |
| Expected paired runs | 20 | 10 tasks x 2 arms. |
| Paired benchmark runs completed | 0 / 20 | No protocol-compliant result JSONL exists yet. |
| Goal-only runs completed | 0 / 10 | Not measured. |
| Mission runs completed | 0 / 10 | Not measured for the fixed 10-task set. |
| Comparative performance claim readiness | 0 / 1 | Blocked until paired runs are complete. |

## Package Validation Results

| Check | Result |
|---|---:|
| Benchmark + doc consistency tests | 29 passed / 29 |
| Full mission test suite | 394 passed / 394 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |
| Mission package creation score | 4.54 / 5.00 |
| Mission package creation minimum item score | 4.50 / 5.00 |

Commands used:

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
| Completion rate | Not measured | Not measured | Requires 10 paired task runs per arm. |
| Validator pass rate | Not measured | Not measured | Requires protocol-compliant result records. |
| Average human quality score | Not measured | Not measured | Requires blind or label-hidden scoring. |
| Average intervention count | Not measured | Not measured | Requires run logs. |
| Resume success rate | Not measured | Not measured | Only applies to interruption tasks. |
| Average evidence completeness | Not measured | Not measured | Requires artifact review. |
| Average elapsed minutes | Not measured | Not measured | Requires run timestamps. |

## Current Marketing Summary

Safe to say now:

> The `mission` vs goal-only benchmark protocol is implemented and validated.
> It is ready for a 10-task paired pilot, but comparative performance results
> have not been measured yet.

Not safe to say yet:

> `mission` outperforms goal-only execution.

## Next Measurement Step

Run the 20 paired benchmark executions and store one JSONL record per run under:

```text
results/YYYY-MM-DD-run-001.jsonl
```

Only after that should this report promote aggregate completion, validator,
quality, intervention, resume, evidence, or elapsed-time comparisons.
