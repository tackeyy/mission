# Official `/goal` vs `/mission` Rerun Runbook

Status: executed on 2026-06-28 JST after the Claude API limit was increased.

The 2026-06-28 JST smoke run is blocked, not comparative evidence. The raw
Claude result for the `/mission` arm reported a workspace API usage limit and
said access resumes on 2026-07-01 00:00 UTC, which is 2026-07-01 09:00 JST.

An API-limit rerun was then executed:

- `2026-06-28-claude-goal-vs-mission-smoke-v3`: one comparable task completed
  on both arms; both arms passed completion and validator checks.
- `2026-06-28-claude-goal-vs-mission-complex-v1`: 20 records were written, but
  every record was `run_status=blocked` with `blocked_reason=api_usage_limit`.
- `2026-06-28-claude-goal-vs-mission-incremental-v1`: two previously unmeasured
  tasks were selected with `--task-ids` under a USD 3.00 per-invocation cap.
  Official `/goal` completed both; `/mission` hit `blocked_reason=max_budget_usd`
  on both.
- `2026-06-28-claude-goal-vs-mission-light-v1`: one previously unmeasured task
  was selected with `--mission-profile light`, `--mission-max-iter 1`, and a
  USD 5.00 cap. Both arms completed and passed; `/mission` light was faster and
  lower cost on that one task.
- `2026-06-28-claude-goal-vs-mission-quality-v1`: one fresh quality-critical
  task was selected with `--mission-profile quality`, `--mission-max-iter 2`,
  and a USD 6.00 cap. Official `/goal` hit `api_usage_limit` before success, so
  `/mission` was not run.

The completed evidence therefore supports two one-task results: one normal
smoke and one light-profile cost-controlled task. It does not support a full
10-task performance claim. The incremental run adds an operational cost/runtime
caution. The quality-profile run is blocked and not a completed quality
comparison.

## Objective

Run a clean paired comparison between:

- `claude_code_goal_command`: Claude Code official built-in `/goal` command.
- `mission`: `/mission` plugin workflow.

Keep this comparison separate from the older local `goal_only` baseline.

## Preconditions

1. Confirm the Claude Code workspace API limit is clear before launching another
   full run. A one-task smoke can pass and a full run can still hit the limit.
2. Start from an up-to-date `main` branch.
3. Keep the starting commit fixed across both arms.
4. Use a unique `--run-id`; never overwrite prior raw result directories.
5. Treat `run_status=blocked` as infrastructure/account state, not task quality.

## Step 1: Smoke Gate

Run one task first. This checks that both arms can create artifacts before
spending a full benchmark budget.

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-smoke \
  --starting-commit "$STARTING_COMMIT" \
  --limit-tasks 1 \
  --timeout 600 \
  --max-budget-usd 2 \
  --mission-max-iter 1
```

Proceed only if both arm records have:

- `run_status=completed`
- `completion=true`
- no `blocked_reason`

If either arm has `run_status=blocked`, stop and update the report as blocked.
Do not convert blocked records into a performance claim.

Observed rerun result:

- `2026-06-28-claude-goal-vs-mission-smoke-v3` met this smoke gate.

## Step 2: Full Paired Pilot

Run all 10 complex tasks only after the smoke gate passes.

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-complex \
  --starting-commit "$STARTING_COMMIT" \
  --limit-tasks 10 \
  --timeout 1800 \
  --max-budget-usd 2 \
  --mission-max-iter 2
```

Expected records: 20.

Observed rerun result:

- `2026-06-28-claude-goal-vs-mission-complex-v1` wrote 20 records, but all 20
  were blocked by `api_usage_limit`; denominator for comparable completion and
  validator rates is therefore zero.

## Step 2b: Cost-Controlled Incremental Pilot

If the full run is too expensive or hits limits, continue with selected tasks
instead of rerunning completed work:

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-incremental \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids complex-failing-test-triage,complex-review-thread-resolution \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 3.0 \
  --mission-max-iter 2
```

Observed incremental result:

- `2026-06-28-claude-goal-vs-mission-incremental-v1` wrote 4 records.
- Official `/goal`: 2 completed comparable records, 2 validator passes.
- `/mission`: 2 `max_budget_usd` blocked records under the USD 3.00 cap.
- Total recorded cost: USD 9.39057695.

## Step 2c: Lightweight Mission Profile Pilot

If the full `/mission` profile is too expensive for paired evaluation, run fresh
selected tasks with the light profile:

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id 2026-07-01-claude-goal-vs-mission-light \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids complex-failing-test-triage \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 5.0 \
  --mission-max-iter 1 \
  --mission-profile light
```

Observed light-profile result:

- `2026-06-28-claude-goal-vs-mission-light-v1` wrote 2 completed comparable records.
- Official `/goal`: validator pass, 9.56 minutes, USD 3.00670750.
- `/mission` light: validator pass, 5.27 minutes, USD 2.00569500.
- This supports only a one-task light-profile hypothesis. Run 3-5 fresh tasks
  before using any broad cost or runtime claim.

## Step 2d: Quality-Focused Critical Pilot

If the goal is to test where `/mission` may produce higher quality rather than
lower cost, use `tasks.quality.json` and the quality profile:

```bash
STARTING_COMMIT=$(git rev-parse HEAD)
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.quality.json \
  --run-id 2026-07-01-claude-goal-vs-mission-quality \
  --starting-commit "$STARTING_COMMIT" \
  --task-ids quality-critical-release-governance \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --mission-max-iter 2 \
  --mission-profile quality
```

Observed quality-profile result:

- `2026-06-28-claude-goal-vs-mission-quality-v1` wrote 1 record out of 2 expected.
- Official `/goal`: `blocked_reason=api_usage_limit`, 2.53 minutes, USD 1.01481150 before stop.
- `/mission`: 0 records because `--stop-on-blocked` stopped after the `/goal` block.
- This is not a quality result. Rerun only after confirming the Claude Code
  workspace API limit is clear.

## Step 3: Validation

```bash
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
```

## Step 4: Report Update

Update both reports:

- `benchmarks/mission-vs-goal/report.md`
- `benchmarks/mission-vs-goal/report.ja.md`

Required report language:

- State the exact run id, starting commit, task count, and scoring method.
- Separate `completed`, `failed`, and `blocked` records.
- Exclude blocked records from capability conclusions.
- Keep `quality_score_method=automated_heuristic_not_blind_human` unless a
  separate blind human review actually happened.

Allowed claim shape:

> In a controlled Claude Code 10-task paired run, official `/goal` had X and
> `/mission` had Y under automated heuristic scoring.

Not allowed:

> `mission` is smarter than official `/goal`.

> `mission` won/lost because one arm was blocked by API usage limits.
