"""Issue #355: adaptive routing goal dispatch provider."""

import importlib.util
import json
from pathlib import Path

import pytest


MISSION_STATE_PATH = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("mission_state_issue355", MISSION_STATE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(root: Path, mode: str) -> None:
    path = root / ".mission" / "routing.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"version: 1\ngoal_dispatch: {mode}\n", encoding="utf-8")


def _isolated_env(tmp_path: Path, **extra) -> dict[str, str | None]:
    env = {
        "HOME": str(tmp_path / "home"),
        "MISSION_SESSION_ID": "test",
        "CLAUDECODE": None,
        "CLAUDE_CODE_SESSION_ID": None,
        "CODEX_THREAD_ID": None,
    }
    env.update(extra)
    return env


def _state(tmp_path: Path) -> dict:
    path = tmp_path / ".mission-state" / "sessions" / "test.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_config_preserves_inline_dispatch(run_cli, tmp_path):
    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "Stop Condition" in verdict["guidance"]


def test_project_host_native_dispatches_to_claude_code_goal(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CLAUDE_CODE_SESSION_ID="cc-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "host-native"
    assert "/goal" in verdict["guidance"]


def test_project_host_native_dispatches_to_codex_goal_mode(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "host-native"
    assert "goal mode" in verdict["guidance"]


def test_unknown_host_falls_back_inline_with_reason(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert verdict["goal_dispatch_fallback_reason"] == "host-native unavailable: host detection returned unknown"
    assert "Stop Condition" in verdict["guidance"]


def test_invalid_project_value_warns_and_fails_safe_inline(run_cli, tmp_path):
    _write_config(tmp_path, "chatgpt")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path, env_extra=_isolated_env(tmp_path), check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "invalid goal_dispatch 'chatgpt'" in result.stderr
    assert "invalid goal_dispatch 'chatgpt'" in verdict["goal_dispatch_fallback_reason"]


def test_invalid_utf8_project_config_warns_and_fails_safe_inline(run_cli, tmp_path):
    path = tmp_path / ".mission" / "routing.yml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"version: 1\ngoal_dispatch: \xff\n")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path, env_extra=_isolated_env(tmp_path),
    )

    assert result.returncode == 0
    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "routing config unreadable" in result.stderr
    assert "UnicodeDecodeError" in verdict["goal_dispatch_fallback_reason"]


def test_symlinked_project_config_warns_and_fails_safe_inline(run_cli, tmp_path):
    outside = tmp_path / "outside-routing.yml"
    outside.write_text("version: 1\ngoal_dispatch: host-native\n", encoding="utf-8")
    path = tmp_path / ".mission" / "routing.yml"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "symlink" in result.stderr
    assert "symlink" in verdict["goal_dispatch_fallback_reason"]


def test_project_config_escaping_project_root_warns_and_fails_safe_inline(run_cli, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "routing.yml").write_text(
        "version: 1\ngoal_dispatch: host-native\n", encoding="utf-8",
    )
    (project / ".mission").symlink_to(outside, target_is_directory=True)

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=project,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "escapes project root" in result.stderr
    assert "escapes project root" in verdict["goal_dispatch_fallback_reason"]


def test_duplicate_project_config_key_warns_and_fails_safe_inline(run_cli, tmp_path):
    path = tmp_path / ".mission" / "routing.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "version: 1\ngoal_dispatch: inline\ngoal_dispatch: host-native\n",
        encoding="utf-8",
    )

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "duplicate routing config key 'goal_dispatch'" in result.stderr
    assert "duplicate routing config key 'goal_dispatch'" in verdict["goal_dispatch_fallback_reason"]


def test_project_config_overrides_user_config(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")
    user_config = tmp_path / "home" / ".config" / "mission" / "routing.yml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("version: 1\ngoal_dispatch: inline\n", encoding="utf-8")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "project:.mission/routing.yml"
    assert verdict["goal_dispatch_effective"] == "host-native"


def test_cli_flag_overrides_project_config(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")

    result = run_cli(
        "init", "typo を直す", "--complexity", "Simple",
        "--goal-dispatch", "inline",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "cli:--goal-dispatch"
    assert verdict["goal_dispatch_effective"] == "inline"


def test_mission_explicit_dispatch_overrides_cli_flag(run_cli, tmp_path):
    result = run_cli(
        "init", "goal_dispatch: host-native; typo を直す", "--complexity", "Simple",
        "--goal-dispatch", "inline",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "mission:user-explicit"
    assert verdict["goal_dispatch_effective"] == "host-native"


def test_standalone_dispatch_on_later_line_overrides_cli_flag(run_cli, tmp_path):
    result = run_cli(
        "init", "typo を直す\n\ngoal_dispatch: host-native\n受入テストを実行する",
        "--complexity", "Simple", "--goal-dispatch", "inline",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "mission:user-explicit"
    assert verdict["goal_dispatch_effective"] == "host-native"


def test_conflicting_standalone_dispatch_directives_warn_and_fail_safe_inline(run_cli, tmp_path):
    result = run_cli(
        "init", "goal_dispatch: host-native\ntypo を直す\ngoal_dispatch: inline",
        "--complexity", "Simple", "--goal-dispatch", "host-native",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "mission:user-explicit"
    assert verdict["goal_dispatch_effective"] == "inline"
    assert "conflicting goal_dispatch directives" in result.stderr
    assert "host-native, inline" in verdict["goal_dispatch_fallback_reason"]


@pytest.mark.parametrize("mission", [
    "例: goal_dispatch: host-native を指定できます",
    "goal_dispatch: host-native にしないで typo を直す",
    "goal_dispatch: host-native ではなく inline にする",
    '"goal_dispatch: host-native" は設定例です',
    "> goal_dispatch: host-native",
    "```text\ngoal_dispatch: host-native\n```\ntypo を直す",
])
def test_mission_mentions_do_not_override_cli_dispatch(run_cli, tmp_path, mission):
    result = run_cli(
        "init", mission, "--complexity", "Simple", "--goal-dispatch", "inline",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["goal_dispatch_source"] == "cli:--goal-dispatch"
    assert verdict["goal_dispatch_effective"] == "inline"


def test_set_simple_records_effective_dispatch_in_routed_halt(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")
    base_env = _isolated_env(tmp_path, MISSION_SESSION_ID="test")
    run_cli("init", "typo を直す", cwd=tmp_path, env_extra=base_env, check=True)

    result = run_cli(
        "set", "complexity=Simple", cwd=tmp_path,
        env_extra={**base_env, "CODEX_THREAD_ID": "codex-test"}, check=True,
    )

    verdict = json.loads(result.stdout)
    state = _state(tmp_path)
    assert verdict["goal_dispatch_effective"] == "host-native"
    assert "goal mode" in verdict["guidance"]
    assert state["halt_category"] == "routed-goal"
    assert state["goal_dispatch_effective"] == "host-native"
    assert "goal_dispatch_fallback_reason" not in state


def test_next_route_to_goal_reports_effective_dispatch(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")
    base_env = _isolated_env(tmp_path, MISSION_SESSION_ID="test")
    run_cli("init", "typo を直す", cwd=tmp_path, env_extra=base_env, check=True)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complexity"] = "Simple"
    state["review_tier"] = "light"
    state["reviewer_count"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "next", cwd=tmp_path,
        env_extra={**base_env, "CLAUDE_CODE_SESSION_ID": "cc-test"}, check=True,
    )

    verdict = json.loads(result.stdout)
    assert verdict["next_action"] == "route-to-goal"
    assert verdict["details"]["goal_dispatch_effective"] == "host-native"
    assert "/goal" in verdict["summary"]

    run_cli(
        "mark-halt", "--reason", "routed-to-goal (#325)", "--category", "routed-goal",
        cwd=tmp_path,
        env_extra={**base_env, "CLAUDE_CODE_SESSION_ID": "cc-test"}, check=True,
    )
    halted = _state(tmp_path)
    assert halted["goal_dispatch_effective"] == "host-native"
    assert halted["goal_dispatch_host"] == "claude-code"
    assert "goal_dispatch_fallback_reason" not in halted


def test_force_mission_remains_authoritative_with_host_native(run_cli, tmp_path):
    _write_config(tmp_path, "host-native")

    run_cli(
        "init", "typo を直す", "--complexity", "Simple", "--force-mission",
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path, CODEX_THREAD_ID="codex-test"),
        check=True,
    )

    assert _state(tmp_path)["loop_active"] is True


def test_detect_host_uses_native_identity_environment(monkeypatch):
    module = _load_mission_state()
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)
    assert module.detect_host() == "unknown"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert module.detect_host() == "claude-code"
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setenv("CODEX_THREAD_ID", "thread")
    assert module.detect_host() == "codex"
