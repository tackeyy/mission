# Marketplace Release Checklist

Use this checklist before submitting `mission` to Claude Code or Codex marketplaces.

## Shared

- [ ] Repository visibility change is intentional. Do not make the repository public as part of routine validation.
- [ ] `git status --short` contains only intended release changes.
- [ ] No secrets, tokens, private URLs, local-only state, `.mission-state/`, `.pytest_cache/`, or personal machine paths are included.
- [ ] `LICENSE`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and Code of Conduct are present.
- [ ] English and Japanese docs are updated together when user-facing install or release steps change.
- [ ] `python3 -m pytest -q` passes under `skills/mission`.
- [ ] `shellcheck scripts/mission-stop-guard.sh` passes.
- [ ] JSON/YAML metadata parses successfully.

## Claude Code

- [ ] `claude plugin validate /path/to/mission` passes without warnings.
- [ ] Isolated install succeeds:
  - `CLAUDE_CONFIG_DIR="$(mktemp -d)"`
  - `claude plugin marketplace add /path/to/mission`
  - `claude plugin install mission@mission-marketplace`
- [ ] `claude plugin details mission` shows six skills and one `Stop` hook.
- [ ] A real Claude Code session with an active mission state emits a `Stop` hook `decision:block` response.

## Codex

- [ ] Run `scripts/sync-codex-plugin-wrapper.sh` after any `skills/` or `scripts/` change.
- [ ] `codex plugin marketplace add /path/to/mission` succeeds under an isolated `CODEX_HOME`.
- [ ] `codex plugin list` shows `mission@mission-marketplace`.
- [ ] `codex plugin add mission@mission-marketplace` succeeds.
- [ ] Installed cache contains `.codex-plugin/plugin.json`, `skills/mission/SKILL.md`, `skills/mission/bin/mission-state.py`, and `scripts/mission-stop-guard.sh`.
- [ ] Installed cache does not contain `.git`, `.mission-state`, or `.pytest_cache`.
- [ ] `MISSION_PLUGIN_ROOT` and compatibility alias `CLAUDE_PLUGIN_ROOT` are documented for the installed cache path.
- [ ] Codex Stop hook remains documented as opt-in user hook unless Codex plugin-root path handling is verified.

## Final Gate

- [ ] Confirm GitHub repository visibility is still the intended value.
- [ ] Commit and push only after all applicable checks pass.
