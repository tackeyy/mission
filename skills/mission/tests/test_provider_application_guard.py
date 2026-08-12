"""Issue #395: application paths must revalidate the current provider contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time



MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


def _prepare_command_provider(run_cli, tmp_path, *, phase="planning", max_calls=None):
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
                        **({"max_calls_per_iteration": max_calls} if max_calls else {}),
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
    (tmp_path / "provider-input.txt").write_text("test provider input\n", encoding="utf-8")
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
        "specialists", "prepare-invocation", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning",
        "--input-file", str(tmp_path / "provider-input.txt"), cwd=tmp_path, env_extra=env,
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
        "specialists", "prepare-invocation", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning",
        "--input-file", str(tmp_path / "provider-input.txt"), cwd=tmp_path, env_extra=env,
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


def test_log_invocation_rejects_legacy_state_without_selection_checkpoint(run_cli, tmp_path):
    run_cli("init", "provider application guard", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("specialists_decision", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "log-invocation", "--iteration", "1", "--phase", "planning",
        "--role", "planning-provider", "--skill", "legacy-provider",
        "--mode", "codex-inline", "--status", "completed", "--selection-source", "manual",
        cwd=tmp_path,
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
        "specialists", "prepare-invocation", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning",
        "--input-file", str(tmp_path / "provider-input.txt"), cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "selection-identity-mismatch" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_invoke_command_consumes_call_limit_before_second_process_spawn(
    run_cli, tmp_path, prepare_approved_invocation
):
    marker, env = _prepare_command_provider(run_cli, tmp_path, max_calls=1)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["specialists_candidates"][0]["max_calls_per_iteration"] == 1
    assert state["specialists_selected"][0]["max_calls_per_iteration"] == 1
    invoke_args, invoke_env, _ = prepare_approved_invocation(
        cwd=tmp_path, provider="guarded-command-provider", iteration=1,
        phase="planning", env_extra=env,
    )
    first = run_cli(*invoke_args, cwd=tmp_path, env_extra=invoke_env)
    assert first.returncode == 0, first.stderr
    marker.unlink()
    before = state_path.read_bytes()

    before = state_path.read_bytes()
    second = run_cli(
        "specialists", "prepare-invocation", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning",
        "--input-file", str(tmp_path / ".test-provider-preflight" / "input.txt"),
        cwd=tmp_path, env_extra=env,
    )

    assert second.returncode == 2
    assert "call-limit-exceeded" in second.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_invoke_command_reresolves_registry_and_rejects_activation_drift_before_spawn(run_cli, tmp_path):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    registry = tmp_path / "provider-registry.json"
    registry_data = json.loads(registry.read_text(encoding="utf-8"))
    registry_data["specialists_v2"][0]["activation"]["min_complexity"] = "Critical"
    registry.write_text(json.dumps(registry_data), encoding="utf-8")
    state_path = _state_path(tmp_path)
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "prepare-invocation", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning",
        "--input-file", str(tmp_path / "provider-input.txt"), cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "provider-ineligible" in result.stderr
    assert "registry" in result.stderr or "activation" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_running_invocation_fences_state_mutation_until_terminal(
    run_cli, tmp_path, prepare_approved_invocation
):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    release = tmp_path / "provider-release"
    command = tmp_path / "commands" / "provider-command"
    command.write_text(
        "#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\n"
        "while [ ! -f \"$PROVIDER_RELEASE\" ]; do sleep 0.02; done\ncat >/dev/null\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    process_env = {
        key: value for key, value in os.environ.items() if not key.startswith("MISSION_")
    }
    process_env.update(env)
    process_env.update({
        "PROVIDER_RELEASE": str(release),
        "MISSION_SESSION_ID": "test",
        "MISSION_LEASE_ID": "test-lease",
    })
    invoke_args, invoke_env, _ = prepare_approved_invocation(
        cwd=tmp_path, provider="guarded-command-provider", iteration=1,
        phase="planning", env_extra=process_env,
    )
    process_env.update(invoke_env)
    process = subprocess.Popen(
        [
            sys.executable, str(MISSION_STATE_PY), *invoke_args,
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=process_env,
    )
    state_path = _state_path(tmp_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("specialist_invocations") and state["specialist_invocations"][0]["status"] == "running":
            break
        time.sleep(0.02)
    else:
        process.kill()
        raise AssertionError("invocation did not publish running state")

    mutation = run_cli("set", "complexity=Critical", cwd=tmp_path, env_extra=env)
    assert mutation.returncode == 2
    assert "provider-invocation-active" in mutation.stderr
    release.touch()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    invocation = state["specialist_invocations"][0]
    assert invocation["status"] == "completed"
    assert invocation["lifecycle_state"] == "terminal"
    assert marker.exists()


def test_reconcile_can_abandon_dead_running_invocation_but_cannot_reapply_it(run_cli, tmp_path):
    _marker, env = _prepare_command_provider(run_cli, tmp_path)
    state_path = _state_path(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    invocation_id = "inv_0123456789abcdef0123456789abcdef"
    state["specialist_invocations"] = [{
        "invocation_id": invocation_id,
        "selection_id": state["specialists_decision"]["selection_id"],
        "iteration": 1,
        "phase": "planning",
        "role": "planning-provider",
        "skill": "guarded-command-provider",
        "mode": "command-provider",
        "provider_kind": "command",
        "status": "running",
        "lifecycle_state": "running",
        "timestamp": "2026-08-12T00:00:00Z",
        "reserved_at": "2026-08-12T00:00:00Z",
        "running_at": "2026-08-12T00:00:01Z",
        "started_at": "2026-08-12T00:00:01Z",
        "transitioned_at": "2026-08-12T00:00:01Z",
        "heartbeat_at": "2026-08-12T00:00:01Z",
        "application_context_digest": "sha256:" + "1" * 64,
        "reservation_owner_session_id": state["owner_session_id"],
        "fencing_epoch": state["fencing_epoch"],
        "child_pid": 99999999,
        "process_identity_digest": "sha256:" + "2" * 64,
    }]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    evidence = tmp_path / "reconcile-evidence.txt"
    evidence.write_text("child result could not be established", encoding="utf-8")

    result = run_cli(
        "specialists", "reconcile-invocation", "--invocation-id", invocation_id,
        "--status", "abandoned-unknown", "--evidence", str(evidence),
        "--expected-fencing-epoch", str(state["fencing_epoch"]),
        cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    terminal = json.loads(state_path.read_text(encoding="utf-8"))["specialist_invocations"][0]
    assert terminal["status"] == "abandoned-unknown"
    assert terminal["lifecycle_state"] == "terminal"
    reapplied = run_cli(
        "specialists", "log-invocation", "--invocation-id", invocation_id,
        "--iteration", "1", "--phase", "planning", "--role", "planning-provider",
        "--skill", "guarded-command-provider", "--mode", "command-provider",
        "--status", "completed", cwd=tmp_path, env_extra=env,
    )
    assert reapplied.returncode == 2
