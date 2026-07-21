# Testing Guide

[Japanese](TESTING.ja.md) | **English**

This repository uses focused tests for the state manager and Stop hook. The most
important invariant is that the orchestrator cannot mark a mission as complete
unless the score history satisfies the threshold gate.

## Test Commands

Run all Python tests:

```bash
cd skills/mission
python3 -m pytest -q
```

Run a specific test file:

```bash
cd skills/mission
python3 -m pytest -q tests/test_mark_passes_threshold.py
```

Run shell linting:

```bash
shellcheck scripts/mission-stop-guard.sh scripts/sync-codex-plugin-wrapper.sh scripts/mission-local-authoring-sync.sh
```

## Test Layout

| Path | Coverage |
|---|---|
| `skills/mission/tests/test_mark_passes_threshold.py` | Passing gate and force override behavior |
| `skills/mission/tests/test_push_score.py` | Score normalization and score history writes |
| `skills/mission/tests/test_session_routing.py` | Session file routing |
| `skills/mission/tests/test_session_lifecycle.py` | State lifecycle transitions |
| `skills/mission/tests/test_stop_hook.py` | Stop hook blocking behavior |
| `skills/mission/tests/test_cleanup_stale.py` | Stale and orphan state cleanup |
| `skills/mission/tests/test_local_authoring_sync.py` | Latest-remote-main local authoring bootstrap and fail-closed checkout protection |
| `skills/mission/tests/test_doc_consistency.py` | Documentation and command consistency |

## What to Test

Add tests when changing:

- `mission-state.py` commands, schema fields, or session routing
- Stop hook ownership checks or blocking conditions
- Scoring item normalization or threshold logic
- Multi-session behavior for Claude Code or Codex
- Documentation that names commands, paths, or required fields

## Local E2E Checks

### Claude Code

For Claude Code plugin install behavior, use an isolated Claude config directory so your
active setup is not modified:

```bash
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
```

Then install the plugin through the local marketplace:

```text
/plugin marketplace add /absolute/path/to/mission
/plugin install mission@mission-marketplace
```

Verify:

- `claude plugin details mission` lists the six skills
- The Stop hook is registered
- A minimal `/mission` run creates `.mission-state/sessions/*.json`
- Subskills such as `mission-reviewer` resolve without a namespace prefix

Do not commit `.mission-state/` output.

### Codex

For Codex marketplace install behavior, use an isolated `CODEX_HOME`:

```bash
export CODEX_HOME="$(mktemp -d)"
codex plugin marketplace add /absolute/path/to/mission
codex plugin list
codex plugin add mission@mission-marketplace
```

Verify:

- `codex plugin list` shows `mission@mission-marketplace` as installed and enabled
- The installed cache contains `.codex-plugin/plugin.json`, `skills/mission/SKILL.md`, and `scripts/mission-stop-guard.sh`
- The installed cache does not contain `.git`, `.mission-state`, or `.pytest_cache`

Run `scripts/sync-codex-plugin-wrapper.sh` before this check if `skills/` or
`scripts/` changed.
