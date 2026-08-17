"""Issue #510: A5 runtime guard observation application boundary."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path

import pytest


AUTHORITY_FIELDS = {
    "passes", "halt_reason", "phase", "score_history", "lease_id",
    "fencing_epoch", "owner_session_id", "lease_expires_at",
}
MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


def _load_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue510", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StopRepository:
    def __init__(self, previous=None, *, failure=None):
        self.previous = copy.deepcopy(previous)
        self.saved = None
        self.failure = failure

    def transaction(self):
        return nullcontext()

    def load(self, session_id):
        return copy.deepcopy(self.previous), ("identity", 1)

    def save(self, session_id, document, expected_identity):
        if self.failure:
            raise ValueError(self.failure)
        self.saved = copy.deepcopy(document)


class MissionRepository:
    def __init__(self, state, *, failure=None):
        self.state = copy.deepcopy(state)
        self.saved = None
        self.failure = failure

    def transaction(self):
        return nullcontext()

    def load(self):
        return copy.deepcopy(self.state)

    def execute(self, state, mutation, transition=None):
        proposed = copy.deepcopy(state)
        mutation(proposed)
        return proposed

    def save(self, state, **kwargs):
        if self.failure:
            raise ValueError(self.failure)
        self.saved = copy.deepcopy(state)


def _previous():
    return {
        "schema": "mission-stop-guard/1",
        "session_id": "session-1",
        "last_digest": "a" * 64,
        "last_detail_epoch": 100,
        "block_count": 1,
        "reinjection_count": 1,
        "detail_count": 1,
        "heartbeat_count": 0,
    }


def _state():
    return {
        "phase": "executing",
        "passes": False,
        "halt_reason": "",
        "score_history": [],
        "lease_id": "lease-1",
        "fencing_epoch": 3,
        "owner_session_id": "owner-1",
        "lease_expires_at": "2030-01-01T00:00:00Z",
    }


def test_a5_command_ownership_is_closed_and_unique():
    from mission_application.runtime_guard import RUNTIME_GUARD_COMMAND_OWNERS

    assert RUNTIME_GUARD_COMMAND_OWNERS == {
        "permission-preflight": "A5.runtime-guard",
        "stop-guard-observe": "A5.runtime-guard",
    }


def test_stop_observation_only_updates_allowlisted_sidecar_fields():
    from mission_application.runtime_guard import StopObservationRequest, observe_stop_guard

    previous = _previous()
    repository = StopRepository(previous)
    request = StopObservationRequest(
        session_id="session-1", digest="b" * 64, now_epoch=101, ttl_seconds=600
    )

    result = observe_stop_guard(repository, request)

    assert result.mode == "detail"
    assert repository.saved == {
        **previous,
        "last_digest": "b" * 64,
        "last_detail_epoch": 101,
        "block_count": 2,
        "reinjection_count": 2,
        "detail_count": 2,
    }
    assert set(repository.saved).isdisjoint(AUTHORITY_FIELDS)


@pytest.mark.parametrize(
    "overrides",
    [
        {"now_epoch": True},
        {"ttl_seconds": 0},
        {"digest": "x" * 64},
        {"session_id": ""},
    ],
)
def test_malformed_stop_observation_rejects_before_repository_write(overrides):
    from mission_application.runtime_guard import StopObservationRequest, observe_stop_guard

    values = dict(
        session_id="session-1", digest="b" * 64, now_epoch=101, ttl_seconds=600
    )
    values.update(overrides)
    repository = StopRepository(_previous())

    with pytest.raises(ValueError):
        observe_stop_guard(repository, StopObservationRequest(**values))

    assert repository.saved is None


def test_malformed_existing_sidecar_rejects_without_rewrite():
    from mission_application.runtime_guard import StopObservationRequest, observe_stop_guard

    previous = {**_previous(), "passes": True}
    repository = StopRepository(previous)
    with pytest.raises(ValueError):
        observe_stop_guard(
            repository,
            StopObservationRequest("session-1", "b" * 64, 101, 600),
        )
    assert repository.saved is None
    assert repository.previous == previous


@pytest.mark.parametrize(
    "failure", ["stale-generation", "foreign-lease", "stale-fence", "expiry-race"]
)
def test_stop_repository_cas_failure_leaves_authoritative_input_unchanged(failure):
    from mission_application.runtime_guard import StopObservationRequest, observe_stop_guard

    previous = _previous()
    repository = StopRepository(previous, failure=failure)
    with pytest.raises(ValueError, match=failure):
        observe_stop_guard(
            repository,
            StopObservationRequest("session-1", "b" * 64, 101, 600),
        )
    assert repository.previous == previous
    assert repository.saved is None


@pytest.mark.parametrize("outcome", ["denied", "unknown"])
def test_permission_denied_or_unknown_cannot_be_weakened_to_allowed(outcome):
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    state = _state()
    repository = MissionRepository(state)
    result = record_permission_observation(
        repository,
        PermissionObservationRequest(
            probes=(PermissionProbe("state", outcome, "write-unavailable"),),
            observed_at="2026-08-17T00:00:00Z",
        ),
    )

    assert result.ok is False
    assert result.halt_recorded is True
    assert repository.saved["phase"] == "halted"
    assert repository.saved["passes"] is False
    assert repository.saved["halt_category"] == "blocked-external"
    assert repository.saved["terminal_outcome"] == "blocked_external"
    assert repository.saved["lease_id"] == state["lease_id"]
    assert repository.saved["fencing_epoch"] == state["fencing_epoch"]


def test_permission_allowed_observation_does_not_write_state():
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    repository = MissionRepository(_state())
    result = record_permission_observation(
        repository,
        PermissionObservationRequest(
            probes=(
                PermissionProbe("state", "allowed", None),
                PermissionProbe("assumptions", "allowed", None),
            ),
            observed_at="2026-08-17T00:00:00Z",
        ),
    )
    assert result.ok is True
    assert result.halt_recorded is False
    assert repository.saved is None


@pytest.mark.parametrize(
    "failure", ["stale-generation", "foreign-lease", "stale-fence", "expiry-race"]
)
def test_permission_repository_failure_does_not_mutate_loaded_state(failure):
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    state = _state()
    repository = MissionRepository(state, failure=failure)
    with pytest.raises(ValueError, match=failure):
        record_permission_observation(
            repository,
            PermissionObservationRequest(
                probes=(PermissionProbe("state", "denied", "write-unavailable"),),
                observed_at="2026-08-17T00:00:00Z",
            ),
        )
    assert repository.state == state
    assert repository.saved is None


def test_permission_transition_callback_cannot_inject_authority_fields():
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    state = _state()
    repository = MissionRepository(state)

    def forged_transition(proposed, _phase, _at):
        proposed.update(
            {
                "phase": "done",
                "passes": True,
                "score_history": [{"composite": 5.0}],
                "lease_id": "forged",
                "fencing_epoch": 999,
                "provider": {"status": "passed"},
            }
        )

    with pytest.raises(ValueError, match="permission-transition-invalid"):
        record_permission_observation(
            repository,
            PermissionObservationRequest(
                probes=(PermissionProbe("state", "denied", "write-unavailable"),),
                observed_at="2026-08-17T00:00:00Z",
            ),
            transition_phase=forged_transition,
        )

    assert repository.state == state
    assert repository.saved is None


def test_permission_transition_callback_cannot_nest_authority_in_timing_fields():
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    repository = MissionRepository(_state())

    def forged_transition(proposed, _phase, at):
        proposed.update(
            {
                "phase": "halted",
                "phase_started_at": at,
                "activity_current": None,
                "activity_segments": [{"passes": True}],
                "activity_rollup": {"provider": {"status": "passed"}},
            }
        )

    with pytest.raises(ValueError, match="permission-transition-invalid"):
        record_permission_observation(
            repository,
            PermissionObservationRequest(
                probes=(PermissionProbe("state", "denied", "write-unavailable"),),
                observed_at="2026-08-17T00:00:00Z",
            ),
            transition_phase=forged_transition,
        )
    assert repository.saved is None


def test_permission_foreign_lease_rejection_changes_no_durable_bytes(tmp_path, run_cli):
    owner = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli(
        "init",
        "A5 foreign lease",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=owner,
        check=True,
    )
    state_path = tmp_path / ".mission-state" / "sessions" / "session-a.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["assumptions_path"] = "outside.md"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in (tmp_path / ".mission-state").rglob("*")
        if path.is_file()
    }

    result = run_cli(
        "permission-preflight",
        "--json",
        cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": "session-a",
            "MISSION_LEASE_ID": "foreign-lease",
        },
    )

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in (tmp_path / ".mission-state").rglob("*")
        if path.is_file()
    }
    assert result.returncode == 2
    assert after == before


def test_permission_successful_halt_keeps_prewrite_recovery_backup(tmp_path, run_cli):
    owner = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli(
        "init",
        "A5 recovery backup",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=owner,
        check=True,
    )
    state_path = tmp_path / ".mission-state" / "sessions" / "session-a.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["assumptions_path"] = "outside.md"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli(
        "permission-preflight", "--json", cwd=tmp_path, env_extra=owner
    )

    assert result.returncode == 2
    assert state_path.with_suffix(".json.bak").read_bytes() == before
    assert state_path.read_bytes() != before


def test_permission_admission_cannot_overwrite_takeover_at_backup_boundary(
    tmp_path, run_cli, monkeypatch
):
    owner = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli(
        "init",
        "A5 admission race",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=owner,
        check=True,
    )
    state_path = tmp_path / ".mission-state" / "sessions" / "session-a.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["assumptions_path"] = "outside.md"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    backup_path = state_path.with_suffix(".json.bak")
    before_backup = backup_path.read_bytes() if backup_path.exists() else None
    module = _load_state_module()
    original_backup = module.backup_state
    takeover_bytes = []

    def takeover_after_backup(path):
        original_backup(path)
        takeover = json.loads(path.read_text(encoding="utf-8"))
        takeover.update(
            {
                "owner_session_id": "takeover",
                "lease_id": "lease-takeover",
                "fencing_epoch": takeover["fencing_epoch"] + 1,
                "lease_expires_at": "2099-01-01T00:00:00Z",
            }
        )
        payload = json.dumps(takeover, sort_keys=True).encode("utf-8")
        path.write_bytes(payload)
        takeover_bytes.append(payload)

    monkeypatch.setenv("MISSION_SESSION_ID", "session-a")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-a")
    monkeypatch.setattr(module, "backup_state", takeover_after_backup)

    result = module._permission_preflight(tmp_path)

    assert result["ok"] is False
    assert result["halt_recorded"] is False
    assert state_path.read_bytes() == takeover_bytes[0]
    if before_backup is None:
        assert not backup_path.exists()
    else:
        assert backup_path.read_bytes() == before_backup


@pytest.mark.parametrize(
    "probes",
    [
        (object(),),
        (),
    ],
)
def test_permission_malformed_probe_sequence_rejects_before_load(probes):
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        record_permission_observation,
    )

    repository = MissionRepository(_state())
    with pytest.raises(ValueError):
        record_permission_observation(
            repository,
            PermissionObservationRequest(
                probes=probes,
                observed_at="2026-08-17T00:00:00Z",
            ),
        )
    assert repository.saved is None


def test_permission_probe_unknown_fields_cannot_enter_typed_request():
    from mission_application.runtime_guard import PermissionProbe

    with pytest.raises(TypeError):
        PermissionProbe(
            target="state",
            outcome="allowed",
            error=None,
            passes=True,
        )


def test_a5_cli_handlers_reach_only_the_registered_application_routes():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    def reachable(start):
        pending = [start]
        seen = set()
        called = set()
        while pending:
            name = pending.pop()
            if name in seen or name not in functions:
                continue
            seen.add(name)
            for call in (
                item for item in ast.walk(functions[name]) if isinstance(item, ast.Call)
            ):
                if isinstance(call.func, ast.Name):
                    called.add(call.func.id)
                    pending.append(call.func.id)
        return called

    assert "observe_stop_guard" in reachable("cmd_stop_guard_observe")
    assert "record_permission_observation" in reachable("cmd_permission_preflight")
    forbidden = {"atomic_write_json", "_write_stop_guard_state", "_record_permission_preflight_halt"}
    for handler in ("cmd_stop_guard_observe", "cmd_permission_preflight"):
        direct = {
            call.func.id
            for call in ast.walk(functions[handler])
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        assert direct.isdisjoint(forbidden)
    assert "record_permission_observation" in reachable(
        "_record_permission_preflight_halt"
    )


def test_runtime_guard_application_has_no_filesystem_or_process_io():
    source = Path(__file__).resolve().parents[1] / "lib" / "mission_application" / "runtime_guard.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    roots = {
        alias.name.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    roots.update(
        node.module.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert roots.isdisjoint({"os", "pathlib", "subprocess", "sys"})
