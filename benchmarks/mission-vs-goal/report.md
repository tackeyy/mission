# mission vs goal-only pilot report

Status: measured on 2026-06-27 as a controlled local Codex CLI pilot.

This is not a general model benchmark and not a blind human evaluation. The
quality and evidence scores below are automated heuristic scores from the local
runner. Raw records and artifacts are checked into this directory so the result
can be audited.

## Executive Summary

We ran all 20 paired benchmark executions from starting commit `0148f16`: 10
`goal_only` runs and 10 `mission` runs.

Both arms completed every task and passed every local validator. In this
controlled pilot, the `mission` arm produced higher automated quality and
evidence scores, while taking more wall-clock time.

The safe takeaway is narrow:

> In this controlled local 10-task pilot, `mission` did not improve completion
> or validator pass rate because both arms reached 100%. It did improve the
> automated evidence/completion-quality score, at the cost of longer runtime.

## Measurement Scope

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-27 | Current local Codex CLI run. |
| Run id | `2026-06-27-codex-cli-local` | `results/2026-06-27-codex-cli-local.jsonl`. |
| Starting commit | `0148f16` | `git ls-remote origin refs/heads/main` before pilot. |
| Fixed pilot tasks defined | 10 / 10 | `benchmarks/mission-vs-goal/tasks.json`. |
| Benchmark arms defined | 2 / 2 | `goal_only`, `mission`. |
| Expected paired runs | 20 | 10 tasks x 2 arms. |
| Paired benchmark runs completed | 20 / 20 | Raw JSONL has 20 records. |
| Goal-only runs completed | 10 / 10 | Raw JSONL records. |
| Mission runs completed | 10 / 10 | Raw JSONL records. |
| Result artifacts captured | 100 files | `artifacts/2026-06-27-codex-cli-local/`. |
| Quality score method | automated heuristic | `quality_score_method=automated_heuristic_not_blind_human`. |
| Comparative performance claim readiness | limited | Only valid for this controlled local task set and scoring method. |

## Results

| Metric | goal_only | mission | Delta |
|---|---:|---:|---:|
| Completion rate | 10 / 10 | 10 / 10 | 0 |
| Validator pass rate | 10 / 10 | 10 / 10 | 0 |
| Average quality score | 4.00 / 5 | 4.50 / 5 | +0.50 |
| Average intervention count | 0.00 | 0.00 | 0 |
| Resume success rate | 1 / 1 | 1 / 1 | 0 |
| Average evidence completeness | 3.80 / 5 | 4.70 / 5 | +0.90 |
| Average elapsed minutes | 1.28 | 2.99 | +1.71 |

## Task-Level Findings

| Task | Stronger arm | Why |
|---|---|---|
| `docs-small-edit` | `mission` on evidence; tie on completion | Both passed; mission recorded more structured evidence and state. |
| `docs-cross-reference` | `mission` on evidence; tie on completion | Both passed; mission included plan/review/score sections. |
| `bug-regression-test` | `mission` on evidence; tie on completion | Both passed; mission had stronger completion trace but longer runtime. |
| `review-comment-batch` | `mission` on evidence; tie on completion | Both passed; mission made review/check evidence explicit. |
| `interrupted-doc-task` | tie on resume; `mission` on evidence | Both resume validators passed; mission recorded state-backed evidence. |
| `ambiguous-research-brief` | `mission` on evidence; tie on completion | Both passed; mission surfaced assumptions and review more explicitly. |
| `release-checklist-audit` | `mission` on evidence; tie on completion | Both passed; mission better matched evidence-tracking intent. |
| `small-refactor` | `mission` on evidence; tie on completion | Both passed; goal-only remained sufficient for validator success. |
| `quality-gate-failure` | `mission` on evidence; tie on completion | Both passed; mission represented the stop decision more explicitly. |
| `marketing-claim-draft` | `mission` on evidence; tie on completion | Both passed; mission made claim guardrails more explicit. |

## Validation Results

| Check | Result |
|---|---:|
| Benchmark + doc consistency tests | 29 passed / 29 |
| Full mission test suite | 394 passed / 394 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |

Commands used:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py --starting-commit 0148f16 --timeout 900
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md benchmarks/mission-vs-goal/run_paired_pilot.py skills/mission/tests/test_benchmark_package.py
```

## Marketing Summary

Safe to say:

> In a controlled local 10-task Codex CLI pilot, both goal-only and `mission`
> completed and passed all validators. `mission` produced stronger automated
> evidence/completion-quality scores, while taking longer per run.

Do not say:

> `mission` is smarter than `/goal`.

Do not say:

> `mission` improves completion rate in this pilot.

## Raw Result Index

```text
results/2026-06-27-codex-cli-local.jsonl
results/2026-06-27-codex-cli-local-summary.json
artifacts/2026-06-27-codex-cli-local/
```
