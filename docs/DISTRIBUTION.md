# Distribution Strategy

[Japanese](DISTRIBUTION.ja.md) | **English**

## Recommendation

Keep Claude Code and Codex support in one repository.

The shared asset is the skill bundle itself: `skills/mission` plus the five
supporting skills. Claude Code and Codex differ mainly in packaging metadata,
marketplace shape, and hook trust/path behavior. Those differences are small
enough to maintain as platform-specific manifests in the same source tree.

Split repositories only if the runtime behavior diverges substantially, for
example if one platform needs different skill instructions, different state
semantics, or incompatible hook behavior.

## Current Packaging

| Platform | Files | Default behavior |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `claude-hooks/hooks.json` | Skills plus Stop hook |
| Codex | `plugins/mission/.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Skills only |

The Codex package is skills-only by default because Codex can load plugin-bundled
hooks, but non-managed hooks must be reviewed and trusted before they run, and
Codex does not document a plugin-root environment variable equivalent to Claude
Code's `${CLAUDE_PLUGIN_ROOT}`. The Stop hook remains available as an opt-in
advanced setup in `skills/mission/refs/codex-setup.md`.

## Marketplace Implications

Claude Code:

- Plugin manifests live under `.claude-plugin/`.
- Marketplace distribution can be hosted from a GitHub repository.
- Claude Code documents `${CLAUDE_PLUGIN_ROOT}` for plugin-local hook and MCP
  paths, so the Stop hook can be packaged directly.

Codex:

- Direct plugin metadata lives under `.codex-plugin/`; marketplace installs use
  the copied wrapper at `plugins/mission/.codex-plugin/plugin.json`.
- Marketplace entries can live under `.agents/plugins/marketplace.json`.
- Keep `plugins/mission/` synced with `scripts/sync-codex-plugin-wrapper.sh` after
  changing `skills/` or `scripts/`.
- After marketplace install, set `MISSION_PLUGIN_ROOT` to the installed cache path
  and keep `CLAUDE_PLUGIN_ROOT` as a compatibility alias until the command text
  no longer depends on it.
- Skills are the stable shared unit.
- Hook packaging should stay conservative until plugin-root path handling is
  fully verified for this plugin.

## Release Checklist

- Keep `skills/` shared and platform-neutral.
- Keep Claude-only hook configuration out of the default `hooks/hooks.json`
  location so Codex does not auto-load it.
- Validate Claude Code plugin install through `/plugin marketplace add` and
  `/plugin install`.
- Validate Codex skills through symlink install and, separately, `codex plugin
  marketplace add` + `codex plugin add`.
- Re-enable automatic GitHub Actions triggers after the repository is public or
  private Actions billing is available.
