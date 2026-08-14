"""Issue #479: mission CLI version must stay aligned with distributed manifests."""

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_SRC = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
PLUGIN_MANIFESTS = (
    REPO_ROOT / ".claude-plugin" / "plugin.json",
    REPO_ROOT / ".codex-plugin" / "plugin.json",
    REPO_ROOT / "plugins" / "mission" / ".codex-plugin" / "plugin.json",
)

CURRENT_VERSION = re.search(
    r'^MISSION_CLI_VERSION = "([^"]+)"', CLI_SRC.read_text(encoding="utf-8"), re.M
).group(1)


def _load_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _isolated_env(tmp_path, **extra):
    env = {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }
    env.update(extra)
    return env


def test_cli_version_matches_plugin_manifests():
    for manifest_path in PLUGIN_MANIFESTS:
        assert _load_version(manifest_path) == CURRENT_VERSION, manifest_path


def test_preflight_flags_old_cache_versions_relative_to_cli_constant(state_dir, run_cli, tmp_path):
    fake_claude_home = tmp_path / "fake-claude-home"
    fake_codex_home = tmp_path / "fake-codex-home"
    claude_cache = fake_claude_home / "plugins" / "cache" / "mission-marketplace" / "mission"
    codex_cache = fake_codex_home / "plugins" / "cache" / "mission-marketplace" / "mission"
    (claude_cache / "2.1.0").mkdir(parents=True)
    (codex_cache / "2.3.0").mkdir(parents=True)

    run_cli(
        "init",
        "m",
        "--complexity",
        "Simple",
        "--force-mission",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path),
        check=True,
    )
    result = run_cli(
        "codex-preflight",
        "--json",
        "--hook-config",
        str(tmp_path / "hooks.json"),
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path),
        check=True,
    )
    out = json.loads(result.stdout)

    assert out["version_skew"] is not None
    assert out["version_skew"]["stale_caches"] == {
        "claude-code": ["2.1.0"],
        "codex": ["2.3.0"],
    }

