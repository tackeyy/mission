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


def test_replay_keeps_verification_projection_validation():
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import VerificationRecordRequest, run_verification_record
    from mission_kernel.commands import VerificationCheck

    request = VerificationRecordRequest(
        now="2030-01-01T00:00:00Z",
        iteration=1,
        checks=(VerificationCheck("tests", True, "1 passed"),),
    )
    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def replay(prepare):
        return prepare(current), SimpleNamespace(
            decision=None,
            replayed=True,
            projection={"verification_history": []},
        )

    repository = SimpleNamespace(execute_evidence_transition_effects=replay)
    with pytest.raises(EvidenceFailure, match="verification-projection-mismatch"):
        run_verification_record(request, repository)


def test_non_replay_without_decision_remains_rejected():
    from mission_application.artifact import EvidenceFailure
    from mission_application.evidence import VerificationRecordRequest, run_verification_record
    from mission_kernel.commands import VerificationCheck

    request = VerificationRecordRequest(
        now="2030-01-01T00:00:00Z",
        iteration=1,
        checks=(VerificationCheck("tests", True, "1 passed"),),
    )
    current = {"phase": "executing", "loop_active": True, "session_id": "test"}

    def invalid(prepare):
        return prepare(current), SimpleNamespace(
            decision=None,
            replayed=False,
            projection={"verification_history": []},
        )

    repository = SimpleNamespace(execute_evidence_transition_effects=invalid)
    with pytest.raises(EvidenceFailure, match="evidence-transition-rejected"):
        run_verification_record(request, repository)
