# Specialist Registry - optional external specialist skills

## Purpose and Background

`mission` owns the loop, state gate, and stop/continue decision. External specialist skills are optional evidence providers that can improve planning, execution checks, and review quality when a task clearly fits a domain such as frontend, backend, security, documentation, or product design.

The registry avoids hard-coding a single user's local skill set or preferred external reviewer into the orchestrator. It gives `/mission` a portable way to classify a mission, select available specialist skills or command providers, and degrade gracefully when a referenced provider is missing.

## Beginner Presets

Beginner presets are small named bundles that map common mission shapes to specialist roles. They are suggestions, not mandatory subskills.

| Preset | Typical trigger | Suggested roles |
|---|---|---|
| `docs` | README, protocol, reference, changelog | `doc-writer`, `doc-reviewer` |
| `frontend` | React/Vue UI, layout, accessibility, visual QA | `frontend`, `ui-reviewer`, `integration-tester` |
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

## Version 2 planning activation

Complexity-triggered planning providers use the versioned registry contract. Keep it separate from version 1 so an older runtime cannot interpret an activation-only entry as an unrestricted provider.

| Source | Version 2 | Version 1 compatibility |
|---|---|---|
| Explicit CLI | a file with `schema: mission-specialist-registry/2` | a file with `version: 1` |
| Project | `.mission/specialists-v2.yml` | `.mission/specialists.yml` |
| User | `~/.config/mission/specialists-v2.yml` | `~/.config/mission/specialists.yml` |
| Installed manifest | `mission-specialist-v2.yml` | `mission-specialist.yml` |

The machine precedence is `explicit v2 > explicit v1 > project v2 > project v1 > user v2 > user v1 > installed v2 > installed v1`. Multiple explicit files within one version preserve CLI argument order. Supplying the same physical file twice, including through a hardlink or symlink alias, returns `duplicate-registry-input`; byte-identical files with different device/inode identities remain distinct inputs. Duplicate provider identities in one file, or in an unordered installed tier, fail closed. A higher invalid entry blocks fallback to the same lower identity. A document that cannot be parsed strictly creates an input-level precedence barrier: only an already valid, higher-priority explicit input may remain; same-tier and lower-priority inputs and built-in candidates produce no candidate, selection, or phase-plan entry. Version 2 uses `disabled: true` as a tombstone keyed only by canonical `provider_id`, so a different provider that shares a skill alias is not suppressed. Version 1 retains alias-compatible `enabled: false`; `enabled` in a version 2 candidate is rejected as a legacy-field mix.

Version 2 requires this root and does not permit a version 1 `specialists:` root in the same document:

```yaml
schema: mission-specialist-registry/2
specialists_v2:
  - role: deep-planning
    skill: deep-planning-provider
    task_profiles: [architecture]
    phases: [planning]
    activation:
      min_complexity: Complex
      auto_select_if: [complexity]
      when_any: [architecture, stalled_iteration]
      explicit_below_min: deny
```

The portable YAML subset permits candidate fields plus one nested mapping. Quoted `#`, commas, and escaped quotes retain their scalar meaning; nested flow collections such as `[[...]]` and `[{...}]` are rejected with the same depth contract as JSON. JSON-compatible finite numbers normalize to the same Python value and registry entry digest in JSON and YAML only when the token is at most 128 characters, integers stay within the portable safe range, and a decimal can be represented without overflow, underflow, negative zero, or precision loss. `timeout` is stricter: it must be an exact integer from 1 through 86400; booleans, fractions, zero, and non-finite values are rejected. Invalid numeric tokens return `invalid-registry-number` or `number-limit` without a traceback and create the same precedence barrier as every other invalid input; they do not globally abort or discard an already-valid higher-priority explicit input. Version 2 validates exact field types, including string identities, string-list profiles/phases, boolean flags, and the activation mapping; violations return `invalid-v2-candidate-type`. Missing schema, an unknown major, duplicate keys, a deeper mapping, or mixed version roots produce an ineligible diagnostic and zero candidates from that input. The official version 2 project, user, and installed-manifest paths are always parsed as version 2 and cannot downgrade based on their contents.

### Activation semantics

- `min_complexity` is a hard eligibility floor for automatic and explicit selection. The only ordered values are `Simple`, `Standard`, `Complex`, and `Critical`; missing, malformed, or `Unknown` context returns `unknown-complexity` for a floor-bearing provider.
- `auto_select_if` is an OR list. `profile` preserves profile-driven selection; `complexity` makes a provider eligible at its floor even when profile confidence is low.
- `when_any` is an optional OR list applied only to automatic selection. It is ANDed with the complexity, phase, and availability hard gates. An empty list matches nothing. Unsupported predicates make the configuration ineligible for every selection source.
- `stalled_iteration` matches only when the current iteration is at least two and the prior iteration did not pass.
- A complexity-triggered planning provider must explicitly allow `planning`. Phase lists are allow-lists; empty, unknown, and mismatched phases fail closed.
- Version 1 `auto_use.min_complexity` remains readable. Version 1 `auto_use.when` maps to `activation.when_any` with the same internal OR semantics. Do not put `activation` in a version 1 file.

### Selection and projection provenance

Persisted selection sources use `automatic`, `confirmed-user`, `user-instruction`, `manual`, or `task-required`. Legacy `auto` maps to `automatic`; legacy `user-specified` maps to `user-instruction`. `selection_source_raw` preserves the received literal. A provider cannot claim `automatic` through `log-invocation` or `invoke-command`; only the recommendation producer may create it.

Recommendation output records `provider_id`, registry source, registry entry digest, normalized activation digest, mission context digest, and `mission-specialist-registry-projection/1`. Each registry is opened once with a bounded nonblocking read, must be a regular file no larger than 1 MiB, and is bound to a single byte snapshot captured from one file descriptor with pre/post validation. Device, inode, mode, size, modification/change timestamps, and bytes read must remain consistent. A read or metadata failure returns the structured `registry-input-unreadable` barrier and closes the descriptor exactly once. Version detection, content digest, and parsing use those same bytes and never re-read or re-resolve the path. A symlink may identify the opened file, but a later target swap cannot change the bytes or lexical portable identity already captured. Process-local device/inode identity is used only for duplicate detection and is never persisted.

Persisted identities use `$PROJECT/...`, `$HOME/...`, or an opaque external digest; process-local absolute paths are never copied into state, sources, diagnostics, barriers, or installed inventory. Because `$EXTERNAL/sha256:...` is intentionally irreversible, its ordered input carries `resolution_mode: explicit-resupply-required`. Until the runtime application work in #395 is available, a command provider from such an input is ineligible for selection. The later runtime gate will require the same explicit registry to be supplied again for current revalidation.

The projection contains all ordered discovery inputs with portable identities and content digests, any `precedence_barriers`, and effective entries with explicit `projection_state` values such as `eligible`, `conflict`, `disabled`, `tombstone`, and `invalid-input-barrier`. `effective_projection_digest` binds the complete projection payload rather than only selected providers. A later application guard can therefore detect content, discovery-order, and semantic-state drift. This issue defines producer evidence only; it does not authorize or execute a provider.

`specialists recommend --record-state` treats the current state's complexity and iteration as authoritative. A supplied complexity that disagrees with state, or complexity/iteration drift detected again inside the write lock, returns exit 2 with `state-context-mismatch`, emits zero output selections, and leaves every persisted selection field unchanged. Without `--record-state`, `--complexity` remains a dry-run-only virtual context and does not mutate mission state.

Phase-plan construction reuses the same eligibility evaluator for every requested phase. It cannot insert a provider merely because a raw candidate lists that phase; complexity floors, phase allow-lists, activation predicates, availability, and selection source remain binding.

### Temporary command-provider portability gate

Issue #394 produces selection evidence but does not implement the runtime registry resolver planned in #395. During this interval, recommendation accepts a command provider only when its command is one bare PATH token, `args` is exactly empty, `env` is empty, the registry locator is reversible, and no explicit result contract must be persisted. The restriction applies to version 1 and version 2 across explicit, project, user, and installed-manifest inputs. A nonportable entry yields `non-portable-execution-config`, zero candidates/selections/unavailable entries/phase-plan providers, and an allowlisted `blocked_config_class`; raw command, argument, path, environment key/value, and nested configuration are not copied into output or state.

Skill providers remain eligible when their canonical identity is portable. Portable command entries remain stored and invokable by the existing runtime. Newly generated public provider records use a recursive declarative allowlist for candidates, selections, unavailable/ineligible diagnostics, installed inventory, projection inputs/barriers/effective entries, decisions, phase plans, and invocations. Unknown keys, wrong types, unknown enums/sources, excessive lengths, malformed digests, or unsafe nested values fail closed. Candidate scores are exact bounded numeric values from 0 through 1; booleans, non-finite floats, and oversized integers fail with a field-only diagnostic. Raw environment, risk, result-contract, activation, parser detail, and registry values are excluded; invalid version 1 policy enums are represented only by a fixed reason/field code and digests. The same validator runs before dry-run output and every atomic session-state publish, as well as before stdout, backup, archive, audit snapshot, summary/accounting, or invocation evidence can expose or copy existing state. Invocation commands validate the pending entry, prospective selection metadata, evidence locator, iteration, and timeout before activity mutation, process spawn, backup, or archive publication. `--evidence-output` accepts only a non-symlink regular UTF-8 file of at most 1 MiB; one file descriptor supplies both metadata checks and a bounded body snapshot, and a metadata or length change fails before state or artifact mutation. Evidence content is sanitized by the same local-locator tokenizer as command-provider output. Evidence is written to a temporary file only after preflight, revalidated before publication, and rolled back if the matching state publish fails. Unsafe legacy records fail closed with `unsafe-legacy-specialist-record` plus a field pointer and are never silently sanitized. Audit discovery isolates such a record as a typed `state_read_error`, omits its state body from the immutable snapshot, and continues with the remaining safe records; archive creation, state publication, and snapshot consumption still reject unsafe payloads. A safe legacy invocation may retain one bare command token, while path-bearing commands remain rejected. Public strings and provider output recognize embedded POSIX, bare/current/named-home-relative (including `~`, `~user`, `~+`, and `~-`), Windows absolute/drive-relative (including separator-free nonempty suffixes such as `C:file`), UNC/network, device, extended-length, and rooted locators after whitespace, `=`, `:`, quotes, or punctuation; rejection and redaction do not depend on a path appearing at the start of the string. A bare drive label such as `C:`, embedded general tilde text, multi-letter colon-delimited tokens, and complete well-formed HTTP(S) URL tokens remain unchanged, including URL paths containing colon or home-like segments. URL protection uses a strict raw allowlist: ASCII DNS names, IPv4, or bracketed IPv6, an optional numeric port from 1 through 65535, valid percent escapes, and no user information. Path accepts RFC 3986 pchar plus `/`; query and fragment additionally accept `?`. Unicode component text is retained, while raw backslashes, pipes, braces, brackets, controls, or other characters outside those component grammars fail closed. Missing or malformed hosts, brackets, ports, percent escapes, controls, backslashes, commas, or equals signs in the authority also fail closed. Every `file:` form, including relative, absolute, and authority forms, is a local locator. Nonempty arguments, environment configuration, filesystem-backed commands, irreversible external command registries, and explicit result contracts remain valid registry concepts, but new selection of them is temporarily fail-closed until #395 can re-resolve and revalidate current registry bytes immediately before invocation. Archive filename/identity collision handling remains owned by #387 and is not changed by this selection/publication contract.

## Provider Kinds

`kind: skill` remains the default. `kind: command` lets a registry describe a local CLI provider without adding provider-specific code to mission core. For delegating an implementation step's diff generation to a headless coding agent CLI, see `refs/implementation-delegation.md`.

```yaml
version: 1
specialists:
  - role: oracle-reviewer
    kind: command
    command: oracle
    args: []
    env:
      ORACLE_MISSION_WAIT_SECONDS: "900"
    timeout: 960
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
      browser_session_material: false
      may_consume_paid_quota: true
      first_use_confirmation: true
```

This `oracle-reviewer` entry is an example manifest shape only. Mission core must not contain oracle-specific browser automation, API calls, or scoring logic. The provider produces evidence; `mission-reviewer`, `mission-scorer`, and `mission-state.py mark-passes` remain the completion gates.

Command providers run through `mission-state.py specialists invoke-command`, which uses argv arrays and stdin/stdout capture rather than shell interpolation. The runner records stdout, stderr, exit status, and archived evidence under `.mission-state/archive`, then appends a `specialist_invocations` entry with `mode=command-provider`. Failed or unavailable optional command providers are logged and the mission continues with core reviewers.

Provider consent scopes are separate. Approval to send selected prompt/repository context to an external service does not imply approval to reuse browser session material, and neither implies paid API/model quota approval. A browser provider should default to manual login or an explicit `awaiting-input` result unless the user has also approved `browser_session_material` use for that run.

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
| `env` | Optional string key/value environment overrides for `kind: command`. Values are passed only to that provider process. |
| `timeout` | Optional exact integer command timeout from 1 through 86400 seconds for `kind: command`. CLI `--timeout` overrides this value but uses the same pre-spawn range validation. |
| `task_profiles` | Profiles that make the specialist relevant. |
| `phases` | Allowed phases: `planning`, `execution`, `review`, `scoring`, `critic`. |
| `required` | If `true`, missing skill becomes a blocker. Default `false`. |
| `install_hint` | If `false`, a missing optional provider degrades to core review instead of recommending installation. Built-in portable presets use `false`; explicit project/user registries default to `true`. |
| `max_calls_per_iteration` | Soft limit to prevent runaway specialist calls. |
| `unavailable` | `continue`, `warn`, or `halt`. Default `continue`. |
| `auto_use.min_complexity` | Minimum mission complexity for automatic selection, such as `Complex`. |
| `risk.first_use_confirmation` | If `true`, require provider consent before automatic use. |
| `risk.external_service` | Provider may send selected prompt, artifact, or repository context to an external service. |
| `risk.browser_automation` | Provider may launch or drive a browser. This does not by itself authorize reuse of an existing signed-in profile. |
| `risk.browser_session_material` | Provider may reuse browser session material such as cookies, profile copies, or existing authenticated browser state. This requires a separate approval boundary from external-send approval. |
| `risk.may_consume_paid_quota` | Provider may consume paid API/model quota. This requires a separate approval boundary from external-send or browser-session approval. |
| `overrides` | Path or mission-text rules that add/remove roles. |

## `task_profile` Classification

Phase 1 classifies the mission into one primary `task_profile` and zero or more secondary profiles. Examples:

| Profile | Signals |
|---|---|
| `architecture` | system design, architecture review, component boundaries, state machines |
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

### Tie Policy

If the top two installed optional candidates are tied within `0.05` score points on a low/medium-risk task, `/mission` auto-selects deterministically instead of asking the user. Ordering is score descending, source precedence, then skill name ascending. The alternate candidates remain in `specialists_candidates`, and `specialists_decision.reason` records `tie-break: auto-selected <top> over <alt>`. High-risk tasks, first-use confirmation, install recommendations, missing required providers, explicit registry confirmation, and low-confidence classifications still require confirmation or fallback.

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

Command provider registries may include `env` and `timeout` to make interactive wrappers complete in one `invoke-command` call. For example, an oracle browser wrapper can set `ORACLE_MISSION_WAIT_SECONDS=900` and `timeout: 960`; the provider may open the browser, wait for the reviewer to save the result, then return substantive stdout that satisfies the result contract. This remains generic provider configuration, not oracle authority inside mission core.

Command providers can define a result contract. While the temporary #394 portability gate is active, examples with nonempty `env` or path-bearing command arguments are schema examples only and are not selectable until #395:

```yaml
result_contract:
  min_non_template_chars: 200
  forbidden_markers:
    - "Browser Review Prepared"
  awaiting_input_markers:
    - "approval required:"
  awaiting_input_exit_codes: [75]
```

If a command exits successfully but only returns a preparation banner or less than the required non-template evidence, the runner records `status: prepared` instead of `completed`. If the provider output or exit code matches `awaiting_input_markers` or `awaiting_input_exit_codes`, the runner records `status: awaiting-input`. `prepared` and `awaiting-input` are terminal accounting statuses for transparency, but they are not applied result evidence. A provider marked `required: true` must produce `completed`, `inline-applied`, or `skill-tool-applied` evidence before `mission-state.py mark-passes` can succeed.

Every provider that requires result evidence must declare its result contract in the explicit registry entry. The contract should reject preparation-only markers such as prompt/result/packet paths and review URLs, so an exit code of 0 cannot satisfy required evidence unless the provider returns substantive findings.

For providers with `risk.first_use_confirmation: true`, record consent after a user approval boundary:

```bash
python3 skills/mission/bin/mission-state.py specialists consent \
  --provider oracle-reviewer
```

Consent is stored in `~/.config/mission/provider-consent.json` by default. Tests and isolated runs can pass `--consent-file <path>`.

This consent records provider first-use only. It must not be treated as blanket approval for external-send, browser-session-material reuse, or paid quota. Those scopes should be described in the mission confirmation text and, when not approved, represented as `awaiting-input` rather than hidden success or generic failure.

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

Use `task_profile` as an object/dict for the classification record, `specialists_mode` for automatic or manual selection mode, `specialists_candidates` for ranked candidates, `specialists_selected` for selected specialist intent, `specialists_unavailable` for missing or unavailable specialists, and `specialists_decision` for the policy outcome. `selection_id` binds a recommendation checkpoint; each invocation has a unique `invocation_id` and carries that identity. A started record is updated in place to its terminal result, preserving crash-visible evidence.

`specialists_selected` and `specialist_invocations` intentionally remain separate. Selection answers "what should be used"; invocation answers "what was actually used or skipped." This keeps ADR-001's audit requirement intact without pretending Codex inline usage is a real forked Skill tool call.

If a specialist appears in `specialist_invocations` but not in `specialists_selected`, report it as `unselected-manual`: evidence was used after the Phase 1 selection checkpoint, but the selection intent was not recorded. This is an observability warning for optional specialists, not a mission failure unless a future strict-mode policy marks that specialist as required.

## Phased Rollout

## Planning provider migration and operator flow

For a planning provider, declare `planning.mode: primary` only with a structured
result contract. `advisory` evidence informs the core planner and is never
execution authority. Legacy `auto_use.min_complexity` remains read-compatible;
new entries use `activation.min_complexity`, and unknown, empty, or conflicting
conditions are rejected.

Run `specialists recommend`, `prepare-invocation`, host-trusted approval,
`invoke-prepared`, `plan-import`, then executor handoff. Confirm stdin digest,
destination, execution context, quota/risk scopes, and ambient-access limits;
a changed digest requires new preflight. Upgrade/resume never migrates an active
legacy session into provider flow: use explicit planning reselection.

1. **Docs-only protocol**: document selection rules and update SKILL.md to mention optional evidence providers.
2. **Manual audit fields**: record `task_profile`, selected specialists, and missing specialists in assumptions/archive notes.
3. **YAML registry parser**: add schema validation and deterministic merge order.
4. **State integration**: add first-class state fields through `mission-state.py`.
5. **Preset tuning**: refine beginner presets from real mission logs.
6. **Strict mode**: optionally let projects require specific specialists for high-risk profiles.
