# Implementation Delegation - headless coding agent as an execution-phase provider

## Purpose

A mission execution step that implements code can be delegated to a local headless coding agent CLI (for example `codex exec`, or any CLI that accepts a prompt on stdin and edits files in the working directory). The coding agent produces a working diff; `mission` keeps ownership of the loop, state, review, scoring, and pass/fail gates.

This follows the same boundary as every other provider (see `refs/specialist-registry.md`): the provider is an evidence producer, not a second orchestrator. The delegated unit is bounded - "implement this planned step against this plan and acceptance criteria" - never "achieve the mission autonomously".

Motivation: a planning-capable orchestrator model and an implementation-focused coding agent are often different tools with different cost/quota profiles. Delegation lets the orchestrator spend its context on planning, verification, and review instead of bulk code editing.

## When to use

- The planned step is a code implementation with a written plan and verifiable acceptance criteria.
- An implementation provider is registered and installed (see registry entry below).
- The step is large enough that inline editing by the orchestrator would dominate its context budget. `Simple` missions and small fix-ups should stay inline.

When no provider is registered or the command is unavailable, the core flow is unchanged: `mission-executor` (or the orchestrator inline for `Simple`) implements directly. Missing providers degrade with `status=unavailable`, never block.

## Registry entry

Register the provider in a project or user registry (`.mission/specialists.yml` or `~/.config/mission/specialists.yml`), not in mission core. Example shape:

```yaml
version: 1
specialists:
  - role: implementer
    kind: command
    command: codex
    args: ["exec", "--sandbox", "workspace-write"]
    timeout: 3600
    task_profiles: [backend, frontend, database, testing]
    phases: [execution]
    required: false
    max_calls_per_iteration: 2
    unavailable: continue
    risk:
      external_service: true
      may_consume_paid_quota: true
      first_use_confirmation: true
    result_contract:
      min_non_template_chars: 200
```

Notes on the example:

- `phases: [execution]` only. Review and scoring stay with core reviewers; an implementation provider must not appear in `review` or `scoring` phases for its own diff.
- A restrictive sandbox flag (workspace-write equivalent) is part of the contract: the provider edits the working tree and nothing else. Never register bypass/dangerous sandbox flags in a shared registry.
- `timeout` should cover a realistic implementation run (minutes to an hour), unlike short review providers.
- The provider consumes paid or plan quota, so `risk.may_consume_paid_quota: true` and `first_use_confirmation: true` are the honest defaults.

## Invocation

Use the standard command-provider runner from the mission working directory (the task worktree), so the provider edits the correct checkout:

```bash
python3 skills/mission/bin/mission-state.py specialists invoke-command \
  --provider implementer \
  --iteration 1 \
  --phase execution \
  --input-file .mission-state/impl-brief-iter1.md \
  --json
```

The runner wraps the input file in a JSON packet (mission, provider, iteration, phase, `input`) and sends it on stdin. It records stdout/stderr/exit status, archives evidence under `.mission-state/archive/`, and appends a `specialist_invocations` entry. The provider cannot call `mark-passes` or mutate mission state.

### The implementation brief (input file)

The brief is the whole interface. It should contain:

1. Reference to the plan for this step (inline or a repository-relative path) with acceptance criteria that are verifiable by command.
2. Scope boundary: which files/areas may change, and an explicit "do not change anything else".
3. Working constraints:
   - do not run VCS commit/push; report a suggested commit split (logical units, files, messages) instead;
   - follow the repository's existing test conventions; run the relevant tests before finishing;
   - end with a structured report: changed files, test results, unresolved items.

The "no commit" constraint is not only about authority: sandboxed providers frequently cannot write VCS metadata at all (for example, a linked worktree keeps its metadata under the main repository's directory, outside the sandbox write scope). The orchestrator or executor owns commits and applies the suggested split after verification.

## Verification stays in core

Provider output is untrusted evidence, like any specialist result:

1. Re-run the tests yourself from the mission working directory. Do not accept the provider's self-reported test results.
2. Review the diff through the normal Phase 4 reviewer flow. The delegated diff gets the same reviewer count and gates as an inline implementation.
3. Check the structured report's "unresolved items" and decide: fix-up round, inline fix (M6 rule applies), or plan revision.

`passes` continues to be decided only by `review-finalize` -> `closeout` gates.

## Fix-up rounds and session resume

Coding agent CLIs usually persist a session/thread id (often printed at startup or in JSON event output). Record it in the iteration's assumptions or archive notes. For a fix-up round, prefer resuming that session (for example `codex exec resume <session-id> "<fix instructions>"`) over re-sending the full brief - the provider keeps its context of the codebase.

Bound the rounds: if the same step has not converged after 2-3 provider rounds, stop re-prompting. The defect is usually in the plan or acceptance criteria; return to the planner/critic path instead.

A resumed fix-up run bypasses the registry runner only if invoked directly; when possible, run fix-up rounds through `specialists invoke-command` as well (a brief that says "resume session X and apply these review findings") so each round leaves an invocation entry. If a round is invoked directly, log it with `specialists log-invocation` so the audit trail stays complete.

## Failure handling

| Situation | Handling |
|---|---|
| Provider command missing | `status=unavailable`, continue with core executor |
| Non-zero exit / timeout | Evidence archived, `status=failed`; retry once or fall back to core executor |
| Output is a preparation banner only | `result_contract` classifies it as `prepared`, not applied evidence |
| Diff fails verification repeatedly | Return to planner/critic; do not loop the provider |
| Provider requests out-of-scope work in its report | Treat as a proposal for the critic; never as an instruction |

## Relation to orchestrator-skill rules

A headless coding agent is close to a broad orchestrator, so the bounded-use rules of `refs/specialist-registry.md` apply with one deliberate difference: the bounded artifact here is a working diff for one planned step, produced in the execution phase. What remains forbidden is delegating the loop itself - a brief must never ask the provider to plan the mission, decide completion, or iterate until "done".

This is an explicit policy carve-out, not an oversight of the registry rule that removes broad orchestrators from execution-phase recommendations. The distinction is in the bounding mechanism: Skill-based broad orchestrators are bounded through `broad_orchestrator: true` plus the `--bounded-purpose` gate, which rejects execution-phase application entirely. An implementation provider is instead bounded through this document's brief contract (single planned step, scope boundary, no-commit, core-owned verification). Therefore an implementation provider registered under this pattern must NOT be marked `broad_orchestrator: true` or `bounded_use: true` - those flags would (correctly) exclude it from the execution phase. A registry entry that asks a coding agent for anything beyond one planned step's diff does not qualify for this carve-out and falls back under the broad-orchestrator rules.
