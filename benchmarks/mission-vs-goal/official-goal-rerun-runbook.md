# Official `/goal` vs `/mission` Rerun Runbook

Status: prepared before the Claude Code workspace API usage limit resets.

The 2026-06-28 JST smoke run is blocked, not comparative evidence. The raw
Claude result for the `/mission` arm reported a workspace API usage limit and
said access resumes on 2026-07-01 00:00 UTC, which is 2026-07-01 09:00 JST.

## Objective

Run a clean paired comparison between:

- `claude_code_goal_command`: Claude Code official built-in `/goal` command.
- `mission`: `/mission` plugin workflow.

Keep this comparison separate from the older local `goal_only` baseline.

## Preconditions

1. Run no earlier than 2026-07-01 09:00 JST unless the Claude Code usage limit is
   independently confirmed clear.
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
