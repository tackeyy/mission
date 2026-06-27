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

The completed evidence therefore supports only a one-task smoke result. It does
not support a full 10-task performance claim.

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
