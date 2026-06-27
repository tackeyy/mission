# mission vs goal-only pilot benchmark

This directory contains a 10-task pilot benchmark for comparing `mission`
against a goal-only execution baseline in a marketing-safe way.

The benchmark is intentionally small. Its purpose is to learn where `mission`
is meaningfully stronger and where a lightweight goal condition is enough
before publishing broader claims.

The first measured cohort used `tasks.json`. More complex validation is planned
separately in `tasks.complex.json` and `complex-validation-plan.md`; those
complex-task outcomes are not measured until a separate run id has raw JSONL
and artifacts.

## Research Question

When the same agent model receives the same task objective, does adding
`mission`'s stateful plan, review, scoring, and iteration loop improve outcomes
for multi-step work compared with a goal-only baseline?

## Arms

| Arm | Setup | What it tests |
|---|---|---|
| `goal_only` | Convert the task into a measurable goal, then run the agent normally until it decides the goal is satisfied. | A lightweight completion-condition workflow. |
| `mission` | Run the same objective through `/mission` with state, review, scoring, and threshold-gated completion. | A stateful loop-engineering workflow. |

Use the same model, repository state, branch starting point, tool permissions,
time budget, and task prompt for both arms.

## Metrics

| Metric | Definition |
|---|---|
| `completion` | The run produced the required artifact or code change and did not stop in an unresolved state. |
| `validator_pass` | The task-specific validator passed. Examples: tests, lint, schema check, exact file assertion, or reviewer checklist. |
| `human_quality_score` | Blind reviewer score from 1 to 5 using the rubric below. |
| `intervention_count` | Number of human clarifications, corrections, or restarts needed after the run began. |
| `resume_success` | For interruption tasks, whether the run resumed without losing task state. |
| `evidence_completeness` | Whether the run left enough evidence to justify "done": commands, artifacts, reviewer notes, and final state. |
| `elapsed_minutes` | Wall-clock runtime from first agent action to final answer. |
| `token_estimate` | Token usage when available. Otherwise leave null. |

## Protocol

1. Start from a clean checkout or isolated worktree.
2. For each task in `tasks.json`, run the `goal_only` arm and the `mission` arm
   from the same starting commit.
3. Counter-balance run order where practical: alternate which arm runs first by
   task id so one arm does not always benefit from operator learning.
4. Do not let either arm see the other arm's transcript.
5. Store one JSONL record per run using `result.schema.json`.
6. Run task validators before assigning human quality scores.
7. Have the human reviewer score blind to arm label when practical.
8. Summarize only aggregate results unless individual examples are anonymized
   and reproducible.

For the next complex-task cohort, run the same protocol with an explicit task
file and a unique run id:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-local \
  --starting-commit <commit> \
  --timeout 1800
```

Start with `--limit 2` for a smoke run before launching all 20 paired complex
executions.

## Human Quality Rubric

| Score | Meaning |
|---:|---|
| 5 | Fully satisfies the prompt, validator, and evidence requirements with no material cleanup needed. |
| 4 | Satisfies the prompt and validator with only minor presentation or completeness gaps. |
| 3 | Partially satisfies the prompt but misses a meaningful requirement or leaves weak evidence. |
| 2 | Produces some useful work, but the validator or core acceptance criteria fail. |
| 1 | Does not produce a usable result or stops in an unresolved state. |

## Marketing Guardrails

Allowed after the pilot has raw evidence:

- "In a 10-task internal pilot, `mission` improved completion quality on
  multi-step tasks that required review, resume, or evidence tracking."
- "`mission` was most useful when the main risk was stopping too early."
- "Goal-only execution remained sufficient for small, single-step tasks."

Not allowed from this pilot:

- Claims about general model intelligence.
- Claims that `mission` is universally better than `/goal`.
- Percent improvements without publishing the denominator, task mix, and scoring method.
- Claims based on fewer than all 10 paired task runs.

## Files

| Path | Purpose |
|---|---|
| `tasks.json` | The measured fixed 10-task baseline pilot set. |
| `tasks.complex.json` | Planned 10-task complex cohort; not measured yet. |
| `result.schema.json` | JSON Schema for one result record. |
| `report.md` | Current measured status and package-validation results. |
| `report-template.md` | Publishable report skeleton with claim guardrails. |
| `complex-validation-plan.md` | Planned protocol for more complex tasks. |
| `README.ja.md` | Japanese version of this benchmark protocol. |
| `report.ja.md` | Japanese current measured status and package-validation results. |
| `report-template.ja.md` | Japanese report skeleton with the same claim guardrails. |
| `complex-validation-plan.ja.md` | Japanese planned protocol for more complex tasks. |
