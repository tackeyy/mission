# mission vs goal-only pilot benchmark

This directory contains pilot benchmarks for comparing `mission` against
goal-based execution baselines in a marketing-safe way.

The benchmark is intentionally small. Its purpose is to learn where `mission`
is meaningfully stronger and where a lightweight goal condition is enough
before publishing broader claims.

The first measured cohort used `tasks.json` and the local `goal_only` baseline.
More complex validation is defined in `tasks.complex.json` and
`complex-validation-plan.md`. Quality-critical validation is defined in
`tasks.quality.json`.

Terminology matters:

- `goal_only` is the local lightweight baseline used by `run_paired_pilot.py`.
  It is not the official Claude Code `/goal` command.
- `claude_code_goal_command` is the official Claude Code built-in `/goal`
  command, run through `run_claude_goal_vs_mission.py`.
- `mission` is the `/mission` plugin workflow.

## Research Question

When the same agent model receives the same task objective, does adding
`mission`'s stateful plan, review, scoring, and iteration loop improve outcomes
for multi-step work compared with a goal-only baseline?

## Arms

| Arm | Setup | What it tests |
|---|---|---|
| `goal_only` | Convert the task into a measurable goal, then run the agent normally until it decides the goal is satisfied. | A lightweight completion-condition workflow. |
| `claude_code_goal_command` | Invoke Claude Code's built-in `/goal` command in print mode. | The official Claude Code goal command path, separate from the local baseline. |
| `mission` | Run the same objective through `/mission` with state, review, scoring, and threshold-gated completion. | A stateful loop-engineering workflow. |

Use the same model, repository state, branch starting point, tool permissions,
time budget, and task prompt for both arms.

## Metrics

| Metric | Definition |
|---|---|
| `run_status` | `completed`, `failed`, or `blocked`. Blocked means infrastructure/account state prevented a comparable attempt. |
| `blocked_reason` | Reason for `run_status=blocked`, currently `api_usage_limit`, `max_budget_usd`, or `timeout`; null otherwise. |
| `comparable_attempt` | False when an arm was blocked before a fair task-quality attempt. |
| `mission_profile` | `/mission` prompt profile for official-runner records. `full` is the normal workflow; `light` is a one-pass cost-controlled profile; `quality` emphasizes evidence maps, rejected hypotheses, and stop/proceed decisions. |
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

To compare against Claude Code's official `/goal` command, use the separate
Claude Code runner:

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-smoke \
  --starting-commit <commit> \
  --limit-tasks 1 \
  --timeout 300 \
  --max-budget-usd 1.5 \
  --mission-max-iter 1
```

The 2026-06-28 official `/goal` smoke produced a valid `/goal` artifact on one
complex task, but the comparable `/mission` arm stopped on a Claude Code
workspace API usage limit before writing an artifact. Treat that run as
blocked, not as evidence that either arm is better.

After the API limit was increased, `2026-06-28-claude-goal-vs-mission-smoke-v3`
completed one comparable task on both arms. The follow-on full 10-task attempt
`2026-06-28-claude-goal-vs-mission-complex-v1` then hit workspace API usage
limits on every record. Treat the smoke as a one-task comparable result and the
full attempt as blocked. The official runner records `run_status`,
`blocked_reason`, and `comparable_attempt` so API/account stops are not mistaken
for task-quality failures.

For cost-controlled incremental runs, use `--task-ids` to avoid rerunning
already measured tasks and `--stop-on-blocked` to stop after the first blocked
record:

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-incremental \
  --starting-commit <commit> \
  --task-ids complex-failing-test-triage,complex-review-thread-resolution \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 3.0 \
  --mission-max-iter 2
```

The 2026-06-28 incremental run under a USD 3.00 per-invocation cap completed
both selected tasks on official `/goal`; both `/mission` records hit the
configured max-budget cap. Treat this as an operational cost/runtime result,
not a completed quality comparison for `mission`.

For a lower-cost `/mission` comparison, use `--mission-profile light` and a
single mission iteration:

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-light \
  --starting-commit <commit> \
  --task-ids complex-failing-test-triage \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 5.0 \
  --mission-max-iter 1 \
  --mission-profile light
```

The 2026-06-28 light-profile run completed one previously unmeasured task on
both arms. Official `/goal` completed in 9.56 minutes at USD 3.00670750, while
`/mission` light completed in 5.27 minutes at USD 2.00569500. Treat this as a
promising one-task result and rerun 3-5 fresh tasks before using any broad
cost or runtime claim.

For quality-first comparisons, use the fresh `tasks.quality.json` cohort and
`--mission-profile quality`:

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.quality.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-quality \
  --starting-commit <commit> \
  --task-ids quality-critical-release-governance \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --mission-max-iter 2 \
  --mission-profile quality
```

The 2026-06-28 quality attempt
`2026-06-28-claude-goal-vs-mission-quality-v1` did not complete a paired
comparison. Official `/goal` hit `api_usage_limit` before success and
`/mission` was not run because `--stop-on-blocked` conserved API budget. Treat
this as blocked, not as evidence for either arm's quality.

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
- Claims that `mission` is better or worse than Claude Code official `/goal`
  from the 2026-06-28 attempts. The first smoke was API-limit blocked on
  `/mission`; the rerun smoke completed only one comparable task; the full
  rerun was API-limit blocked on all records; the incremental rerun was
  max-budget blocked on the `/mission` records; the light-profile rerun
  completed only one comparable task; the quality-profile attempt was
  API-limit blocked before the `/mission` arm ran.
- Percent improvements without publishing the denominator, task mix, and scoring method.
- Claims based on fewer than all 10 paired task runs.

## Files

| Path | Purpose |
|---|---|
| `tasks.json` | The measured fixed 10-task baseline pilot set. |
| `tasks.complex.json` | 10-task complex cohort used by official smoke/full attempts; no full comparable run has completed yet. |
| `tasks.quality.json` | Fresh quality-critical cohort for evidence-depth and stop/proceed decision tasks. |
| `result.schema.json` | JSON Schema for one result record. |
| `report.md` | Current measured status and package-validation results. |
| `run_claude_goal_vs_mission.py` | Claude Code official `/goal` vs `/mission` smoke runner. |
| `official-goal-rerun-runbook.md` | English rerun checklist for the official `/goal` comparison. |
| `report-template.md` | Publishable report skeleton with claim guardrails. |
| `complex-validation-plan.md` | Planned protocol for more complex tasks. |
| `README.ja.md` | Japanese version of this benchmark protocol. |
| `report.ja.md` | Japanese current measured status and package-validation results. |
| `official-goal-rerun-runbook.ja.md` | Japanese rerun checklist for the official `/goal` comparison. |
| `report-template.ja.md` | Japanese report skeleton with the same claim guardrails. |
| `complex-validation-plan.ja.md` | Japanese planned protocol for more complex tasks. |
