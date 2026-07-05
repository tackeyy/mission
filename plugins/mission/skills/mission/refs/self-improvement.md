# /mission Self-Improvement Loop

Use `scripts/mission-audit.py` when you want Codex or Claude Code to review recent
mission runs and turn the findings into the next improvement mission.

## Manual audit

```bash
python3 scripts/mission-audit.py --root ~/projects --since 2026-06-18
```

Use `mission-state.py stats` for quick numeric health checks and
`scripts/mission-audit.py` for improvement work. Both commands accept repeated
`--root` arguments, but the audit script adds findings, self-improvement prompts,
and issue-oriented diagnostics.

```bash
python3 skills/mission/bin/mission-state.py stats \
  --root ~/projects \
  --root ~/workspace \
  --since 2026-06-18
```

Write a report:

```bash
python3 scripts/mission-audit.py \
  --root ~/projects \
  --since 2026-06-18 \
  --out docs/audit-2026-06-18.md
```

Limit the scope to runs updated after a known fix commit:

```bash
python3 scripts/mission-audit.py \
  --root ~/projects \
  --repo ~/projects/mission \
  --after-commit 319d02d
```

## Self-improvement prompt

Generate a prompt that can be pasted directly into Codex or Claude Code:

```bash
python3 scripts/mission-audit.py \
  --root ~/projects \
  --since 2026-06-18 \
  --self-improvement-prompt
```

Then run the generated `/mission ...` prompt. The generated prompt asks the agent
to focus on P0/P1 findings first, add tests for any code changes, and rerun the
audit before reporting completion.

Before an agent creates GitHub issues from audit findings, it must first search
existing open and closed issues for duplicates. The created issue body must
include the search terms or related issue numbers that were checked, plus the
reason the new issue is not a duplicate. The issue body must also include an
engineering review summary from a development/tech-lead review provider covering
correctness, implementation approach, test plan, and OSS portability. Maintainer
workflows may use a local `development` skill for that review, but OSS defaults
should describe the requirement generically rather than hard-coding private
skill names.

## Suggested automation

Keep the automation read-only by default:

1. Run `scripts/mission-audit.py` after a batch of mission work.
2. If the report contains P0/P1 findings, search existing GitHub issues and
   prepare duplicate-check evidence before deciding whether new issues are
   needed.
3. Review any proposed new issue with a development/tech-lead reviewer and
   include the review summary in the issue body.
4. Create a Codex or Claude Code thread with the generated self-improvement
   prompt.
5. Let `/mission` implement the fix with normal threshold gates and tests.

Do not have a shell script directly edit files from the audit result. The script
should identify findings; `/mission` should still plan, implement, review, score,
and verify the improvement.
