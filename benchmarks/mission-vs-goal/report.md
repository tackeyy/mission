# mission vs goal-only pilot report

Status: measured on 2026-06-27 as a controlled local Codex CLI pilot.
An additional Claude Code official `/goal` smoke was attempted on 2026-06-28
JST and is reported separately below.

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

## Claude Code Official `/goal` Smoke

Status: attempted on 2026-06-28 JST as a controlled Claude Code CLI print-mode
smoke. The comparison target is the official Claude Code built-in `/goal`
command, not the earlier local `goal_only` baseline. Claude Code command and
skill documentation are published at <https://code.claude.com/docs/en/commands>
and <https://code.claude.com/docs/en/skills>.

This smoke produced evidence, but it is **not** a completed performance
comparison: the `/mission` arm stopped before writing its task artifact because
Claude Code returned workspace API usage limit error 400. The raw error states
that access resumes on 2026-07-01 at 00:00 UTC.

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-28 JST | Raw records completed at 2026-06-27T16:34:57Z. |
| Run id | `2026-06-28-claude-goal-vs-mission-smoke-v2` | `results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl`. |
| Starting commit | `38cc7907e5e35fcd9fa23022a1fcf03f756df99b` | Runner argument. |
| Task file | `tasks.complex.json` | One smoke task: `complex-cross-file-feature`. |
| Arms | 2 | `claude_code_goal_command`, `mission`. |
| Records completed | 2 / 2 | Summary JSON has two records. |
| `/goal` artifact completion | 1 / 1 | Artifact exists and validator heuristic passed. |
| `/mission` artifact completion | 0 / 1 | No artifact; API usage limit stopped the run. |
| `/goal` cost | USD 0.93959825 | Raw Claude result JSON. |
| `/mission` cost before stop | USD 1.05234325 | Raw Claude result JSON. |
| Quality score method | automated heuristic | Not blind human review. |
| Marketing comparison readiness | blocked | The `/mission` arm did not receive a full comparable attempt. |

Smoke result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completion rate | 1 / 1 | 0 / 1 | `/mission` failed due to workspace API limit, not a validated task-quality failure. |
| Validator pass rate | 1 / 1 | 0 / 1 | The `/mission` validator could not run because no artifact was written. |
| Average quality score | 4.00 / 5 | 1.00 / 5 | Automated placeholder score from artifact presence; not a capability conclusion. |
| Average evidence completeness | 4.00 / 5 | 1.00 / 5 | Same limitation as above. |
| Average elapsed minutes | 1.97 | 2.06 | `/mission` elapsed time ended at API-limit stop. |

Safe interpretation:

> The official Claude Code `/goal` smoke harness ran one complex task and wrote
> an auditable artifact. The comparable `/mission` arm was blocked by Claude
> Code workspace API limits before artifact completion, so this smoke does not
> support a marketing claim that either arm is better.

In short, this smoke does not support a marketing claim that either arm is better.

Unsafe interpretation:

> `mission` lost to official `/goal`.

That would be unsupported because the failed `/mission` record is an
infrastructure/API-limit stop, not a completed task-quality measurement.

Follow-up preparation completed after this smoke:

- `run_claude_goal_vs_mission.py` now records `run_status`,
  `blocked_reason`, `failure_kind`, and `comparable_attempt` for future runs.
- `official-goal-rerun-runbook.md` defines the 2026-07-01 09:00 JST smoke gate
  and full paired pilot procedure.
- Future reports must separate `completed`, `failed`, and `blocked` records
  before making any capability claim.

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
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v2 --starting-commit 38cc7907e5e35fcd9fa23022a1fcf03f756df99b --limit-tasks 1 --timeout 300 --max-budget-usd 1.5 --mission-max-iter 1
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/tasks.complex.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md benchmarks/mission-vs-goal/complex-validation-plan.md benchmarks/mission-vs-goal/complex-validation-plan.ja.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.ja.md benchmarks/mission-vs-goal/run_paired_pilot.py benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py skills/mission/tests/test_benchmark_package.py
```

## Marketing Summary

Safe to say:

> In a controlled local 10-task Codex CLI pilot, both goal-only and `mission`
> completed and passed all validators. `mission` produced stronger automated
> evidence/completion-quality scores, while taking longer per run.

Safe to say about the official `/goal` smoke:

> A first Claude Code official `/goal` smoke was attempted on one complex task.
> `/goal` completed the artifact, while the comparable `/mission` arm was
> blocked by Claude Code workspace API limits. No marketing comparison should be
> made from this smoke until the `/mission` arm can complete.

Do not say:

> `mission` is smarter than `/goal`.

Do not say:

> `mission` improves completion rate in this pilot.

Do not say:

> `mission` is better or worse than Claude Code official `/goal` based on the
> 2026-06-28 smoke.

## Raw Result Index

```text
results/2026-06-27-codex-cli-local.jsonl
results/2026-06-27-codex-cli-local-summary.json
artifacts/2026-06-27-codex-cli-local/
results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl
results/2026-06-28-claude-goal-vs-mission-smoke-v2-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-smoke-v2/
```
