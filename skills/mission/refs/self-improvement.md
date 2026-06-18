# /mission Self-Improvement Loop

Use `scripts/mission-audit.py` when you want Codex or Claude Code to review recent
mission runs and turn the findings into the next improvement mission.

## Manual audit

```bash
python3 scripts/mission-audit.py --root /Users/tackeyy/dev --since 2026-06-18
```

Write a report:

```bash
python3 scripts/mission-audit.py \
  --root /Users/tackeyy/dev \
  --since 2026-06-18 \
  --out docs/audit-2026-06-18.md
```

Limit the scope to runs updated after a known fix commit:

```bash
python3 scripts/mission-audit.py \
  --root /Users/tackeyy/dev \
  --repo /Users/tackeyy/dev/mission \
  --after-commit 319d02d
```

## Self-improvement prompt

Generate a prompt that can be pasted directly into Codex or Claude Code:

```bash
python3 scripts/mission-audit.py \
  --root /Users/tackeyy/dev \
  --since 2026-06-18 \
  --self-improvement-prompt
```

Then run the generated `/mission ...` prompt. The generated prompt asks the agent
to focus on P0/P1 findings first, add tests for any code changes, and rerun the
audit before reporting completion.

## Suggested automation

Keep the automation read-only by default:

1. Run `scripts/mission-audit.py` after a batch of mission work.
2. If the report contains P0/P1 findings, create a Codex or Claude Code thread
   with the generated self-improvement prompt.
3. Let `/mission` implement the fix with normal threshold gates and tests.

Do not have a shell script directly edit files from the audit result. The script
should identify findings; `/mission` should still plan, implement, review, score,
and verify the improvement.
