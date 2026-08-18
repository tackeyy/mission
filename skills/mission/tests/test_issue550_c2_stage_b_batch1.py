"""Issue #550 C2 Stage B Batch 1 real-process repository coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


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


def _prepare_handoff(run_cli, root: Path, session_id: str) -> None:
    initialized = run_cli(
        "init",
        "C2 Stage B executor handoff",
        "--complexity",
        "Standard",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=root,
        env_extra=_env(session_id),
    )
    assert initialized.returncode == 0, initialized.stderr
    iteration = _public_state(run_cli, root, session_id)["iteration"]
    plan_path = root / ".mission-state" / "plans" / (session_id + ".json")
    plan_path.parent.mkdir(exist_ok=True)
    plan_payload = {
        "schema": "mission-plan/1",
        "steps": [
            {"depends_on": [], "id": "s1"},
            {"depends_on": ["s1"], "id": "s2"},
        ],
    }
    plan_bytes = json.dumps(
        plan_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    plan_path.write_bytes(plan_bytes)
    binding = {
        "generation": 1,
        "iteration": iteration,
        "selection_source": "automatic",
        "source": "core",
        "source_id": "batch1-fixture",
    }
    plan = {
        **binding,
        "digest": "sha256:" + hashlib.sha256(plan_bytes).hexdigest(),
        "path": str(plan_path.relative_to(root)),
        "schema": "mission-plan/1",
        "source_digest": "sha256:" + hashlib.sha256(plan_bytes).hexdigest(),
        "validated_at": "2026-08-18T00:00:00Z",
    }
    handoff = {
        "handoff_id": "handoff_" + session_id,
        "iteration": iteration,
        "plan_digest": plan["digest"],
        "plan_generation": plan["generation"],
        "plan_path": plan["path"],
        "plan_source": plan["source"],
        "schema": "mission-executor-handoff/1",
        "selection_source": plan["selection_source"],
        "source_id": plan["source_id"],
        "status": "prepared",
        "step_ids": ["s1", "s2"],
    }
    configured = run_cli(
        "set",
        "planning_policy_version=1",
        "canonical_plan=" + json.dumps(plan, sort_keys=True, separators=(",", ":")),
        "executor_handoff="
        + json.dumps(handoff, sort_keys=True, separators=(",", ":")),
        "planning_source_records="
        + json.dumps(
            {"core:batch1-fixture": binding},
            sort_keys=True,
            separators=(",", ":"),
        ),
        cwd=root,
        env_extra=_env(session_id),
    )
    assert configured.returncode == 0, configured.stderr
    assert _public_state(run_cli, root, session_id)["executor_handoff"]["status"] == "prepared"


def _run_operation(run_cli, root: Path, session_id: str, operation_id: str, *command: str):
    return run_cli(
        "executor-handoff",
        *command,
        cwd=root,
        env_extra=_env(session_id, operation_id=operation_id),
    )


def _prefix_for(run_cli, root: Path, session_id: str, target: str) -> None:
    if target != "begin":
        result = _run_operation(run_cli, root, session_id, "prefix-begin", "begin")
        assert result.returncode == 0, result.stderr
    if target == "complete":
        for step_id in ("s1", "s2"):
            result = _run_operation(
                run_cli,
                root,
                session_id,
                "prefix-record-" + step_id,
                "record-step",
                "--step-id",
                step_id,
                "--result",
                "ok",
            )
            assert result.returncode == 0, result.stderr


def test_executor_handoff_real_cli_preserves_v5_heads_replays_and_domain_order(
    run_cli, tmp_path
):
    session_id = "batch1-flow"
    _prepare_handoff(run_cli, tmp_path, session_id)
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"

    commands = [
        ("begin-1", ("begin",)),
        ("verify-s1-1", ("verify-step", "--step-id", "s1")),
    ]
    for operation_id, command in commands:
        before = _head(tmp_path, session_id)
        first = _run_operation(run_cli, tmp_path, session_id, operation_id, *command)
        assert first.returncode == 0, first.stderr
        committed = _head(tmp_path, session_id)
        assert committed["schema"] == "mission-head/1"
        assert committed["generation"] == before["generation"] + 1
        replay = _run_operation(run_cli, tmp_path, session_id, operation_id, *command)
        assert replay.returncode == 0, replay.stderr
        assert json.loads(replay.stdout) == json.loads(first.stdout)
        assert _head(tmp_path, session_id) == committed

    before_rejection = _head(tmp_path, session_id)
    rejected = _run_operation(
        run_cli,
        tmp_path,
        session_id,
        "record-s2-too-early",
        "record-step",
        "--step-id",
        "s2",
        "--result",
        "ok",
    )
    assert rejected.returncode == 2
    assert "executor-step-dependency-incomplete" in rejected.stderr
    assert _head(tmp_path, session_id) == before_rejection

    recorded_s1 = _run_operation(
        run_cli,
        tmp_path,
        session_id,
        "record-s1-1",
        "record-step",
        "--step-id",
        "s1",
        "--result",
        "ok",
    )
    assert recorded_s1.returncode == 0, recorded_s1.stderr
    record_s1_head = _head(tmp_path, session_id)
    record_s1_replay = _run_operation(
        run_cli,
        tmp_path,
        session_id,
        "record-s1-1",
        "record-step",
        "--step-id",
        "s1",
        "--result",
        "ok",
    )
    assert record_s1_replay.returncode == 0, record_s1_replay.stderr
    assert json.loads(record_s1_replay.stdout) == json.loads(recorded_s1.stdout)
    assert _head(tmp_path, session_id) == record_s1_head

    recorded = _run_operation(
        run_cli,
        tmp_path,
        session_id,
        "record-s2-1",
        "record-step",
        "--step-id",
        "s2",
        "--result",
        "ok",
    )
    assert recorded.returncode == 0, recorded.stderr
    completed = _run_operation(
        run_cli, tmp_path, session_id, "complete-1", "complete"
    )
    assert completed.returncode == 0, completed.stderr
    complete_head = _head(tmp_path, session_id)
    replay = _run_operation(
        run_cli, tmp_path, session_id, "complete-1", "complete"
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == json.loads(completed.stdout)
    assert _head(tmp_path, session_id) == complete_head

    state = _public_state(run_cli, tmp_path, session_id)
    assert state["executor_handoff"]["status"] == "consumed"
    assert [decision["step_id"] for decision in state["decisions"]] == ["s1", "s2"]
    phase = run_cli(
        "get", "--field", "phase", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert phase.returncode == 0, phase.stderr
    assert json.loads(phase.stdout) == "planning"
    updated = run_cli(
        "set", "batch1_probe=true", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert updated.returncode == 0, updated.stderr
    resumed = run_cli("resume", cwd=tmp_path, env_extra=_env(session_id))
    assert resumed.returncode == 0, resumed.stderr
    assert _public_state(run_cli, tmp_path, session_id)["batch1_probe"] is True
    assert _head(tmp_path, session_id)["schema"] == "mission-head/1"


@pytest.mark.parametrize(
    ("target", "command", "collision"),
    [
        ("begin", ("begin",), ("verify-step", "--step-id", "s1")),
        (
            "verify",
            ("verify-step", "--step-id", "s1"),
            ("verify-step", "--step-id", "s2"),
        ),
        (
            "record",
            ("record-step", "--step-id", "s1", "--result", "ok"),
            ("record-step", "--step-id", "s1", "--result", "partial"),
        ),
        ("complete", ("complete",), ("verify-step", "--step-id", "s1")),
    ],
)
def test_each_executor_handoff_command_has_caller_stable_operation_identity(
    run_cli, tmp_path, target, command, collision
):
    session_id = "batch1-id-" + target
    _prepare_handoff(run_cli, tmp_path, session_id)
    _prefix_for(run_cli, tmp_path, session_id, target)
    operation_id = "batch1-target-" + target

    first = _run_operation(
        run_cli, tmp_path, session_id, operation_id, *command
    )
    assert first.returncode == 0, first.stderr
    committed = _head(tmp_path, session_id)
    replay = _run_operation(
        run_cli, tmp_path, session_id, operation_id, *command
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == json.loads(first.stdout)
    assert _head(tmp_path, session_id) == committed

    rejected = _run_operation(
        run_cli, tmp_path, session_id, operation_id, *collision
    )
    assert rejected.returncode == 2
    assert "operation ID has a different intent" in rejected.stderr
    assert _head(tmp_path, session_id) == committed


@pytest.mark.parametrize(
    ("target", "command"),
    [
        ("begin", ("begin",)),
        ("verify", ("verify-step", "--step-id", "s1")),
        (
            "record",
            ("record-step", "--step-id", "s1", "--result", "ok"),
        ),
        ("complete", ("complete",)),
    ],
)
def test_each_executor_handoff_v5_mutation_requires_operation_id(
    run_cli, tmp_path, target, command
):
    session_id = "batch1-missing-id-" + target
    _prepare_handoff(run_cli, tmp_path, session_id)
    _prefix_for(run_cli, tmp_path, session_id, target)
    before = _head(tmp_path, session_id)

    rejected = run_cli(
        "executor-handoff",
        *command,
        cwd=tmp_path,
        env_extra=_env(session_id),
    )

    assert rejected.returncode == 2
    assert "MISSION_OPERATION_ID" in rejected.stderr
    assert _head(tmp_path, session_id) == before


def test_executor_handoff_retained_v4_cli_behavior_is_unchanged(
    legacy_run_cli, tmp_path
):
    session_id = "batch1-v4"
    initialized = legacy_run_cli(
        "init",
        "retained v4 executor handoff",
        "--complexity",
        "Standard",
        "--force-mission",
        cwd=tmp_path,
        env_extra=_env(session_id),
    )
    assert initialized.returncode == 0, initialized.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / (session_id + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan_path = tmp_path / ".mission-state" / "plans" / "legacy-batch1.json"
    plan_path.parent.mkdir(exist_ok=True)
    payload = {"schema": "mission-plan/1", "steps": [{"depends_on": [], "id": "s1"}]}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan_path.write_bytes(raw)
    binding = {
        "generation": 1,
        "iteration": state["iteration"],
        "selection_source": "automatic",
        "source": "core",
        "source_id": "legacy-batch1",
    }
    state["planning_policy_version"] = 1
    state["canonical_plan"] = {
        **binding,
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "path": str(plan_path.relative_to(tmp_path)),
        "schema": "mission-plan/1",
        "source_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "validated_at": "2026-08-18T00:00:00Z",
    }
    state["planning_source_records"] = {"core:legacy-batch1": binding}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    advanced = legacy_run_cli(
        "advance", "--phase", "executing", cwd=tmp_path, env_extra=_env(session_id)
    )
    assert advanced.returncode == 0, advanced.stderr

    for command in (
        ("begin",),
        ("verify-step", "--step-id", "s1"),
        ("record-step", "--step-id", "s1", "--result", "ok"),
        ("complete",),
    ):
        result = legacy_run_cli(
            "executor-handoff", *command, cwd=tmp_path, env_extra=_env(session_id)
        )
        assert result.returncode == 0, (command, result.stderr)
    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert "schema" not in final
    assert final["executor_handoff"]["status"] == "consumed"
    assert [decision["step_id"] for decision in final["decisions"]] == ["s1"]


def test_executor_handoff_v5_canonical_drift_rejection_replays_fail_closed(
    run_cli, tmp_path
):
    session_id = "batch1-drift"
    _prepare_handoff(run_cli, tmp_path, session_id)
    plan_path = tmp_path / ".mission-state" / "plans" / (session_id + ".json")
    plan_path.write_text('{"schema":"mission-plan/1","steps":[]}', encoding="utf-8")
    before = _head(tmp_path, session_id)

    first = _run_operation(
        run_cli, tmp_path, session_id, "batch1-drift-begin", "begin"
    )

    assert first.returncode == 2
    assert "canonical-plan-digest-drift" in first.stderr
    rejected_head = _head(tmp_path, session_id)
    assert rejected_head["generation"] == before["generation"] + 1
    assert _public_state(run_cli, tmp_path, session_id)["executor_handoff"][
        "status"
    ] == "rejected"
    replay = _run_operation(
        run_cli, tmp_path, session_id, "batch1-drift-begin", "begin"
    )
    assert replay.returncode == 2
    assert replay.stderr == first.stderr
    assert _head(tmp_path, session_id) == rejected_head


def test_executor_handoff_v5_rejects_stale_fencing_token(run_cli, tmp_path):
    session_id = "batch1-fencing"
    _prepare_handoff(run_cli, tmp_path, session_id)
    before = _head(tmp_path, session_id)

    rejected = run_cli(
        "executor-handoff",
        "begin",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": session_id,
            "MISSION_LEASE_ID": "wrong-lease",
            "MISSION_OPERATION_ID": "batch1-stale-fence",
        },
    )

    assert rejected.returncode == 2
    assert "lease" in rejected.stderr
    assert _head(tmp_path, session_id) == before
