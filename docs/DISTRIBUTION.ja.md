# 配布戦略

**日本語** | [English](DISTRIBUTION.md)

## 推奨

Claude Code 用と Codex 用で repo は分けず、単一 repo のまま維持します。

共有資産は `skills/mission` と 5 つの supporting skills です。Claude Code と Codex の違いは主に packaging metadata、marketplace の形式、hook trust/path behavior です。この差分は platform-specific manifest として同一 source tree に置く方が保守しやすいです。

repo を分けるべきなのは、runtime behavior が大きく分岐した場合です。たとえば片方だけ skill instructions、state semantics、hook behavior が互換不能になる場合です。

## 現在の packaging

| Platform | Files | Default behavior |
|---|---|---|
| Claude Code | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `claude-hooks/hooks.json` | Skills + Stop hook |
| Codex | `plugins/mission/.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Skills only |

Codex package は default では skills-only です。Codex は plugin-bundled hooks を読み込めますが、non-managed hooks は実行前に review/trust が必要です。また、Claude Code の `${CLAUDE_PLUGIN_ROOT}` に相当する plugin-root 環境変数が Codex manual では確認できません。Stop hook は `skills/mission/refs/codex-setup.md` の opt-in advanced setup として残します。

## Marketplace への含意

Claude Code:

- Plugin manifest は `.claude-plugin/` に置きます。
- Marketplace distribution は GitHub repository から配布できます。
- Claude Code は plugin-local hook / MCP path 用に `${CLAUDE_PLUGIN_ROOT}` を document しているため、Stop hook を直接 package できます。

Codex:

- 直接 plugin metadata は `.codex-plugin/` に置きます。marketplace install は `plugins/mission/.codex-plugin/plugin.json` を使います。
- Marketplace entry は `.agents/plugins/marketplace.json` に置けます。
- `skills/` または `scripts/` を変えた後は `scripts/sync-codex-plugin-wrapper.sh` で `plugins/mission/` を同期します。
- marketplace install 後は `MISSION_PLUGIN_ROOT` を install cache path に設定し、command text の依存がなくなるまでは `CLAUDE_PLUGIN_ROOT` を互換 alias として同じ値にします。
- Skills が安定した共有単位です。
- plugin-root path handling をこの plugin で十分に検証するまでは、hook packaging は conservative に扱います。

## Release checklist

- `skills/` は shared かつ platform-neutral に保つ
- Claude-only hook config を default `hooks/hooks.json` に置かず、Codex が自動 load しないようにする
- Claude Code plugin install を `/plugin marketplace add` と `/plugin install` で検証する
- Codex は symlink install と `codex plugin marketplace add` + `codex plugin add` を別々に検証する
- repo public 化または private Actions billing 解決後に GitHub Actions の automatic trigger を戻す
