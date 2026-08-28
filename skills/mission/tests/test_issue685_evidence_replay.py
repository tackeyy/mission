"""#685: evidence 操作は同一 operation ID の replay を成功として返す。"""

import json
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
