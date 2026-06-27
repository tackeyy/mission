# Marketplace Release Checklist

Use this checklist before submitting `mission` to Claude Code or Codex marketplaces.

Before starting, confirm this is a **distribution release**, not an ordinary
merge release. See [`VERSIONING.md`](VERSIONING.md). Do not bump versions for
every merged PR.

## Shared

- [ ] This release intentionally publishes a new plugin version; ordinary PR merges should leave versions unchanged and keep entries under `[Unreleased]`.
- [ ] Repository visibility change is intentional. Do not make the repository public as part of routine validation.
- [ ] `git status --short` contains only intended release changes.
- [ ] No secrets, tokens, private URLs, local-only state, `.mission-state/`, `.pytest_cache/`, or personal machine paths are included.
- [ ] `LICENSE`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, and Code of Conduct are present.
- [ ] English and Japanese docs are updated together when user-facing install or release steps change.
- [ ] Version numbers are bumped together in `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `plugins/mission/.codex-plugin/plugin.json`, README install paths, and Codex setup docs.
- [ ] `CHANGELOG.md` and `CHANGELOG.ja.md` contain the release entry and links for the new version.
- [ ] Compare `git log <previous-tag>..HEAD --oneline` with the new changelog entry, and confirm every user-facing `feat:` / `fix:` / audit or release-process change is represented in both English and Japanese.
- [ ] Create and push the new `vX.Y.Z` git tag for the exact release commit.
- [ ] Create or update the GitHub Release for the new `vX.Y.Z` tag before reporting release completion.
- [ ] Re-verify publication with `git ls-remote --tags origin vX.Y.Z` and `gh release view vX.Y.Z --repo tackeyy/mission`; do not report a distribution release as complete until both commands confirm the new version.
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
