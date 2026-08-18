"""Issue #550 C2 Stage B Batch 2 real-process repository coverage.

specialists recommend / log-invocation / verify-approval / prepare-invocation /
invoke-command / invoke-prepared / reconcile-invocation / plan-import の
repository 移行テスト。実 CLI・別プロセスで検証。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _env(session_id: str, *, operation_id: str | None = None) -> dict[str, str]:
    environment = {
        "MISSION_SESSION_ID": session_id,
        "MISSION_LEASE_ID": session_id + "-lease",
    }
    if operation_id is not None:
        environment["MISSION_OPERATION_ID"] = operation_id
    return environment


def _head(root: Path, session_id: str) -> dict:
    return json.loads(
        (root / ".mission-state" / "sessions" / (session_id + ".json")).read_text(
            encoding="utf-8"
        )
    )


def _public_state(run_cli, root: Path, session_id: str) -> dict:
    result = run_cli("get", cwd=root, env_extra=_env(session_id))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _init_v5(run_cli, root: Path, session_id: str) -> None:
    result = run_cli(
        "init",
        "C2 Stage B Batch 2",
        "--complexity",
        "Complex",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=root,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr


def _make_registry(root: Path, *, provider_id: str = "batch2-provider") -> Path:
    """Create a minimal registry with a command provider."""
    registry = root / f"registry-{provider_id}.json"
    cmd_dir = root / "commands"
    cmd_dir.mkdir(exist_ok=True)
    cmd = cmd_dir / f"{provider_id}-command"
    cmd.write_text("#!/bin/sh\ncat >/dev/null\necho '{}'\n", encoding="utf-8")
    cmd.chmod(0o700)
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": provider_id,
                        "role": "planning-provider",
                        "skill": provider_id,
                        "kind": "command",
                        "command": f"{provider_id}-command",
                        "args": [],
                        "env": {},
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
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
    # Resolve symlinks so _portable_registry_identity maps to $PROJECT not $EXTERNAL.
    # On macOS tmp_path uses /var/folders (symlink) but conftest runs cwd via .resolve()
    # which gives /private/var/folders; an unresolved registry path falls outside cwd
    # and gets classified as external-registry-resupply.
    return registry.resolve()


def _recommend(
    run_cli,
    root: Path,
    session_id: str,
    registry: Path,
    *,
    operation_id: str | None = None,
    provider_id: str = "batch2-provider",
) -> None:
    cmd_dir = root / "commands"
    # recommend --record-state requires MISSION_OPERATION_ID for v5; use a
    # stable default so multiple helper calls in the same session are replays.
    env = _env(session_id, operation_id=operation_id or "recommend-setup")
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review architecture",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--record-state",
        cwd=root,
        env_extra=env,
    )
    assert result.returncode == 0, result.stderr


def _make_counting_command(root: Path, provider_id: str) -> tuple[Path, Path]:
    """Create a command that counts invocations via a marker file."""
    cmd_dir = root / "commands"
    cmd_dir.mkdir(exist_ok=True)
    marker = root / f"{provider_id}-marker"
    cmd = cmd_dir / f"{provider_id}-command"
    # Write count to marker file on each invocation
    cmd.write_text(
        "#!/bin/sh\n"
        f"count=$(cat '{marker}' 2>/dev/null || echo 0)\n"
        f"echo $((count+1)) > '{marker}'\n"
        "cat >/dev/null\n"
        "echo '{}'\n",
        encoding="utf-8",
    )
    cmd.chmod(0o700)
    return cmd_dir, marker


def _make_approval_verifier(root: Path, provider_id: str) -> dict:
    """Create a test approval verifier and return env variables."""
    provider_root = root / f".test-provider-{provider_id}"
    provider_root.mkdir(exist_ok=True)
    source = provider_root / "test_approval_provider.py"
    source.write_text(
        "import hashlib\n"
        "def verify(request):\n"
        " nonce=hashlib.sha256(request['preflight_id'].encode()).hexdigest()[:32]\n"
        " return {**request,'schema':'approval-evidence/1','issuer_id':'test-host-event',"
        "'verifier_id':'test-verifier','verifier_version':'1.0','actor_kind':'human',"
        "'actor_id':'actor:test','proof_kind':'opaque-host-event',"
        "'proof_digest':'sha256:'+'f'*64,'expires_at':'2099-01-01T00:00:00Z',"
        "'single_use_nonce':nonce}\n",
        encoding="utf-8",
    )
    dist = provider_root / "test_approval_provider-1.0.dist-info"
    dist.mkdir(exist_ok=True)
    (dist / "METADATA").write_text(
        "Name: test-approval-provider\nVersion: 1.0\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(
        "[mission.approval_verifiers]\ntest-entry = test_approval_provider:verify\n",
        encoding="utf-8",
    )
    config = root / ".test-host-config" / "mission"
    config.mkdir(parents=True, exist_ok=True)
    (config / "approval-verifiers.json").write_text(
        json.dumps(
            {
                "schema": "mission-approval-verifier-registry/2",
                "verifiers": [
                    {
                        "id": "test-verifier",
                        "entry_point": "test-entry",
                        "distribution": "test-approval-provider",
                        "version": "1.0",
                        "source_digest": "sha256:"
                        + hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    inherited = os.environ.get("PYTHONPATH")
    return {
        "PYTHONPATH": str(provider_root) + (os.pathsep + inherited if inherited else ""),
        "XDG_CONFIG_HOME": str(config.parent),
    }


def _v5_patch_session_state(root: Path, session_id: str, patcher: Callable[[dict], None]) -> None:
    """Patch v5 session state in-process via the V5CompatibilityRepository API.

    conftest.py already adds the mission lib to sys.path, so importing
    mission_persistence works directly inside the test process.
    """
    from mission_persistence.fenced_commit import LocalFencedRepository  # type: ignore[import]
    from mission_persistence.legacy_v4 import V5CompatibilityRepository  # type: ignore[import]

    ms = root / ".mission-state"
    lfr = LocalFencedRepository(ms, lease_ttl_seconds=3600)
    compat = V5CompatibilityRepository(
        repository=lfr,
        session_id=session_id,
        lease_owner_session_id=session_id,
        # The MISSION_LEASE_ID convention in _env() is session_id + "-lease"
        presented_lease_id=session_id + "-lease",
        # Both None → random compat operation_id per call (non-idempotent, fine for setup)
    )
    with compat.transaction():
        data = compat.load()
        patcher(data)
        compat.save(data)


# ---------------------------------------------------------------------------
# tests: specialists recommend (record-state)
# ---------------------------------------------------------------------------


def test_specialists_recommend_v5_preserves_head_and_replays(run_cli, tmp_path):
    session_id = "batch2-recommend"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="recommend-provider")
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    cmd_dir = tmp_path / "commands"
    env = _env(session_id, operation_id="recommend-op-1")
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    first = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review architecture",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--record-state",
        cwd=tmp_path,
        env_extra=env,
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"

    # Replay with same operation_id must not advance the head
    replay = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review architecture",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--record-state",
        cwd=tmp_path,
        env_extra=env,
    )
    assert replay.returncode == 0, replay.stderr
    assert _head(tmp_path, session_id) == committed

    # Surrounding commands still work
    state = _public_state(run_cli, tmp_path, session_id)
    assert "specialists_selected" in state
    updated = run_cli(
        "set", "batch2_recommend_probe=true", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert updated.returncode == 0, updated.stderr
    assert _public_state(run_cli, tmp_path, session_id)["batch2_recommend_probe"] is True


def test_specialists_recommend_v5_requires_operation_id(run_cli, tmp_path):
    session_id = "batch2-recommend-noid"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="recommend-noid-provider")
    before = _head(tmp_path, session_id)

    cmd_dir = tmp_path / "commands"
    env = _env(session_id)  # no operation_id
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review architecture",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--record-state",
        cwd=tmp_path,
        env_extra=env,
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_specialists_recommend_retained_v4_unchanged(legacy_run_cli, tmp_path):
    session_id = "batch2-recommend-v4"
    result = legacy_run_cli(
        "init",
        "retained v4 recommend",
        "--complexity",
        "Complex",
        "--force-mission",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr
    registry = _make_registry(tmp_path, provider_id="recommend-v4-provider")

    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    result = legacy_run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Review architecture",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--record-state",
        cwd=tmp_path,
        env_extra=env,
    )
    assert result.returncode == 0, result.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / (session_id + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "schema" not in state
    assert "specialists_selected" in state


# ---------------------------------------------------------------------------
# tests: specialists log-invocation
# ---------------------------------------------------------------------------


def test_specialists_log_invocation_v5_preserves_head_and_replays(run_cli, tmp_path):
    session_id = "batch2-loginv"
    _init_v5(run_cli, tmp_path, session_id)
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    first = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "batch2-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="loginv-op-1"),
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"

    replay = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "batch2-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="loginv-op-1"),
    )
    assert replay.returncode == 0, replay.stderr
    assert _head(tmp_path, session_id) == committed

    state = _public_state(run_cli, tmp_path, session_id)
    invocations = state.get("specialist_invocations") or []
    assert any(inv.get("skill") == "batch2-skill" for inv in invocations)


def test_specialists_log_invocation_v5_requires_operation_id(run_cli, tmp_path):
    session_id = "batch2-loginv-noid"
    _init_v5(run_cli, tmp_path, session_id)
    before = _head(tmp_path, session_id)

    result = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "batch2-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_specialists_log_invocation_intent_collision_rejected(run_cli, tmp_path):
    session_id = "batch2-loginv-collision"
    _init_v5(run_cli, tmp_path, session_id)

    first = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "batch2-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="loginv-collision-id"),
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)

    # Same operation_id but different intent (different skill)
    collision = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "different-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="loginv-collision-id"),
    )
    assert collision.returncode == 2
    assert "operation ID has a different intent" in collision.stderr
    assert _head(tmp_path, session_id) == committed


def test_specialists_log_invocation_retained_v4_unchanged(legacy_run_cli, tmp_path):
    session_id = "batch2-loginv-v4"
    result = legacy_run_cli(
        "init",
        "retained v4 log-invocation",
        "--complexity",
        "Standard",
        "--force-mission",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr

    result = legacy_run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "v4-batch2-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert result.returncode == 0, result.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / (session_id + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "schema" not in state
    invocations = state.get("specialist_invocations") or []
    assert any(inv.get("skill") == "v4-batch2-skill" for inv in invocations)


# ---------------------------------------------------------------------------
# tests: specialists verify-approval
# ---------------------------------------------------------------------------


def test_specialists_verify_approval_v5_preserves_head_and_replays(
    run_cli, tmp_path, prepare_approved_invocation
):
    session_id = "batch2-verify-approval"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="approval-provider")
    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    env.update(_make_approval_verifier(tmp_path, "approval"))

    _recommend(run_cli, tmp_path, session_id, registry, provider_id="approval-provider")

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")

    prepared = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        "approval-provider",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-for-verify-op"},
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_id = json.loads(prepared.stdout)["preflight_id"]

    before = _head(tmp_path, session_id)

    first = run_cli(
        "specialists",
        "verify-approval",
        "--preflight-id",
        preflight_id,
        "--evidence-ref",
        "sha256:" + "e" * 64,
        "--approval-verifier",
        "test-verifier",
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "verify-approval-op-1"},
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"
    assert committed["generation"] == before["generation"] + 1

    replay = run_cli(
        "specialists",
        "verify-approval",
        "--preflight-id",
        preflight_id,
        "--evidence-ref",
        "sha256:" + "e" * 64,
        "--approval-verifier",
        "test-verifier",
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "verify-approval-op-1"},
    )
    assert replay.returncode == 0, replay.stderr
    assert _head(tmp_path, session_id) == committed


def test_specialists_verify_approval_v5_requires_operation_id(
    run_cli, tmp_path
):
    session_id = "batch2-verify-approval-noid"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="approval-noid-provider")
    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    env.update(_make_approval_verifier(tmp_path, "approval-noid"))

    _recommend(run_cli, tmp_path, session_id, registry, provider_id="approval-noid-provider")

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")

    prepared = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        "approval-noid-provider",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-for-verify-noid-op"},
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_id = json.loads(prepared.stdout)["preflight_id"]
    before = _head(tmp_path, session_id)

    result = run_cli(
        "specialists",
        "verify-approval",
        "--preflight-id",
        preflight_id,
        "--evidence-ref",
        "sha256:" + "e" * 64,
        "--approval-verifier",
        "test-verifier",
        cwd=tmp_path,
        env_extra=env,  # no MISSION_OPERATION_ID
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


# ---------------------------------------------------------------------------
# tests: specialists prepare-invocation
# ---------------------------------------------------------------------------


def test_specialists_prepare_invocation_v5_preserves_head_and_replays(
    run_cli, tmp_path
):
    session_id = "batch2-prepare-inv"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="prepare-provider")
    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    _recommend(run_cli, tmp_path, session_id, registry, provider_id="prepare-provider")

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")
    before = _head(tmp_path, session_id)

    first = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        "prepare-provider",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-inv-op-1"},
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"
    assert committed["generation"] == before["generation"] + 1

    replay = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        "prepare-provider",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-inv-op-1"},
    )
    assert replay.returncode == 0, replay.stderr
    assert _head(tmp_path, session_id) == committed


def test_specialists_prepare_invocation_v5_requires_operation_id(
    run_cli, tmp_path
):
    session_id = "batch2-prepare-inv-noid"
    _init_v5(run_cli, tmp_path, session_id)
    registry = _make_registry(tmp_path, provider_id="prepare-noid-provider")
    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    _recommend(
        run_cli, tmp_path, session_id, registry, provider_id="prepare-noid-provider"
    )

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")
    before = _head(tmp_path, session_id)

    result = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        "prepare-noid-provider",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra=env,  # no MISSION_OPERATION_ID
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


# ---------------------------------------------------------------------------
# tests: specialists invoke-command / invoke-prepared – idempotency / no double-dispatch
# ---------------------------------------------------------------------------


def test_specialists_invoke_command_no_double_dispatch_on_replay(run_cli, tmp_path):
    """同一 operation_id の再実行で外部 provider が二重呼び出しされないこと。"""
    session_id = "batch2-invoke-cmd"
    _init_v5(run_cli, tmp_path, session_id)
    provider_id = "invoke-cmd-provider"

    registry = _make_registry(tmp_path, provider_id=provider_id)
    cmd_dir, marker = _make_counting_command(tmp_path, provider_id)
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    env.update(_make_approval_verifier(tmp_path, "invoke-cmd"))

    _recommend(run_cli, tmp_path, session_id, registry, provider_id=provider_id)

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")

    # Prepare and approve
    prepared = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        provider_id,
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-for-invoke-op"},
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight = json.loads(prepared.stdout)
    preflight_id = preflight["preflight_id"]

    run_cli(
        "specialists",
        "verify-approval",
        "--preflight-id",
        preflight_id,
        "--evidence-ref",
        "sha256:" + "e" * 64,
        "--approval-verifier",
        "test-verifier",
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "verify-for-invoke-op"},
        check=True,
    )

    invoke_env = {**env, "MISSION_OPERATION_ID": "invoke-cmd-op-1"}

    first = run_cli(
        "specialists",
        "invoke-prepared",
        "--provider",
        provider_id,
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--preflight-id",
        preflight_id,
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra=invoke_env,
    )
    assert first.returncode == 0, first.stderr
    assert marker.exists()
    count_after_first = int(marker.read_text().strip())
    assert count_after_first == 1

    committed = _head(tmp_path, session_id)

    replay = run_cli(
        "specialists",
        "invoke-prepared",
        "--provider",
        provider_id,
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--preflight-id",
        preflight_id,
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra=invoke_env,
    )
    assert replay.returncode == 0, replay.stderr
    # Count must still be 1 — provider was NOT called a second time
    assert int(marker.read_text().strip()) == count_after_first
    # Head did not advance
    assert _head(tmp_path, session_id) == committed


def test_specialists_invoke_command_v5_requires_operation_id(run_cli, tmp_path):
    session_id = "batch2-invoke-cmd-noid"
    _init_v5(run_cli, tmp_path, session_id)
    provider_id = "invoke-cmd-noid-provider"
    registry = _make_registry(tmp_path, provider_id=provider_id)
    cmd_dir = tmp_path / "commands"
    env = _env(session_id)
    env["PATH"] = f"{cmd_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    env.update(_make_approval_verifier(tmp_path, "invoke-cmd-noid"))

    _recommend(run_cli, tmp_path, session_id, registry, provider_id=provider_id)

    input_file = tmp_path / "input.txt"
    input_file.write_text("test input\n", encoding="utf-8")

    prepared = run_cli(
        "specialists",
        "prepare-invocation",
        "--provider",
        provider_id,
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--input-file",
        str(input_file),
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "prepare-noid-invoke"},
    )
    assert prepared.returncode == 0, prepared.stderr
    preflight_id = json.loads(prepared.stdout)["preflight_id"]

    run_cli(
        "specialists",
        "verify-approval",
        "--preflight-id",
        preflight_id,
        "--evidence-ref",
        "sha256:" + "e" * 64,
        "--approval-verifier",
        "test-verifier",
        cwd=tmp_path,
        env_extra={**env, "MISSION_OPERATION_ID": "verify-noid-invoke"},
        check=True,
    )

    before = _head(tmp_path, session_id)
    result = run_cli(
        "specialists",
        "invoke-command",
        "--provider",
        provider_id,
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--preflight-id",
        preflight_id,
        "--registry",
        str(registry),
        cwd=tmp_path,
        env_extra=env,  # no MISSION_OPERATION_ID
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


# ---------------------------------------------------------------------------
# tests: specialists reconcile-invocation
# ---------------------------------------------------------------------------


def test_specialists_reconcile_invocation_v5_preserves_head_and_replays(
    run_cli, tmp_path
):
    session_id = "batch2-reconcile"
    _init_v5(run_cli, tmp_path, session_id)
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    # Log a skipped invocation first (to establish an invocation_id)
    logged = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "reconcile-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        "--json",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="reconcile-setup-loginv"),
    )
    assert logged.returncode == 0, logged.stderr
    invocation_id = json.loads(logged.stdout)["entry"]["invocation_id"]

    # Patch the v5 session state to dispatch-unknown via the repository API
    def _set_dispatch_unknown(data: dict) -> None:
        for inv in data.get("specialist_invocations") or []:
            if inv.get("invocation_id") == invocation_id:
                inv["status"] = "dispatch-unknown"
                inv["lifecycle_state"] = "dispatch-unknown"
                inv["fencing_epoch"] = 1
                inv["reservation_owner_session_id"] = session_id
                inv["operation_id"] = "test-op-id"
                inv["outbound_packet_digest"] = "sha256:" + "a" * 64
                break
        data["fencing_epoch"] = 1

    _v5_patch_session_state(tmp_path, session_id, _set_dispatch_unknown)

    evidence_path = tmp_path / "evidence.md"
    evidence_path.write_text("# Reconcile Evidence\n\ncontent\n", encoding="utf-8")

    before = _head(tmp_path, session_id)

    first = run_cli(
        "specialists",
        "reconcile-invocation",
        "--invocation-id",
        invocation_id,
        "--status",
        "abandoned-unknown",
        "--expected-fencing-epoch",
        "1",
        "--evidence",
        str(evidence_path),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="reconcile-op-1"),
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    assert committed["schema"] == "mission-head/1"
    assert committed["generation"] == before["generation"] + 1

    replay = run_cli(
        "specialists",
        "reconcile-invocation",
        "--invocation-id",
        invocation_id,
        "--status",
        "abandoned-unknown",
        "--expected-fencing-epoch",
        "1",
        "--evidence",
        str(evidence_path),
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="reconcile-op-1"),
    )
    assert replay.returncode == 0, replay.stderr
    assert _head(tmp_path, session_id) == committed


def test_specialists_reconcile_invocation_v5_requires_operation_id(run_cli, tmp_path):
    session_id = "batch2-reconcile-noid"
    _init_v5(run_cli, tmp_path, session_id)

    logged = run_cli(
        "specialists",
        "log-invocation",
        "--iteration",
        "1",
        "--phase",
        "planning",
        "--role",
        "planning-provider",
        "--skill",
        "reconcile-noid-skill",
        "--mode",
        "fallback-core",
        "--status",
        "skipped",
        "--reason",
        "test",
        "--json",
        cwd=tmp_path,
        env_extra=_env(session_id, operation_id="reconcile-noid-setup"),
    )
    assert logged.returncode == 0, logged.stderr
    invocation_id = json.loads(logged.stdout)["entry"]["invocation_id"]

    # Patch the v5 session state to dispatch-unknown via the repository API
    def _set_dispatch_unknown_noid(data: dict) -> None:
        for inv in data.get("specialist_invocations") or []:
            if inv.get("invocation_id") == invocation_id:
                inv["status"] = "dispatch-unknown"
                inv["lifecycle_state"] = "dispatch-unknown"
                inv["fencing_epoch"] = 1
                inv["reservation_owner_session_id"] = session_id
                inv["operation_id"] = "test-op-id"
                inv["outbound_packet_digest"] = "sha256:" + "a" * 64
                break
        data["fencing_epoch"] = 1

    _v5_patch_session_state(tmp_path, session_id, _set_dispatch_unknown_noid)

    evidence_path = tmp_path / "evidence-noid.md"
    evidence_path.write_text("# Evidence\n\ncontent\n", encoding="utf-8")
    before = _head(tmp_path, session_id)

    result = run_cli(
        "specialists",
        "reconcile-invocation",
        "--invocation-id",
        invocation_id,
        "--status",
        "abandoned-unknown",
        "--expected-fencing-epoch",
        "1",
        "--evidence",
        str(evidence_path),
        cwd=tmp_path,
        env_extra=_env(session_id),  # no operation_id
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


# ---------------------------------------------------------------------------
# tests: specialists plan-import
# ---------------------------------------------------------------------------


def _plan_contract() -> dict:
    return {
        "envelope_schema": "mission-provider-result/1",
        "artifact_schema": "mission-plan/1",
        "cardinality": "exactly-one",
        "required_capability_class": "deep-planning",
        "required_capability_variant": "portable-v1",
        "require_exact_variant": True,
    }


def _plan_document_body() -> dict:
    return {
        "objective": "bounded",
        "scope": {"resources": [], "actions": [{"type": "analyze", "effect_class": "reversible"}]},
        "assumptions": [{"id": "a", "statement": "s", "validation": "v"}],
        "steps": [{"id": "s", "action": "analyze", "inputs": [], "outputs": [], "depends_on": [],
                   "acceptance_checks": ["observable"], "risk": "low", "rollback": "none"}],
        "global_acceptance": ["done"],
        "stop_conditions": ["blocked"],
    }


def _make_plan_registry(root: Path, *, provider_id: str = "planimp-provider") -> Path:
    """Create a registry with a deep-planning provider that has a result_contract."""
    commands = root / "commands"
    commands.mkdir(exist_ok=True)
    cmd = commands / f"{provider_id}-cmd"
    cmd.write_text("#!/bin/sh\ncat >/dev/null\necho '{}'\n", encoding="utf-8")
    cmd.chmod(0o700)
    registry = root / f"registry-{provider_id}.json"
    registry.write_text(
        json.dumps({
            "schema": "mission-specialist-registry/2",
            "specialists_v2": [{
                "provider_id": provider_id,
                "role": "deep-planning",
                "skill": provider_id,
                "kind": "command",
                "command": f"{provider_id}-cmd",
                "args": [], "env": {},
                "task_profiles": ["architecture"],
                "phases": ["planning"],
                "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
                "result_contract": _plan_contract(),
            }],
        }),
        encoding="utf-8",
    )
    return registry.resolve()


def _setup_v5_plan_import(run_cli, root: Path, session_id: str, *, provider_id: str = "planimp-provider"):
    """Set up a v5 session fully ready for specialists plan-import.

    Returns (registry, invocation_id, result_doc, cmd_env) where cmd_env
    includes PATH so the provider command is found.
    """
    registry = _make_plan_registry(root, provider_id=provider_id)
    commands = root / "commands"

    _init_v5(run_cli, root, session_id)

    # Recommend to record specialist_registry_projection in state.
    rec_env = {**_env(session_id, operation_id="planimp-setup-rec"),
               "PATH": f"{commands}{os.pathsep}{os.environ.get('PATH', '')}"}
    rec = run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review architecture",
        "--registry", str(registry),
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=root, env_extra=rec_env,
    )
    assert rec.returncode == 0, rec.stderr

    # In v5, candidates are returned in stdout (not committed to session state).
    rec_out = json.loads(rec.stdout)
    candidates = rec_out.get("specialists_candidates") or []
    assert candidates, f"no candidates after recommend: {rec.stdout}"
    candidate = dict(candidates[0])
    selection_id = (rec_out.get("specialists_decision") or {}).get("selection_id") or "sel-planimp"
    candidate["selection_id"] = selection_id
    candidate["eligibility_selection_source"] = "automatic"

    invocation_id = "inv_" + "c" * 32
    preflight_id = "pf_planimp_setup"

    private = root / ".mission-state" / "private-preflights"
    private.mkdir(exist_ok=True)
    packet = private / f"{preflight_id}.json"
    packet.write_text("packet-content", encoding="utf-8")
    outbound = "sha256:" + hashlib.sha256(packet.read_bytes()).hexdigest()

    receipts_dir = root / ".mission-state" / "private-receipts"
    receipts_dir.mkdir(exist_ok=True)
    receipt_file = receipts_dir / f"{preflight_id}.json"
    receipt_file.write_text("receipt-content", encoding="utf-8")
    receipt_digest = "sha256:" + hashlib.sha256(receipt_file.read_bytes()).hexdigest()

    def _patcher(data: dict) -> None:
        data["iteration"] = 1
        data["phase"] = "planning"
        data["specialists_selected"] = [candidate]
        data["specialists_decision"] = {
            "policy": "auto", "action": "select",
            "prompted_user": False, "decision": "selected",
            "selection_id": selection_id,
        }
        data["specialist_invocations"] = [{
            "invocation_id": invocation_id,
            "iteration": 1,
            "phase": "planning",
            "role": "deep-planning",
            "skill": provider_id,
            "mode": "command-provider",
            "status": "completed",
            "lifecycle_state": "terminal",
            "timestamp": "2026-01-01T00:00:00Z",
        }]
        data["provider_preflights"] = {
            preflight_id: {
                "invocation_id": invocation_id,
                "consumed_invocation_id": invocation_id,
                "outbound_packet_digest": outbound,
                "status": "consumed",
                "artifact_path": str(packet.relative_to(root / ".mission-state")),
                "receipt": {
                    "artifact_path": str(receipt_file.relative_to(root / ".mission-state")),
                    "digest": receipt_digest,
                },
            }
        }

    _v5_patch_session_state(root, session_id, _patcher)

    binding = {
        "invocation_id": invocation_id,
        "preflight_id": preflight_id,
        "outbound_packet_digest": outbound,
        "selection_id": selection_id,
        "selection_source": "automatic",
        "iteration": 1,
    }
    result_doc = {
        "schema": "mission-provider-result/1",
        "binding": binding,
        "capability_attestation": {
            "requested_class": "deep-planning",
            "effective_class": "deep-planning",
            "requested_variant": "portable-v1",
            "effective_variant": "portable-v1",
        },
        "artifacts": [{"schema": "mission-plan/1", "document": _plan_document_body()}],
    }

    cmd_env = {**_env(session_id),
               "PATH": f"{commands}{os.pathsep}{os.environ.get('PATH', '')}"}
    return registry, invocation_id, result_doc, cmd_env


def test_specialists_plan_import_v5_requires_operation_id(run_cli, tmp_path):
    """plan-import が v5 で MISSION_OPERATION_ID を必須とすること。"""
    session_id = "batch2-plan-import-noid"
    registry, invocation_id, result_doc, cmd_env = _setup_v5_plan_import(
        run_cli, tmp_path, session_id
    )
    source = tmp_path / "plan-noid-result.json"
    source.write_text(json.dumps(result_doc), encoding="utf-8")
    before = _head(tmp_path, session_id)

    result = run_cli(
        "specialists",
        "plan-import",
        "--invocation-id",
        invocation_id,
        "--input",
        str(source),
        "--registry",
        str(tmp_path / f"registry-planimp-provider.json"),
        cwd=tmp_path,
        env_extra={k: v for k, v in cmd_env.items() if k != "MISSION_OPERATION_ID"},
    )
    assert result.returncode == 2
    assert "MISSION_OPERATION_ID" in result.stderr
    assert _head(tmp_path, session_id) == before


def test_specialists_plan_import_v5_preserves_head_and_replays(run_cli, tmp_path):
    """plan-import が v5 で冪等にリプレイされ head が進まないこと (M-1 replay fast-path)。

    Lease を失効させた後に同じ operation_id でリトライしても成功することを確認する。
    Without M-1, the lease check fires on retry and returns exit code 2.
    """
    session_id = "batch2-plan-import-replay"
    registry, invocation_id, result_doc, cmd_env = _setup_v5_plan_import(
        run_cli, tmp_path, session_id
    )
    source = tmp_path / "plan-replay-result.json"
    source.write_text(json.dumps(result_doc), encoding="utf-8")
    op_env = {**cmd_env, "MISSION_OPERATION_ID": "planimp-replay-op-1"}

    first = run_cli(
        "specialists", "plan-import",
        "--invocation-id", invocation_id,
        "--input", str(source),
        "--registry", str(registry),
        "--json",
        cwd=tmp_path, env_extra=op_env,
    )
    assert first.returncode == 0, first.stderr
    out = json.loads(first.stdout)
    assert out["ok"] is True
    assert isinstance(out["plan_import"], dict)
    committed = _head(tmp_path, session_id)
    assert committed["generation"] > 0

    # Modify the invocation status so that domain validation would fail on a
    # fresh (non-replay) execution.  Without M-1, the replay path falls through
    # to the invocation check and exits 2.  With M-1, the fast-path fires before
    # domain validation and returns the stored result.
    def _invalidate_invocation(data: dict) -> None:
        for inv in data.get("specialist_invocations") or []:
            if inv.get("invocation_id") == invocation_id:
                inv["status"] = "started"
                inv["lifecycle_state"] = "invoked"
                break

    _v5_patch_session_state(tmp_path, session_id, _invalidate_invocation)
    # The patch itself advances the head; capture the new generation as baseline.
    after_invalidation = _head(tmp_path, session_id)

    replay = run_cli(
        "specialists", "plan-import",
        "--invocation-id", invocation_id,
        "--input", str(source),
        "--registry", str(registry),
        "--json",
        cwd=tmp_path, env_extra=op_env,
    )
    assert replay.returncode == 0, replay.stderr
    replay_out = json.loads(replay.stdout)
    assert replay_out["ok"] is True
    assert replay_out["plan_import"] == out["plan_import"]
    # Head must not advance beyond the invalidation patch.
    assert _head(tmp_path, session_id)["generation"] == after_invalidation["generation"]


def test_specialists_plan_import_v5_intent_collision_rejected(run_cli, tmp_path):
    """plan-import が同一 operation_id で異なる invocation_id を弾くこと。"""
    session_id = "batch2-plan-import-collision"
    registry, invocation_id, result_doc, cmd_env = _setup_v5_plan_import(
        run_cli, tmp_path, session_id
    )
    source = tmp_path / "plan-collision-result.json"
    source.write_text(json.dumps(result_doc), encoding="utf-8")
    op_env = {**cmd_env, "MISSION_OPERATION_ID": "planimp-collision-op"}

    first = run_cli(
        "specialists", "plan-import",
        "--invocation-id", invocation_id,
        "--input", str(source),
        "--registry", str(registry),
        cwd=tmp_path, env_extra=op_env,
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)

    # Same operation_id but different invocation_id → intent collision
    collision = run_cli(
        "specialists", "plan-import",
        "--invocation-id", "inv_" + "d" * 32,
        "--input", str(source),
        "--registry", str(registry),
        cwd=tmp_path, env_extra=op_env,
    )
    assert collision.returncode == 2
    assert "operation ID has a different intent" in collision.stderr
    assert _head(tmp_path, session_id) == committed


def test_specialists_plan_import_retained_v4_unchanged(legacy_run_cli, tmp_path):
    """v4（retained）セッションで plan-import が v4 挙動のまま動くこと。"""
    # v4 has no MISSION_OPERATION_ID requirement; operation_id is not required
    result = legacy_run_cli(
        "init",
        "retained v4 plan-import",
        "--complexity",
        "Complex",
        "--force-mission",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "batch2-planimp-v4",
                   "MISSION_LEASE_ID": "batch2-planimp-v4-lease"},
    )
    assert result.returncode == 0, result.stderr

    # Without MISSION_OPERATION_ID, v4 should not raise an error early on invocation_id check
    # It will fail on domain validation (invocation-not-found) but not on operation_id missing
    result = legacy_run_cli(
        "specialists",
        "plan-import",
        "--invocation-id",
        "inv_" + "e" * 32,
        "--input",
        str(tmp_path / "nonexistent.json"),
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "batch2-planimp-v4",
                   "MISSION_LEASE_ID": "batch2-planimp-v4-lease"},
    )
    assert result.returncode == 2
    # v4 should NOT require MISSION_OPERATION_ID
    assert "MISSION_OPERATION_ID" not in result.stderr


# ---------------------------------------------------------------------------
# tests: surrounding commands not broken
# ---------------------------------------------------------------------------


def test_surrounding_commands_work_after_batch2_migration(run_cli, tmp_path):
    """get / set / resume / specialists accounting / specialists summary が壊れていないこと。"""
    session_id = "batch2-surrounding"
    _init_v5(run_cli, tmp_path, session_id)

    for cmd, args in [
        ("get", []),
        ("set", ["surrounding_probe=true"]),
        ("resume", []),
        ("specialists", ["accounting"]),
        ("specialists", ["summary"]),
    ]:
        result = run_cli(cmd, *args, cwd=tmp_path, env_extra=_env(session_id))
        assert result.returncode == 0, f"{cmd} {args} failed: {result.stderr}"

    assert _public_state(run_cli, tmp_path, session_id).get("surrounding_probe") is True


# ---------------------------------------------------------------------------
# tests: allowlist is exactly 3 commands after batch2
# ---------------------------------------------------------------------------


def test_batch2_allowlist_post_batch3_is_empty():
    """Batch 3 完了後 C2_DIRECT_WRITE_ALLOWLIST は空（Batch 2 完了時点では 3 件だったが、
    Batch 3 (#550) で残り 3 件が移行されたため frozenset() になった）。"""
    from mission_application.command_owners import C2_DIRECT_WRITE_ALLOWLIST

    assert C2_DIRECT_WRITE_ALLOWLIST == frozenset()


def test_batch2_repository_commands_includes_all_8_specialists():
    """Batch 2 完了後 C2_REPOSITORY_COMMANDS に specialists 系 8 件が含まれる。"""
    from mission_application.command_owners import C2_REPOSITORY_COMMANDS

    specialists_commands = {
        "specialists recommend",
        "specialists log-invocation",
        "specialists verify-approval",
        "specialists prepare-invocation",
        "specialists invoke-command",
        "specialists invoke-prepared",
        "specialists reconcile-invocation",
        "specialists plan-import",
    }
    assert specialists_commands <= C2_REPOSITORY_COMMANDS
