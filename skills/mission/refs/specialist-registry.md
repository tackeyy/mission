# Specialist Registry - optional external specialist skills

## Purpose and Background

`mission` owns the loop, state gate, and stop/continue decision. External specialist skills are optional evidence providers that can improve planning, execution checks, and review quality when a task clearly fits a domain such as frontend, backend, security, documentation, or product design.

The registry avoids hard-coding a single user's local skill set or preferred external reviewer into the orchestrator. It gives `/mission` a portable way to classify a mission, select available specialist skills or command providers, and degrade gracefully when a referenced provider is missing.

## Beginner Presets

Beginner presets are small named bundles that map common mission shapes to specialist roles. They are suggestions, not mandatory subskills.

| Preset | Typical trigger | Suggested roles |
|---|---|---|
| `docs` | README, protocol, reference, changelog | `doc-writer`, `doc-reviewer` |
| `frontend` | React/Vue UI, layout, accessibility, visual QA | `frontend`, `designer-product`, `integration-tester` |
| `backend` | API, business logic, database integration | `backend`, `api-designer`, `unit-tester` |
| `security` | auth, permissions, secrets, high-risk input handling | `security-reviewer`, `backend` |
| `infra` | CI/CD, deployment, Docker, cloud config | `infra`, `integration-tester` |
| `research` | market, strategy, competitive, factual synthesis | `researcher`, `analyst`, `document-reviewer` |

The orchestrator may infer a preset during Phase 1, then refine it with project/user registry entries.

## Registry Precedence

When multiple registry sources exist, apply them in this order:

1. **User instruction in the current mission**: explicit "use X" or "do not use X" wins.
2. **Explicit CLI registry**: `mission-state.py specialists recommend --registry <path>`.
3. **Project registry**: repository-local policy, `.mission/specialists.yml`.
4. **User registry**: personal default mappings, `~/.config/mission/specialists.yml`.
5. **Skill/plugin manifests**: installed skill manifests such as `~/.codex/skills/*/mission-specialist.yml` or `~/.claude/skills/*/mission-specialist.yml`.
6. **Beginner preset**: built-in fallback mapping from task profile to role names.
7. **No specialist**: continue with the core mission subskills only.

Project entries may disable a user-level default for a repository by setting `enabled: false` for that role or skill. `--no-default-skill-roots` disables user registry and default skill/plugin manifest discovery for deterministic tests or isolated runs; project registries still apply.

## Provider Kinds

`kind: skill` remains the default. `kind: command` lets a registry describe a local CLI provider without adding provider-specific code to mission core.

```yaml
version: 1
specialists:
  - role: oracle-reviewer
    kind: command
    command: oracle
    args: ["review", "--stdin"]
    task_profiles: [architecture, product, research, documentation, security]
    phases: [planning, review, critic]
    required: false
    max_calls_per_iteration: 1
    unavailable: continue
    auto_use:
      min_complexity: Complex
      when: [pr_review, strategy, architecture, security, stalled_iteration]
    risk:
      external_service: true
      browser_automation: true
      may_consume_paid_quota: true
      first_use_confirmation: true
```

This `oracle-reviewer` entry is an example manifest shape only. Mission core must not contain oracle-specific browser automation, API calls, or scoring logic. The provider produces evidence; `mission-reviewer`, `mission-scorer`, and `mission-state.py mark-passes` remain the completion gates.

Command providers run through `mission-state.py specialists invoke-command`, which uses argv arrays and stdin/stdout capture rather than shell interpolation. The runner records stdout, stderr, exit status, and archived evidence under `.mission-state/archive`, then appends a `specialist_invocations` entry with `mode=command-provider`. Failed or unavailable optional command providers are logged and the mission continues with core reviewers.

## YAML Schema

Future machine-readable registries should use this shape. Unknown keys are ignored with a warning, so older orchestrators can continue safely.

```yaml
version: 1
presets:
  docs:
    task_profiles: [documentation, protocol]
    specialists:
      - role: doc-writer
        skill: documentation-provider
        phases: [planning, execution, review]
        required: false
        install_hint: false
        evidence: doc_accuracy
specialists:
  - role: security-reviewer
    skill: security-review-provider
    task_profiles: [security, auth, payment]
    phases: [planning, review]
    required: false
    max_calls_per_iteration: 1
    unavailable: continue
    notes: "Use for security-sensitive diffs."
overrides:
  - match:
      paths: ["docs/**", "README*.md"]
    add_roles: [doc-writer]
    remove_roles: []
```

Fields:

| Field | Meaning |
|---|---|
| `version` | Schema version. Start with `1`. |
| `presets.*.task_profiles` | Profiles that activate the preset. |
| `specialists[].role` | Stable logical role name used in state/audit logs. |
| `specialists[].skill` | Actual skill name when available in the current agent. |
| `kind` | `skill` or `command`. Defaults to `skill`. |
| `command` | Local executable for `kind: command`; invoked without shell interpolation. |
| `args` | Optional argv list for `kind: command`. |
| `task_profiles` | Profiles that make the specialist relevant. |
| `phases` | Allowed phases: `planning`, `execution`, `review`, `scoring`, `critic`. |
| `required` | If `true`, missing skill becomes a blocker. Default `false`. |
| `install_hint` | If `false`, a missing optional provider degrades to core review instead of recommending installation. Built-in portable presets use `false`; explicit project/user registries default to `true`. |
| `max_calls_per_iteration` | Soft limit to prevent runaway specialist calls. |
| `unavailable` | `continue`, `warn`, or `halt`. Default `continue`. |
| `auto_use.min_complexity` | Minimum mission complexity for automatic selection, such as `Complex`. |
| `risk.first_use_confirmation` | If `true`, require provider consent before automatic use. |
| `risk.external_service`, `risk.browser_automation`, `risk.may_consume_paid_quota` | Risk flags used for audit and confirmation policy. |
| `overrides` | Path or mission-text rules that add/remove roles. |

## `task_profile` Classification

Phase 1 classifies the mission into one primary `task_profile` and zero or more secondary profiles. Examples:

| Profile | Signals |
|---|---|
| `documentation` | README, docs, guide, protocol, reference, changelog |
| `frontend` | UI, component, CSS, accessibility, browser screenshots |
| `backend` | API, service logic, data validation, workers |
| `database` | schema, migration, query, persistence |
| `security` | auth, secrets, permissions, injection, PII |
| `testing` | unit, integration, E2E, flaky tests, coverage |
| `infra` | deployment, CI, Docker, cloud, observability |
| `product` | PRD, user workflow, UX, acceptance criteria |
| `research` | market, competitor, source-backed analysis |
| `strategy` | strategic positioning, roadmap, KPI, differentiation, recommendation |
| `financial` | ROI, NPV, business case, revenue model, sensitivity analysis |
| `risk` | risk, regulation, compliance, scenario analysis |
| `general` | no strong specialist signal |

Classification should be recorded as evidence, not treated as an irreversible decision. If later files or reviews reveal a better profile, update the audit note and specialist list for the next iteration.

## Phase Usage

Specialists provide evidence; they do not own the mission loop.

| Phase | How specialists may be used |
|---|---|
| Phase 1 | Classify `task_profile`, select specialists, record why each was selected or skipped. |
| Phase 2 | Provide planning constraints, risk notes, or acceptance criteria. |
| Phase 3 | Assist execution only when the task profile is strong and the specialist is available. |
| Phase 4 | Review relevant diffs or artifacts as additional evidence for mission-reviewer. |
| Phase 5 | Feed evidence to mission-scorer; specialists should not directly set pass/fail. |
| Phase 6 | Inform critic next steps when scores are below threshold. |

Core subskills remain authoritative for the standard loop: mission-planner, mission-executor, mission-reviewer, mission-scorer, and mission-critic.

## Dry-Run Recommendation Command

`mission-state.py specialists recommend` provides a deterministic dry-run path for Phase 1 specialist selection.

Example:

```bash
python3 skills/mission/bin/mission-state.py specialists recommend \
  --task "Implement a React UI component with accessibility tests" \
  --installed-skills frontend-provider \
  --json
```

The command classifies `task_profile`, discovers installed skills and command providers, ranks candidates, and returns a `specialists_decision`. It does not install external skills or execute command providers. Use `--record-state` only after `init` when the recommendation should be persisted to the current `.mission-state` session.

The recommendation output also includes `specialists_phase_plan`, a bounded advisory plan grouped by `planning`, `execution`, `review`, and `synthesis`. It is a scheduling hint, not a second orchestrator loop. It helps development registries place implementation providers before test/review providers, and strategy registries place market/financial evidence before strategy synthesis. The plan must remain based on generic roles from registries, not maintainer-local skill names.

Command provider invocation is a separate evidence step:

```bash
python3 skills/mission/bin/mission-state.py specialists invoke-command \
  --provider oracle-reviewer \
  --iteration 1 \
  --phase review \
  --input-file /tmp/mission-review-context.md \
  --json
```

The input file is wrapped in a JSON packet with mission, provider, iteration, and phase metadata, then sent to the configured command over stdin. The provider cannot set `passes`, cannot call `mark-passes`, and cannot alter mission state except through the invocation evidence recorded by the runner.

Command providers can define a result contract:

```yaml
result_contract:
  min_non_template_chars: 200
  forbidden_markers:
    - "Browser Review Prepared"
```

If a command exits successfully but only returns a preparation banner or less than the required non-template evidence, the runner records `status: prepared` instead of `completed`. `prepared` and `awaiting-input` are terminal accounting statuses for transparency, but they are not applied result evidence. A provider marked `required: true` must produce `completed`, `inline-applied`, or `skill-tool-applied` evidence before `mission-state.py mark-passes` can succeed.

The `oracle-reviewer` provider role gets a conservative default result contract even if a project registry omits one. Its default rejects common browser-review preparation markers such as prompt/result/packet paths and review URLs, so an exit code of 0 cannot satisfy required evidence unless the provider returns substantive findings.

For providers with `risk.first_use_confirmation: true`, record consent after a user approval boundary:

```bash
python3 skills/mission/bin/mission-state.py specialists consent \
  --provider oracle-reviewer
```

Consent is stored in `~/.config/mission/provider-consent.json` by default. Tests and isolated runs can pass `--consent-file <path>`.

If Phase 1 ended with `specialists_decision.action: ask-user`, an applied invocation for a not-yet-selected candidate must include `--selection-source confirmed-user` after the user confirms it:

```bash
python3 skills/mission/bin/mission-state.py specialists log-invocation \
  --iteration 1 \
  --phase review \
  --role strategy-review \
  --skill strategy-review-provider \
  --mode codex-inline \
  --status inline-applied \
  --selection-source confirmed-user
```

This writes both `specialist_invocations[]` evidence and the matching `specialists_selected[]` metadata. For command providers, pass the same option to `specialists invoke-command`.

## Fallback and Missing Skills

Default behavior is graceful degradation:

- Missing optional skill: record `missing`, continue with core subskills.
- Missing optional command provider: record `provider-unavailable`, continue with core subskills.
- Registry file absent: use beginner presets when matching providers are already installed, otherwise continue with core subskills.
- Invalid YAML: warn, ignore invalid registry, continue.
- Skill exists but cannot be invoked in the current agent: record `unavailable`, continue.
- Command exits non-zero: archive stdout/stderr/exit status, record `failed`, continue unless a future strict-mode policy makes that provider mandatory.
- `required: true` with `unavailable: halt`: mark a blocker only if the user or project explicitly made that specialist mandatory.

Never invent a specialist result. If a specialist cannot run, the audit log should say so plainly.

## Orchestrator-Skill Handling

Some skills are themselves orchestrators or broad methodologies, for example `development`. Treat them as advisory only unless the user explicitly asks to delegate the mission to that orchestrator.

Rules:

- Do not nest a second completion loop inside `/mission` by default.
- Prefer narrower specialists (`backend-provider`, `frontend-provider`, `unit-test-provider`) over a broad orchestrator when both match.
- If a broad orchestrator is selected, call it for bounded evidence such as "produce an implementation plan" or "review this design", not for autonomous end-to-end control.
- Registry candidates marked `bounded_use: true`, `broad_orchestrator: true`, or described as a broad orchestrator are removed from execution-phase recommendations.
- Applied evidence for a bounded orchestrator must include `--bounded-purpose "<limited artifact>"`; execution-phase application is rejected.
- `/mission` remains responsible for state, scoring, threshold gates, and final reporting.

Before the final report, run:

```bash
python3 skills/mission/bin/mission-state.py specialists summary --json
```

Use its `kind` and `source` fields in the `【Specialists】` line so command providers, actual Skill tool calls, and Codex inline application are not collapsed into one label.

## Claude Code / Codex Graceful Degradation

Claude Code may have `Skill(...)` calls, forked contexts, and packaged hooks. Codex may expose skills differently, ignore `context: fork`, or rely on natural-language role switching.

The registry must therefore be interpreted as intent:

- If a named skill is callable, use it according to the selected phase.
- If a named skill is visible only as instructions, adopt its checklist manually and record that it was applied inline.
- If neither is available, continue with the core loop and record the missing evidence source.
- Parallel specialist review is an optimization for Claude Code, not a correctness requirement for Codex.
- If a candidate is available but intentionally not used, record `status=skipped` with a concrete reason instead of leaving the candidate unaccounted for.
- Keep the system hackable: user-installed skills, command providers, and project-local plugins are optional evidence sources by default. Mission core owns the loop, state, audit, and safety boundaries, but should not hard-code provider-specific authority.

For `Critical` missions, every available candidate from the Phase 1 recommendation must be accounted for as used, skipped, unavailable, or failed. For `Complex` missions, apply the same rule to security, testing, and infra candidates because those profiles can materially change the risk of the final outcome. Apply the database/backend rule only when schema, migration, query, SQL, database, or persistence signals make database impact concrete. `Standard` missions should record skips when the decision is non-obvious; `Simple` missions may rely on the core loop unless a project policy says otherwise. Use `mission-state.py specialists accounting --json` before completion to surface required unaccounted candidates; this is a warning-oriented accounting check, not a blanket hard gate for all optional plugins.

## Audit and State Fields

The orchestrator preserves enough traceability to explain specialist selection with the first-class fields managed by `mission-state.py`:

```json
{
  "task_profile": {
    "primary": "documentation",
    "secondary": ["protocol"],
    "preset": "docs",
    "signals": ["registry docs update", "state field alignment"]
  },
  "specialists_mode": "auto",
  "specialists_candidates": [
    {
      "role": "doc-writer",
      "skill": "documentation-provider",
      "score": 0.82,
      "installed": true,
      "reason": "documentation profile match"
    }
  ],
  "specialists_selected": [
    {
      "role": "doc-writer",
      "skill": "documentation-provider",
      "phases": ["planning", "execution", "review"],
      "status": "selected",
      "source": "preset:docs"
    }
  ],
  "specialists_unavailable": [
    {
      "role": "security-reviewer",
      "skill": "security-review-provider",
      "reason": "not installed"
    }
  ],
  "specialists_decision": {
    "policy": "auto",
    "action": "select",
    "reason": "top candidate documentation-provider is installed with score 0.82",
    "prompted_user": false
  },
  "specialist_invocations": [
    {
      "iteration": 1,
      "phase": "review",
      "role": "doc-writer",
      "skill": "documentation-provider",
      "mode": "codex-inline",
      "status": "inline-applied",
      "timestamp": "2026-06-19T08:00:00Z",
      "evidence_path": ".mission-state/archive/iter-1-deadbeef-specialist-documentation-provider.md"
    },
    {
      "iteration": 1,
      "phase": "planning",
      "role": "security-reviewer",
      "skill": "security-review-provider",
      "mode": "fallback-core",
      "status": "skipped",
      "reason": "Core reviewer covered the security checklist for this docs-only change",
      "timestamp": "2026-06-19T08:03:00Z"
    }
  ]
}
```

Use `task_profile` as an object/dict for the classification record, `specialists_mode` for automatic or manual selection mode, `specialists_candidates` for ranked candidates, `specialists_selected` for selected specialist intent, `specialists_unavailable` for missing or unavailable specialists, and `specialists_decision` for the policy outcome. Use `specialist_invocations` for append-only execution evidence after selection: actual Skill tool calls, Codex inline application, natural-language role application, fallback-core usage, skips, unavailable cases, and failures.

`specialists_selected` and `specialist_invocations` intentionally remain separate. Selection answers "what should be used"; invocation answers "what was actually used or skipped." This keeps ADR-001's audit requirement intact without pretending Codex inline usage is a real forked Skill tool call.

If a specialist appears in `specialist_invocations` but not in `specialists_selected`, report it as `unselected-manual`: evidence was used after the Phase 1 selection checkpoint, but the selection intent was not recorded. This is an observability warning for optional specialists, not a mission failure unless a future strict-mode policy marks that specialist as required.

## Phased Rollout

1. **Docs-only protocol**: document selection rules and update SKILL.md to mention optional evidence providers.
2. **Manual audit fields**: record `task_profile`, selected specialists, and missing specialists in assumptions/archive notes.
3. **YAML registry parser**: add schema validation and deterministic merge order.
4. **State integration**: add first-class state fields through `mission-state.py`.
5. **Preset tuning**: refine beginner presets from real mission logs.
6. **Strict mode**: optionally let projects require specific specialists for high-risk profiles.
