"""Regression guards for Codex plugin hook packaging.

Codex attempts to load plugin-bundled ``hooks/hooks.json`` files. Keep the
default mission Codex package skills-only so Claude Code hook metadata cannot be
parsed as Codex hook config by accident.
"""
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

CODEX_PLUGIN_MANIFESTS = [
    REPO_ROOT / ".codex-plugin" / "plugin.json",
    REPO_ROOT / "plugins" / "mission" / ".codex-plugin" / "plugin.json",
]

CODEX_PLUGIN_ROOTS = [
    REPO_ROOT,
    REPO_ROOT / "plugins" / "mission",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_codex_plugin_manifests_do_not_package_hooks():
    for manifest_path in CODEX_PLUGIN_MANIFESTS:
        manifest = _load_json(manifest_path)

        assert "skills" in manifest, manifest_path
        assert "hooks" not in manifest, (
            f"{manifest_path} must remain skills-only by default. "
            "Keep Claude Code hooks under claude-hooks/hooks.json and document "
            "Codex hook setup as opt-in."
        )


def test_codex_plugin_roots_do_not_expose_default_hooks_json():
    for plugin_root in CODEX_PLUGIN_ROOTS:
        hooks_config = plugin_root / "hooks" / "hooks.json"

        assert not hooks_config.exists(), (
            f"{hooks_config} would be auto-loaded by Codex. "
            "Use claude-hooks/hooks.json for Claude Code-only hook packaging."
        )


def test_claude_hook_config_stays_in_claude_only_directory():
    claude_hooks = REPO_ROOT / "claude-hooks" / "hooks.json"
    manifest = _load_json(REPO_ROOT / ".claude-plugin" / "plugin.json")

    assert claude_hooks.exists()
    assert manifest.get("hooks") == "./claude-hooks/hooks.json"
