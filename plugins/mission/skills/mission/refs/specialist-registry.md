# Specialist Registry - optional external specialist skills

## Purpose and Background

`mission` owns the loop, state gate, and stop/continue decision. External specialist skills are optional evidence providers that can improve planning, execution checks, and review quality when a task clearly fits a domain such as frontend, backend, security, documentation, or product design.

The registry avoids hard-coding a single user's local skill set into the orchestrator. It gives `/mission` a portable way to classify a mission, select available specialist skills, and degrade gracefully when a referenced skill is missing.

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
2. **Project registry**: repository-local policy, usually `skills/mission/refs/specialist-registry.md` or a future `.mission/specialists.yml`.
3. **User registry**: personal default mappings, for example `~/.config/mission/specialists.yml`.
4. **Beginner preset**: built-in fallback mapping from task profile to role names.
5. **No specialist**: continue with the core mission subskills only.

Project entries may disable a user-level default for a repository by setting `enabled: false` for that role or skill.

## YAML Schema

Future machine-readable registries should use this shape. Unknown keys are ignored with a warning, so older orchestrators can continue safely.

```yaml
version: 1
presets:
  docs:
    task_profiles: [documentation, protocol]
    specialists:
      - role: doc-writer
        skill: dev-doc-writer
        phases: [planning, execution, review]
        required: false
        evidence: doc_accuracy
specialists:
  - role: security-reviewer
    skill: dev-security-reviewer
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
| `task_profiles` | Profiles that make the specialist relevant. |
| `phases` | Allowed phases: `planning`, `execution`, `review`, `scoring`, `critic`. |
| `required` | If `true`, missing skill becomes a blocker. Default `false`. |
| `max_calls_per_iteration` | Soft limit to prevent runaway specialist calls. |
| `unavailable` | `continue`, `warn`, or `halt`. Default `continue`. |
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

## Fallback and Missing Skills

Default behavior is graceful degradation:

- Missing optional skill: record `missing`, continue with core subskills.
- Registry file absent: use beginner presets or no specialist.
- Invalid YAML: warn, ignore invalid registry, continue.
- Skill exists but cannot be invoked in the current agent: record `unavailable`, continue.
- `required: true` with `unavailable: halt`: mark a blocker only if the user or project explicitly made that specialist mandatory.

Never invent a specialist result. If a specialist cannot run, the audit log should say so plainly.

## Orchestrator-Skill Handling

Some skills are themselves orchestrators or broad methodologies, for example `development`. Treat them as advisory only unless the user explicitly asks to delegate the mission to that orchestrator.

Rules:

- Do not nest a second completion loop inside `/mission` by default.
- Prefer narrower specialists (`dev-backend`, `dev-frontend`, `dev-unit-tester`) over a broad orchestrator when both match.
- If a broad orchestrator is selected, call it for bounded evidence such as "produce an implementation plan" or "review this design", not for autonomous end-to-end control.
- `/mission` remains responsible for state, scoring, threshold gates, and final reporting.

## Claude Code / Codex Graceful Degradation

Claude Code may have `Skill(...)` calls, forked contexts, and packaged hooks. Codex may expose skills differently, ignore `context: fork`, or rely on natural-language role switching.

The registry must therefore be interpreted as intent:

- If a named skill is callable, use it according to the selected phase.
- If a named skill is visible only as instructions, adopt its checklist manually and record that it was applied inline.
- If neither is available, continue with the core loop and record the missing evidence source.
- Parallel specialist review is an optimization for Claude Code, not a correctness requirement for Codex.

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
  "specialists_selected": [
    {
      "role": "doc-writer",
      "skill": "dev-doc-writer",
      "phases": ["planning", "execution", "review"],
      "status": "used",
      "source": "user",
      "evidence_path": ".mission-state/archive/iter-1-doc-writer.md"
    }
  ],
  "specialists_unavailable": [
    {
      "role": "security-reviewer",
      "skill": "dev-security-reviewer",
      "reason": "not installed"
    }
  ]
}
```

Use `task_profile` as an object/dict for the classification record, `specialists_mode` for automatic or manual selection mode, `specialists_selected` for selected specialist evidence, and `specialists_unavailable` for missing or unavailable specialists.

## Phased Rollout

1. **Docs-only protocol**: document selection rules and update SKILL.md to mention optional evidence providers.
2. **Manual audit fields**: record `task_profile`, selected specialists, and missing specialists in assumptions/archive notes.
3. **YAML registry parser**: add schema validation and deterministic merge order.
4. **State integration**: add first-class state fields through `mission-state.py`.
5. **Preset tuning**: refine beginner presets from real mission logs.
6. **Strict mode**: optionally let projects require specific specialists for high-risk profiles.
