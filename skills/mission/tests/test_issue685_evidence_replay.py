"""#685: evidence 操作は同一 operation ID の replay を成功として返す。"""

import json
import os
from types import SimpleNamespace

import pytest


def _v5_env(tmp_path):
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
        "MISSION_STATE_NOW": "2030-01-01T00:00:00Z",
    }


def _init_v5(run_cli, tmp_path, env):
    result = run_cli(
        "init",
        "issue 685 replay",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
    )
    assert result.returncode == 0, result.stderr


def _authoritative_state(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository

    snapshot = LocalFencedRepository(tmp_path / ".mission-state").read("test")
    return json.loads(snapshot.state_bytes)


def test_verification_record_replay_returns_the_original_result_once(tmp_path, run_cli):
    env = {**_v5_env(tmp_path), "MISSION_OPERATION_ID": "issue-685-replay"}
    _init_v5(run_cli, tmp_path, env)
    payload = json.dumps(
        {
            "schema": "mission-verification/1",
            "checks": [{"name": "tests", "ok": True, "detail": "1 passed"}],
        }
    )

    first = run_cli(
        "verification",
        "record",
        "--iteration",
        "1",
        "--stdin",
        cwd=tmp_path,
        input_text=payload,
        env_extra=env,
    )
    second = run_cli(
        "verification",
        "record",
        "--iteration",
        "1",
        "--stdin",
        cwd=tmp_path,
        input_text=payload,
        env_extra=env,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == json.loads(first.stdout)
    state = _authoritative_state(tmp_path)
    assert state["verification_history"] == [
        json.loads(first.stdout)["verification"]
    ]


def test_verification_record_without_operation_id_appends_each_call(tmp_path, run_cli):
    """Replay remains opt-in; caller-less verification records are all retained."""
    env = _v5_env(tmp_path)
    _init_v5(run_cli, tmp_path, env)
    payload = json.dumps(
        {
            "schema": "mission-verification/1",
            "checks": [{"name": "tests", "ok": True}],
        }
    )

    first = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path, input_text=payload, env_extra=env,
    )
    second = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path, input_text=payload, env_extra=env,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert len(_authoritative_state(tmp_path)["verification_history"]) == 2


def _closed_result(projection, *, decision=None, replayed=True):
    from mission_application.ports import LegacyCommandExecutionResult
    from mission_kernel.json_codec import freeze_json_value

    return LegacyCommandExecutionResult(decision, freeze_json_value(projection), replayed)


def _request():
    from mission_application.evidence import VerificationRecordRequest
    from mission_kernel.commands import VerificationCheck

    return VerificationRecordRequest(
        now="2030-01-01T00:00:00Z",
        iteration=1,
        checks=(VerificationCheck("tests", True, "1 passed"),),
    )


def test_verification_operation_identity_does_not_read_state_bytes(tmp_path):
    """Caller identity is content-bound without application-layer file I/O."""
    from mission_application.evidence import prepare_verification_record_operation

    captured = {}

    def compatibility(arguments, *, target_digest, require_caller):
        captured["arguments"] = arguments
        captured["target_digest"] = target_digest
        captured["require_caller"] = require_caller
        return "issue-685-no-read", arguments

    def canonical(session_id, command_type, arguments, *, caller_operation_id):
        captured["session_id"] = session_id
        captured["command_type"] = command_type
        captured["caller_operation_id"] = caller_operation_id
        return caller_operation_id, arguments

    prepared = prepare_verification_record_operation(
        {"checks": [{"name": "tests", "ok": True}]},
        iteration=1,
        state_path=tmp_path / "missing.json",
        compatibility_arguments=compatibility,
        canonical_operation=canonical,
    )

    assert prepared.operation_id == "issue-685-no-read"
    assert captured["arguments"]["iteration"] == 1
    assert captured["arguments"]["checks_digest"].startswith("sha256:")
    assert captured["target_digest"] == ""
    assert captured["require_caller"] is False


def test_prepared_operations_declare_their_volatile_fields():
    from mission_application.evidence import (
        prepare_context_manifest,
        prepare_progress_clear,
        prepare_progress_update,
        prepare_verification_record,
    )
    from mission_kernel.commands import VerificationCheck

    state = {
        "phase": "reviewing",
        "loop_active": True,
        "session_id": "test",
        "mission": "m",
        "mission_id": "mid",
        "assumptions_path": "a.md",
    }
    progress = prepare_progress_update(
        state,
        now="2030-01-01T00:00:00Z",
        total=1,
        completed=0,
        batch_size=1,
        last_unit=None,
        artifact_path=None,
        iteration=1,
        evidence_path="progress.json",
    )
    clear = prepare_progress_clear(state, now="2030-01-01T00:00:00Z")
    context = prepare_context_manifest(
        state,
        now="2030-01-01T00:00:00Z",
        iteration=1,
        publication_path="manifest.json",
    )
    verification = prepare_verification_record(
        state,
        now="2030-01-01T00:00:00Z",
        iteration=1,
        checks=(VerificationCheck("tests", True, None),),
    )

    assert progress.volatile_fields == ("updated_at",)
    assert clear.volatile_fields == ()
    assert context.volatile_fields == ()
    assert verification.volatile_fields == ("recorded_at",)


def test_replay_keeps_verification_projection_validation():
    """A replay must still be checked against the state it claims to have written."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        return prepare(current), _closed_result({"verification_history": []})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(_request(), repository)


def test_foreign_execution_result_is_rejected():
    """Only the closed result type may claim a replay (#685 review)."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def foreign(prepare):
        return prepare(current), SimpleNamespace(
            decision=None, replayed=True, projection={"verification_history": []}
        )

    repository = SimpleNamespace(execute_evidence_transition_effects=foreign)
    with pytest.raises(EvidenceFailure, match="evidence-execution-result-invalid"):
        run_verification_record(_request(), repository)


def test_closed_result_forbids_a_missing_decision_without_replay():
    """decision=None without replayed=True is unconstructible, not merely rejected."""
    with pytest.raises(ValueError, match="legacy-command-replay-result-invalid"):
        _closed_result({"verification_history": []}, replayed=False)


def test_replay_keeps_progress_projection_validation():
    """The replay branch must validate progress projections too (#685 review)."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import ProgressUpdateRequest, run_progress_update

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        return prepare(current), _closed_result({"progress": {"note": "other"}})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="progress-projection-mismatch"):
        run_progress_update(
            ProgressUpdateRequest(
                now="2030-01-01T00:00:00Z",
                total=10,
                completed=1,
                batch_size=1,
                last_unit="unit",
                artifact_path="artifact.md",
                iteration=1,
                evidence_path="evidence.json",
            ),
            repository,
        )


def test_replay_keeps_context_projection_validation():
    """The replay branch must validate context manifest projections too."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import ContextManifestRequest, run_context_manifest

    current = {
        "phase": "reviewing",
        "loop_active": True,
        "session_id": "test",
        "mission": "m",
        "mission_id": "mid",
        "assumptions_path": "a.md",
    }

    def replay(prepare):
        return prepare(current), _closed_result({"context_manifests": {}})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="context-projection-mismatch"):
        run_context_manifest(
            ContextManifestRequest(
                now="2030-01-01T00:00:00Z",
                iteration=1,
                publication_path="manifest.json",
            ),
            repository,
        )


def test_context_manifest_replay_returns_once_with_operation_id(monkeypatch):
    """A repeated context operation returns the one content-addressed record."""
    from mission_application.evidence import ContextManifestRequest, run_context_manifest
    from mission_application.ports import LegacyCommandExecutionResult
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import Decision

    monkeypatch.setenv("MISSION_OPERATION_ID", "issue-685-context")
    state = {
        "phase": "reviewing",
        "loop_active": True,
        "session_id": "test",
        "mission": "m",
        "mission_id": "mid",
        "assumptions_path": "a.md",
    }
    completed = set()

    def execute(prepare):
        prepared = prepare(state)
        operation_id = os.environ["MISSION_OPERATION_ID"]
        if operation_id in completed:
            return prepared, _closed_result(state)
        completed.add(operation_id)
        state["context_manifests"] = {
            "1": {
                "path": prepared.result["path"],
                "digest": prepared.result["digest"],
                "generated_at": prepared.command.at,
            }
        }
        accepted = Decision(True, None, None)
        return prepared, LegacyCommandExecutionResult(
            accepted, freeze_json_value(state)
        )

    repository = SimpleNamespace(execute_evidence_transition_effects=execute)
    request = ContextManifestRequest(
        now="2030-01-01T00:00:00Z",
        iteration=1,
        publication_path="manifest.json",
    )

    first = run_context_manifest(request, repository)
    second = run_context_manifest(request, repository)

    assert second == first
    assert len(state["context_manifests"]) == 1


def test_replay_succeeds_when_the_clock_advances_between_attempts(tmp_path, run_cli):
    """A crash retry happens later than the first attempt (#685 review).

    `recorded_at` is re-derived from the current clock, so a strict comparison
    against the freshly prepared payload would reject every retry that crosses
    a second boundary.
    """
    env = {**_v5_env(tmp_path), "MISSION_OPERATION_ID": "issue-685-clock"}
    _init_v5(run_cli, tmp_path, env)
    payload = json.dumps(
        {
            "schema": "mission-verification/1",
            "checks": [{"name": "tests", "ok": True, "detail": "1 passed"}],
        }
    )
    first = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path, input_text=payload, env_extra=env,
    )
    later = {**env, "MISSION_STATE_NOW": "2030-01-01T00:05:00Z"}
    second = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path, input_text=payload, env_extra=later,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    # The stored record stays authoritative for the clock-sourced field.
    assert json.loads(second.stdout) == json.loads(first.stdout)
    state = _authoritative_state(tmp_path)
    assert state["verification_history"] == [json.loads(first.stdout)["verification"]]


def test_replay_finds_the_matching_verification_before_the_latest_record():
    """An intervening record must not replace the replay's stored result."""
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        prepared = prepare(current)
        matching = {
            **prepared.result["verification"],
            "recorded_at": "2029-12-31T23:59:00Z",
        }
        latest = {
            **matching,
            "checks": [{"name": "other", "ok": False, "detail": None}],
            "failed_count": 1,
            "status": "failed",
            "recorded_at": "2029-12-31T23:59:30Z",
        }
        return prepared, _closed_result(
            {"verification_history": [matching, latest]}
        )

    result = run_verification_record(
        _request(), SimpleNamespace(execute_evidence_transition_effects=replay)
    )

    assert result["verification"]["recorded_at"] == "2029-12-31T23:59:00Z"


def test_replay_rejects_an_ambiguous_matching_verification_history():
    """Content equality without a unique stored record is fail-closed."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        prepared = prepare(current)
        first = {
            **prepared.result["verification"],
            "recorded_at": "2029-12-31T23:59:00Z",
        }
        second = {**first, "recorded_at": "2029-12-31T23:59:30Z"}
        return prepared, _closed_result(
            {"verification_history": [first, second]}
        )

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(_request(), repository)


def test_replay_still_rejects_a_different_payload(tmp_path, run_cli):
    """Ignoring the clock field must not let a different record pass as a replay."""
    env = {**_v5_env(tmp_path), "MISSION_OPERATION_ID": "issue-685-divergent"}
    _init_v5(run_cli, tmp_path, env)
    first = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path,
        input_text=json.dumps(
            {"schema": "mission-verification/1", "checks": [{"name": "tests", "ok": True}]}
        ),
        env_extra=env,
    )
    assert first.returncode == 0, first.stderr
    second = run_cli(
        "verification", "record", "--iteration", "1", "--stdin",
        cwd=tmp_path,
        input_text=json.dumps(
            {"schema": "mission-verification/1", "checks": [{"name": "tests", "ok": False}]}
        ),
        env_extra=env,
    )
    assert second.returncode != 0
    assert "operation ID has a different intent" in second.stderr


def test_same_operation_id_rejects_different_checks_after_an_intervening_record(
    tmp_path, run_cli
):
    """The retry key is bound to normalized checks, not the latest record."""
    base_env = _v5_env(tmp_path)
    operation_a = {**base_env, "MISSION_OPERATION_ID": "issue-685-content-a"}
    operation_b = {**base_env, "MISSION_OPERATION_ID": "issue-685-content-b"}
    _init_v5(run_cli, tmp_path, operation_a)

    def record(name, ok, env):
        return run_cli(
            "verification",
            "record",
            "--iteration",
            "1",
            "--stdin",
            cwd=tmp_path,
            input_text=json.dumps(
                {
                    "schema": "mission-verification/1",
                    "checks": [{"name": name, "ok": ok}],
                }
            ),
            env_extra=env,
        )

    first = record("A", True, operation_a)
    intervening = record("B", False, operation_b)
    collision = record("A", False, operation_a)

    assert first.returncode == 0, first.stderr
    assert intervening.returncode == 0, intervening.stderr
    assert collision.returncode != 0
    assert "operation ID has a different intent" in collision.stderr
    history = _authoritative_state(tmp_path)["verification_history"]
    assert [entry["checks"][0]["name"] for entry in history] == ["A", "B"]


def test_replay_rejects_a_stored_record_without_a_clock_field():
    """A missing or malformed clock field is a corrupt record, not a replay."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        prepared = prepare(current)
        stored = dict(prepared.result["verification"])
        stored.pop("recorded_at")
        return prepared, _closed_result({"verification_history": [stored]})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(_request(), repository)


def test_replay_rejects_a_stored_record_with_an_empty_clock_field():
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        prepared = prepare(current)
        stored = {**prepared.result["verification"], "recorded_at": ""}
        return prepared, _closed_result({"verification_history": [stored]})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(_request(), repository)


def test_replay_rejects_a_stored_record_with_a_nul_clock_field():
    """Replay clock validation is exactly the kernel projection contract."""
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import run_verification_record

    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        prepared = prepare(current)
        stored = {**prepared.result["verification"], "recorded_at": "bad\x00clock"}
        return prepared, _closed_result({"verification_history": [stored]})

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(_request(), repository)
