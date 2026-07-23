"""Issue #186: cli_version の state 記録とバージョン skew 警告。

plugin cache 経由の起動 (Claude Code plugin cache 1.0.6 のまま、Codex cache 1.1.1 のまま) が
Wave 1 の修正を反映しない古い SKILL.md で走り続け、checkpoint 欠落・review_tier 未記録・
findings 欠落の主因になっていた実害への対策。

全テストで MISSION_CLAUDE_HOME / CODEX_HOME を隔離 tmp_path に固定する
(実行マシンの実際の ~/.claude, ~/.codex plugin cache が非決定性を持ち込むため必須)。
"""
import json
import re
from pathlib import Path

# リリースのたびにテストのリテラルを書き換えずに済むよう、CLI から現行 version を読む。
_CLI_SRC = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
CURRENT_VERSION = re.search(
    r'^MISSION_CLI_VERSION = "([^"]+)"', _CLI_SRC.read_text(encoding="utf-8"), re.M
).group(1)


def _isolated_env(tmp_path, **extra):
    env = {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }
    env.update(extra)
    return env


def test_init_records_cli_version(state_dir, run_cli, read_state, tmp_path):
    run_cli("init", "cli version mission", "--complexity", "Simple", "--force-mission", cwd=tmp_path,
            env_extra=_isolated_env(tmp_path), check=True)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["cli_version"], "cli_version が記録されていない"
    assert isinstance(state["cli_version"], str)


def test_preflight_no_skew_when_caches_absent(state_dir, run_cli, tmp_path):
    run_cli("init", "m", "--complexity", "Simple", "--force-mission", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    r = run_cli("codex-preflight", "--json", "--hook-config", str(tmp_path / "hooks.json"),
                cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    out = json.loads(r.stdout)
    assert out["version_skew"] is None
    assert not any("plugin cache" in w for w in out["warnings"])


def test_preflight_detects_stale_claude_code_cache(state_dir, run_cli, tmp_path):
    fake_home = tmp_path / "fake-claude-home"
    cache = fake_home / "plugins" / "cache" / "mission-marketplace" / "mission"
    (cache / "1.0.6").mkdir(parents=True)
    (cache / CURRENT_VERSION).mkdir(parents=True)  # 現行と同じバージョンは stale 扱いしない

    run_cli("init", "m", "--complexity", "Simple", "--force-mission", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    r = run_cli("codex-preflight", "--json", "--hook-config", str(tmp_path / "hooks.json"),
                cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    out = json.loads(r.stdout)

    assert out["version_skew"] is not None
    assert out["version_skew"]["stale_caches"] == {"claude-code": ["1.0.6"]}
    assert any("plugin cache" in w for w in out["warnings"])


def test_preflight_detects_stale_codex_cache(state_dir, run_cli, tmp_path):
    fake_codex = tmp_path / "fake-codex-home"
    cache = fake_codex / "plugins" / "cache" / "mission-marketplace" / "mission"
    (cache / "1.1.1").mkdir(parents=True)

    run_cli("init", "m", "--complexity", "Simple", "--force-mission", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    r = run_cli("codex-preflight", "--json", "--hook-config", str(tmp_path / "hooks.json"),
                cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    out = json.loads(r.stdout)

    assert out["version_skew"]["stale_caches"] == {"codex": ["1.1.1"]}


def test_preflight_does_not_flag_newer_or_equal_cache_version(state_dir, run_cli, tmp_path):
    """将来バージョンや同一バージョンの cache は stale 扱いしない (壊れたディレクトリ名等の誤検知防止)."""
    fake_home = tmp_path / "fake-claude-home"
    cache = fake_home / "plugins" / "cache" / "mission-marketplace" / "mission"
    (cache / "9.9.9").mkdir(parents=True)

    run_cli("init", "m", "--complexity", "Simple", "--force-mission", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    r = run_cli("codex-preflight", "--json", "--hook-config", str(tmp_path / "hooks.json"),
                cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    out = json.loads(r.stdout)
    assert out["version_skew"] is None


def test_resume_output_includes_version_skew(state_dir, run_cli, tmp_path):
    fake_home = tmp_path / "fake-claude-home"
    cache = fake_home / "plugins" / "cache" / "mission-marketplace" / "mission"
    (cache / "1.0.6").mkdir(parents=True)

    run_cli("init", "m", "--complexity", "Simple", "--force-mission", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    r = run_cli("resume", cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True)
    out = json.loads(r.stdout)
    assert out["resume"]["version_skew"]["stale_caches"] == {"claude-code": ["1.0.6"]}


def test_stats_reports_by_cli_version(tmp_path, run_cli):
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    (sd / "v1.json").write_text(json.dumps({
        "mission": "m1", "mission_id": "a1", "session_id": "v1",
        "loop_active": False, "passes": True, "halt_reason": "",
        "cli_version": CURRENT_VERSION, "phase": "done", "score_history": [], "iteration": 1,
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:10:00Z",
        "schema_version": 2, "project_root": str(tmp_path),
        "pid": 0, "hostname": "test", "created_at_session": "2026-05-25T00:00:00Z",
    }))
    (sd / "v2.json").write_text(json.dumps({
        "mission": "m2", "mission_id": "a2", "session_id": "v2",
        "loop_active": False, "passes": False, "halt_reason": "",
        "phase": "planning", "score_history": [], "iteration": 1,
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:10:00Z",
        "schema_version": 2, "project_root": str(tmp_path),
        "pid": 0, "hostname": "test", "created_at_session": "2026-05-25T00:00:00Z",
    }))
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True)
    data = json.loads(r.stdout)
    assert data["by_cli_version"][CURRENT_VERSION]["total"] == 1
    assert data["by_cli_version"]["unknown"]["total"] == 1

    r_text = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path, check=True)
    assert "by_cli_version:" in r_text.stdout
    assert CURRENT_VERSION in r_text.stdout
