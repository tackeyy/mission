"""Issue #395: application paths must revalidate the current provider contract."""

import json
import os


def _prepare_command_provider(run_cli, tmp_path, *, phase="planning"):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    marker = tmp_path / "provider-ran"
    command = command_dir / "provider-command"
    command.write_text(
        "#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\ncat >/dev/null\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    registry = tmp_path / "provider-registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "guarded-command-provider",
                        "role": "planning-provider",
                        "skill": "guarded-command-provider",
                        "kind": "command",
                        "command": "provider-command",
                        "args": [],
                        "env": {},
                        "task_profiles": ["architecture"],
                        "phases": [phase],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        "PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "PROVIDER_MARKER": str(marker),
    }
    run_cli(
        "init", "provider application guard", "--complexity", "Complex",
        cwd=tmp_path, check=True, env_extra=env,
    )
    run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--record-state", cwd=tmp_path,
        check=True, env_extra=env,
    )
    return marker, env


def _state_path(root):
    return root / ".mission-state" / "sessions" / "test.json"


def test_invoke_command_rejects_below_current_complexity_before_process_spawn(run_cli, tmp_path):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["complexity"] = "Standard"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "provider-ineligible" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_invoke_command_rejects_requested_phase_that_disagrees_with_current_state(run_cli, tmp_path):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "executing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "provider-ineligible" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_log_invocation_cannot_create_selection_metadata_for_unselected_provider(run_cli, tmp_path):
    run_cli("init", "provider application guard", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_path = _state_path(tmp_path)
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "log-invocation", "--iteration", "1", "--phase", "planning",
        "--role", "planning-provider", "--skill", "unselected-provider",
        "--mode", "codex-inline", "--status", "completed",
        "--selection-source", "manual", cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "provider-ineligible" in result.stderr
    assert state_path.read_bytes() == before


def test_invoke_command_rejects_stale_selection_identity_without_process_spawn(run_cli, tmp_path):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["specialists_selected"][0]["selection_id"] = "sel_ffffffffffffffffffffffffffffffff"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "selection-identity-mismatch" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_invoke_command_consumes_call_limit_before_second_process_spawn(run_cli, tmp_path):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["specialists_candidates"][0]["max_calls_per_iteration"] = 1
    state["specialists_selected"][0]["max_calls_per_iteration"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    first = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )
    assert first.returncode == 0, first.stderr
    marker.unlink()
    before = state_path.read_bytes()

    second = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )

    assert second.returncode == 2
    assert "call-limit-exceeded" in second.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before
