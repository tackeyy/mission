# ADR-001: Specialist Skill Auto-Selection and Interactive Fallback Policy

## Status

Accepted

## Date

2026-06-19

## Context

`mission` should improve output quality by using specialist skills when they are relevant to a mission. Examples include development, frontend, backend, testing, security, documentation, design, and research skills.

Because `mission` is OSS, users will not share the same installed skill set. Some users may have official or bundled skills only. Others may have private or organization-specific skills. Requiring every user to pass skill names as arguments would make `mission` harder to use, especially for beginners.

At the same time, silently installing or invoking arbitrary external skills is not acceptable. Skill selection and installation can affect security, privacy, cost, and execution quality. `mission` also must remain portable across Claude Code and Codex, where skill discovery and invocation capabilities differ.

## Decision

`mission` will default to automatic specialist selection.

The orchestrator will classify the task during Phase 1, build a `task_profile`, rank available specialist candidates from project/user registries and beginner presets, and automatically select specialists when confidence is high and the skill is already available.

`mission` will ask the user only in these cases:

- candidate ranking is uncertain or closely tied
- a preset or specialist is being used for the first time
- a useful specialist is missing and installation is recommended
- a `required: true` specialist is missing
- the task is high-risk, security-sensitive, production-facing, or irreversible
- the registry explicitly requires confirmation

External specialists are evidence providers, not final judges. They may strengthen planning, execution, review, scoring evidence, or critic feedback, but `mission-scorer` and `mission-state.py mark-passes` remain the final completion gate.

`mission` will not silently install external skills. Installation requires an explicit user approval step and trusted source metadata.

The portable interactive fallback is chat-based selection, such as a numbered list. Custom graphical pickers are not required for correctness.

Provider registries may describe two provider kinds:

- `kind: skill`: a Claude/Codex skill selected as optional evidence.
- `kind: command`: a local command invoked through `mission-state.py specialists invoke-command` with argv arrays, stdin/stdout capture, archived evidence, and `specialist_invocations` logging.

Command providers are treated as local code execution. Project registries can disable user-level defaults with `enabled: false`, and providers with `risk.first_use_confirmation: true` require a user-scoped consent allowlist before automatic use. Provider failures or missing commands degrade to logged evidence and core reviewers unless a future strict policy explicitly makes that provider mandatory.

No provider gets mission-core-specific authority. In particular, high-value reviewers such as `oracle` must be represented as external manifests or examples, not hard-coded branches in `mission` core.

## OSS Portability Boundary

The public `mission` repository must not embed a maintainer's personal skill names, private workflow taxonomy, local file paths, or organization-specific agent teams as built-in behavior. Personal or team-specific specialists belong in user/project configuration, not in OSS defaults.

Allowed in OSS:

- generic provider protocols, registry schemas, scoring/audit semantics, and safety rules
- portable beginner presets that describe broad capabilities rather than a private skill collection
- tests that use neutral fixture provider names such as `external-code-reviewer` or `market-research-provider`
- documentation examples that clearly mark local/private skill names as examples only

Not allowed in OSS:

- adding personal skills to `BUILTIN_SPECIALIST_CANDIDATES`
- bundling private `mission-specialist.yml` manifests for one maintainer's local skills
- assuming that any local skill path under a maintainer's home directory exists for other users
- making pass/fail gates depend on private skills or private command providers

If a maintainer wants `mission` to use personal skills, they should define those bindings outside the public repo through `~/.config/mission/specialists.yml`, a private project `.mission/specialists.yml`, or installed skill manifests. The OSS code should only make those extension points reliable and auditable.

## State and Audit Requirements

Specialist selection must be auditable. `.mission-state` should record:

- `task_profile`
- `specialists_candidates`
- `specialists_selected`
- `specialists_unavailable`
- `specialists_decision`

The decision record should explain whether the policy was `auto`, `interactive`, `install-recommended`, `fallback`, or `halt`, and whether the user was prompted.

## Consequences

Positive:

- beginner users get useful defaults without learning specialist configuration first
- power users can customize behavior through project/user registries
- optional specialists improve quality without becoming hard dependencies
- missing specialists degrade gracefully to core `mission-*` skills
- high-risk or install-related choices remain under user control
- Claude Code and Codex can share the same policy through chat-based fallback

Negative:

- `mission` needs deterministic discovery, ranking, and policy tests
- first-use and high-risk prompts add some interaction cost
- install recommendations require a trust model before they can be automated
- state schema and audit output become more complex

## Alternatives Considered

### Always ask the user

Rejected. This would make `mission` less autonomous and would force beginners to understand specialist choices before they can get value.

### Require users to pass skill names as arguments

Rejected. This is useful for overrides, but it should not be the default user experience. It also does not help beginners discover useful specialists.

### Fully automatic installation

Rejected. Installing external code without explicit approval is unsafe for an OSS agent workflow.

### No external specialist integration

Rejected. It preserves simplicity, but leaves quality gains from user/project skills unused.

## Implementation Notes

The next implementation phase is tracked in GitHub issue #29: "Implement specialist auto-selection policy with interactive fallback".

The foundation from #28 introduced the specialist registry protocol and initial state fields:

- `task_profile`
- `specialists_mode`
- `specialists_selected`
- `specialists_unavailable`

The next phase should add discovery, candidate ranking, interactive fallback, install recommendation dry-runs, and expanded state metadata.

Issue #49 extends this ADR with deterministic registry discovery, `kind: command` provider support, first-use risk consent, and command-provider invocation evidence. This remains an optional evidence-provider path; pass/fail authority stays with the core mission loop and state gates.
