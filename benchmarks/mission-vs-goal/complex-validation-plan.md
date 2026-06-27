# Complex task validation plan

Status: planned, not measured.

The first 10-task pilot showed no difference in completion or validator pass
rate because both arms reached 100%. It did show stronger automated evidence
and completion-quality scores for `mission`, with longer runtime. That result
does not answer what happens on tasks with more ambiguity, more files, context
loss, security sensitivity, or stop/go decisions.

This plan defines the next validation step without making new performance
claims.

## Research Question

When the task is complex enough that "done" requires state tracking, hypothesis
discipline, safety gates, or cross-artifact consistency, does `mission` improve
completion quality compared with a goal-only baseline?

## Hypotheses

These are hypotheses to test, not results:

| Hypothesis | Why it may happen | How to falsify it |
|---|---|---|
| `mission` improves evidence completeness on complex tasks. | The workflow forces plan, review, score, and stop-decision evidence. | Goal-only artifacts provide equally complete evidence across the complex cohort. |
| `mission` reduces premature "done" on stop/go and safety-gated tasks. | Threshold-gated completion should make unresolved risk visible. | Both arms make equally safe stop/go decisions with comparable evidence. |
| `mission` is slower. | Extra state, review, and scoring add work. | Runtime difference is negligible or goal-only needs retries that erase the time advantage. |
| Goal-only remains competitive on tightly scoped complex tasks. | Some complex-looking tasks still have deterministic validators. | `mission` materially outperforms on all complex tasks, including deterministic ones. |

## Task Set

Use `tasks.complex.json` for the next cohort:

- 10 tasks.
- Same two arms: `goal_only` and `mission`.
- `mission` tasks use `Complex` or `Critical` state initialization instead of the previous `Simple --max-iter 1` setup.
- The cohort is still local and controlled. It is not a general model benchmark.

## Execution Protocol

Run from a clean starting commit and a unique run id so prior results are not
overwritten:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-local \
  --starting-commit <commit> \
  --timeout 1800
```

Recommended first pass:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-smoke \
  --starting-commit <commit> \
  --limit 2 \
  --timeout 1800
```

The smoke run should complete one task in both arms before the full 20-run
complex cohort is launched.

## Measurement Requirements

| Metric | Requirement |
|---|---|
| Completion rate | Count only completed task artifacts. |
| Validator pass rate | Count only runs that satisfy the task-specific validator text and required headings. |
| Quality score | Label as automated heuristic unless a separate blind human review is actually performed. |
| Evidence completeness | Score from the artifact and saved raw logs, not from memory or assumptions. |
| Resume success | Only score tasks where resume or context recovery is part of the prompt. |
| Runtime | Use recorded wall-clock minutes from the runner. |
| Intervention count | Count human clarification or correction after the run begins. |

## Interpretation Rules

Safe after full measurement:

- "In this controlled complex-task cohort, `mission` had X measured outcome
  under Y scoring method."
- "`mission` was slower/faster by X minutes on average in this local run."
- "The effect was concentrated in task categories A and B."

Unsafe without more evidence:

- "`mission` is generally better than goal-only."
- "`mission` is smarter."
- "Complex tasks always need `mission`."
- Any claim that hides the sample size, local setup, task mix, or scoring method.

## Exit Criteria

The complex validation is ready for marketing use only when all are true:

1. 20 / 20 paired executions are attempted from the same starting commit.
2. Raw JSONL, summary JSON, and artifacts are saved.
3. Reports state whether quality scoring was automated heuristic or blind human.
4. English and Japanese reports are updated.
5. Tests validate task-set shape, runner configuration, and report honesty.
6. Unsupported claims are explicitly listed as unsafe.
