# Mission

Draft a public-facing comparison paragraph for the `mission` vs goal-only pilot using only benchmark-backed claims.

# Plan

Use the benchmark package evidence only:

- Sample size: 10 fixed pilot tasks and 2 arms, for 20 expected paired runs.
- Current measurement status: 0 of 20 paired benchmark runs completed.
- Supported claim: the benchmark protocol and package are implemented and validated.
- Unsupported claim: any outcome advantage between the two arms.

# Execution

Public-facing comparison paragraph:

The `mission` vs goal-only benchmark protocol is implemented and validated for a 10-task internal pilot, with 20 paired runs planned across two arms. As of the current measured report, 0 of those 20 paired runs have been completed, so the evidence supports only a readiness claim: the task set, protocol, schemas, and validation checks are in place for a controlled comparison. We are not making an outcome comparison between the two arms yet; any future performance statement should report the completed denominator, task mix, scoring method, and aggregate results.

# Review

Validator checklist:

- Includes sample size: Pass. The paragraph states 10 fixed tasks, 2 arms, and 20 planned paired runs.
- Includes scope limitation: Pass. The paragraph limits the claim to an internal pilot protocol/package readiness and states that 0 of 20 paired runs are complete.
- Avoids broad advantage language: Pass. The paragraph does not make an outcome comparison between the two arms.
- Uses only benchmark-backed claims: Pass. The paragraph is grounded in `benchmarks/mission-vs-goal/report.md`, `README.md`, and `tasks.json`.

# Score

Composite score: 4.6 / 5.0

Item scores:

- Mission achievement: 4.6
- Accuracy: 4.7
- Completeness: 4.5
- Usability: 4.5

Reviewer consensus is omitted because this Simple mission used one self-reviewer; the score is based on the four applicable items.

# Stop Decision

Pass. The draft satisfies the validator, stays within the benchmark evidence, and does not require another iteration.

# Evidence

- `benchmarks/mission-vs-goal/tasks.json` defines 10 fixed pilot tasks and the `goal_only` and `mission` arms.
- `benchmarks/mission-vs-goal/README.md` defines the paired benchmark protocol and marketing guardrails.
- `benchmarks/mission-vs-goal/report.md` states that 20 paired runs are expected and 0 of 20 paired benchmark runs have been completed.
- `benchmarks/mission-vs-goal/report.md` states that current evidence supports package-readiness claims, not workflow-performance claims.

# Assumptions

- "Public-facing" means suitable for a report, README, or release note without unsupported comparative performance claims.
- Because no paired outcome metrics are measured yet, the comparison paragraph should compare readiness and protocol status only.
