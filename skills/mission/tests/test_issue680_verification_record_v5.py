"""#680: v5 verification record と evidence effect 境界の回帰テスト。"""

from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from .evidence_doubles import FakeFencedRepository as _FakeFencedRepository
from .evidence_doubles import in_memory_v5_repository as _in_memory_v5_repository


def _v5_env(tmp_path):
    """v5 state 生成用: MISSION_* を絞り、version-skew 警告も抑制する。"""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME":          str(tmp_path / "fake-codex-home"),
    }


def _init_v5(run_cli, tmp_path, *, mission="v5 mission"):
    env = _v5_env(tmp_path)
    result = run_cli(
        "init",
        mission,
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )
    head = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
    )
    assert head["schema"] == "mission-head/1"
    return env


def _authoritative_state(tmp_path, repository_format):
    if repository_format == "v4":
        return json.loads(
            (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
        )

    from mission_persistence.fenced_commit import LocalFencedRepository

    snapshot = LocalFencedRepository(tmp_path / ".mission-state").read("test")
    return json.loads(snapshot.state_bytes)


def _record(run_cli, tmp_path, *, checks, iteration=1, env_extra=None):
    payload = json.dumps({"schema": "mission-verification/1", "checks": checks})
    return run_cli(
        "verification", "record",
        "--iteration", str(iteration),
        "--stdin",
        cwd=tmp_path,
        input_text=payload,
        env_extra=env_extra or {},
    )


def test_v5_verification_record_succeeds_and_appends_history(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path)
    before = _authoritative_state(tmp_path, "v5")
    result = _record(
        run_cli,
        tmp_path,
        checks=[{"name": "tests", "ok": True, "detail": "5 passed"}],
        env_extra=env,
    )
    assert result.returncode == 0, (
        f"verification record が v5 state で失敗した\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["verification"]["status"] == "passed"

    state = _authoritative_state(tmp_path, "v5")
    assert state["verification_history"][-1] == payload["verification"]
    for key in ("passes", "loop_active", "halt_reason", "threshold", "score_history"):
        assert state.get(key) == before.get(key)


def test_v5_verification_record_preserves_failed_check_details(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path, mission="v5 readability mission")
    result = _record(
        run_cli,
        tmp_path,
        checks=[
            {"name": "unit", "ok": True, "detail": "3 passed"},
            {"name": "lint", "ok": False, "detail": "1 warning"},
        ],
        env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    verification = json.loads(result.stdout)["verification"]
    assert verification["status"] == "failed"
    assert verification["failed_count"] == 1
    assert verification["checks"][1] == {
        "name": "lint",
        "ok": False,
        "detail": "1 warning",
    }


def test_v5_verification_record_empty_checks_is_not_run(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path, mission="v5 empty checks mission")
    result = _record(run_cli, tmp_path, checks=[], env_extra=env)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verification"]["status"] == "not-run"


def test_v5_failed_verification_does_not_block_record_command(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path, mission="v5 fail mission")
    result = _record(
        run_cli,
        tmp_path,
        checks=[{"name": "tests", "ok": False, "detail": "1 failed"}],
        env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verification"]["status"] == "failed"


def test_v4_verification_record_unchanged(tmp_path, legacy_run_cli):
    env = _v5_env(tmp_path)
    legacy_run_cli(
        "init",
        "v4 mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    assert "schema" not in json.loads(state_path.read_text())
    before = json.loads(state_path.read_text())

    result = _record(
        legacy_run_cli,
        tmp_path,
        checks=[{"name": "tests", "ok": False, "detail": "1 failed"}],
        env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verification"]["status"] == "failed"
    assert json.loads(state_path.read_text())["verification_history"][-1] == payload[
        "verification"
    ]
    backup = json.loads(state_path.with_suffix(".json.bak").read_text())
    assert backup == before
    after = json.loads(state_path.read_text())
    for key in ("passes", "loop_active", "halt_reason", "threshold", "score_history"):
        assert after.get(key) == before.get(key)


def test_v5_context_manifest_uses_lifecycle_repository(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path, mission="v5 context mission")
    result = run_cli(
        "context-manifest",
        "--iteration",
        "1",
        "--out",
        "context-manifest.json",
        cwd=tmp_path,
        env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    # #711: evidence is published as a projection of the repository, so it
    # cannot land inside the repository's own subtree.
    assert (tmp_path / "context-manifest.json").exists()


@pytest.mark.parametrize("repository_format", ["v4", "v5"])
@pytest.mark.parametrize(
    "lease_case", ["missing-live-token", "retired-expired-token"]
)
def test_verification_record_rejects_invalid_lease_without_state_change(
    tmp_path,
    run_cli,
    legacy_run_cli,
    repository_format,
    lease_case,
):
    runner = legacy_run_cli if repository_format == "v4" else run_cli
    env = {
        **_v5_env(tmp_path),
        "MISSION_STATE_NOW": "2030-01-01T00:00:00Z",
    }
    runner(
        "init",
        f"{repository_format} lease mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )
    if lease_case == "retired-expired-token":
        takeover = _record(
            runner,
            tmp_path,
            checks=[{"name": "takeover", "ok": True}],
            env_extra={
                **env,
                "MISSION_STATE_NOW": "2030-01-01T00:16:00Z",
                "MISSION_LEASE_ID": "replacement-lease",
            },
        )
        assert takeover.returncode == 0, takeover.stderr

    before = _authoritative_state(tmp_path, repository_format)
    backup_path = (
        tmp_path / ".mission-state" / "sessions" / "test.json.bak"
        if repository_format == "v4"
        else None
    )
    backup_before = (
        backup_path.read_bytes() if backup_path is not None and backup_path.exists() else None
    )
    invalid_env = {
        **env,
        "MISSION_STATE_NOW": (
            "2030-01-01T00:00:01Z"
            if lease_case == "missing-live-token"
            else "2030-01-01T00:16:01Z"
        ),
        "MISSION_LEASE_ID": (
            None if lease_case == "missing-live-token" else "test-lease"
        ),
    }

    result = _record(
        runner,
        tmp_path,
        checks=[{"name": "tests", "ok": True}],
        env_extra=invalid_env,
    )

    assert result.returncode == 2
    assert "lease" in result.stderr.lower()
    assert _authoritative_state(tmp_path, repository_format) == before
    if backup_path is not None:
        backup_after = backup_path.read_bytes() if backup_path.exists() else None
        assert backup_after == backup_before


def _prepared_verification(state):
    from mission_application.evidence import prepare_verification_record
    from mission_kernel.commands import VerificationCheck

    return prepare_verification_record(
        state,
        now="2030-01-01T00:00:01Z",
        iteration=1,
        checks=(VerificationCheck("tests", True, None),),
    )


def test_v5_evidence_operation_rejects_unbound_effect_claim_before_publish():
    from mission_application.artifact import make_evidence_effect

    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}
    repository = _in_memory_v5_repository(current)
    prepared = replace(
        _prepared_verification(current),
        effects=(make_evidence_effect("evidence", "evidence.json", b"{}"),),
    )

    with pytest.raises(ValueError, match="invalid-transition-effect-binding"):
        repository.execute_evidence_transition_effects(lambda _state: prepared)


def test_v5_effect_claim_without_prepared_effects_is_rejected_before_commit():
    from mission_application.evidence import prepare_context_manifest
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import TransitionTableError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    current = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable",
        "score_history": [],
    }
    prepared = replace(
        prepare_context_manifest(
            current,
            now="2030-01-01T00:00:01Z",
            iteration=1,
            publication_path="evidence/context/manifest.json",
        ),
        effects=(),
    )
    backend = _FakeFencedRepository(
        decode_mission_state(json.dumps(current).encode("utf-8"))
    )
    repository = V5CompatibilityRepository(
        repository=backend,
        session_id="portable",
        lease_owner_session_id="portable",
        presented_lease_id=None,
    )

    with pytest.raises(
        TransitionTableError, match="invalid-transition-effect-binding"
    ):
        repository.execute_evidence_transition_effects(lambda _state: prepared)

    assert backend.commits == []


def test_v5_evidence_replay_matches_transition_replay_semantics():
    current = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable",
        "verification_history": [
            {
                "iteration": 1,
                "status": "passed",
                "checks": [{"name": "tests", "ok": True, "detail": None}],
                "failed_count": 0,
                "recorded_at": "2030-01-01T00:00:00Z",
            }
        ],
    }
    repository = _in_memory_v5_repository(current, replayed=True)
    prepared, execution = repository.execute_evidence_transition_effects(
        _prepared_verification
    )

    assert prepared.effects == ()
    assert execution.replayed is True
    assert execution.decision is None
    assert execution.projection == current


@pytest.mark.parametrize(
    ("unsupported_argument", "unsupported_value", "expected_message"),
    (
        (
            "effect_transaction",
            "unsupported-transaction",
            "effect_transaction='unsupported-transaction' is not supported by the v5 executor",
        ),
        (
            "verify_published",
            "unsupported-verifier",
            "verify_published='unsupported-verifier' is not supported by the v5 executor",
        ),
        ("backup", None, "backup=None is not supported by the v5 executor"),
    ),
)
def test_v5_evidence_executor_rejects_unsupported_arguments_before_prepare(
    unsupported_argument, unsupported_value, expected_message
):
    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}
    repository = _in_memory_v5_repository(current, replayed=True)

    def prepare(_state):
        raise AssertionError("unsupported arguments must be rejected before prepare")

    with pytest.raises(ValueError) as error:
        repository.execute_evidence_transition_effects(
            prepare, **{unsupported_argument: unsupported_value}
        )

    assert str(error.value) == expected_message


@pytest.mark.parametrize(
    ("unsupported_argument", "unsupported_value", "expected_message"),
    (
        (
            "effect_transaction",
            "unsupported-transaction",
            "effect_transaction='unsupported-transaction' is not supported by the v5 executor",
        ),
        (
            "verify_published",
            "unsupported-verifier",
            "verify_published='unsupported-verifier' is not supported by the v5 executor",
        ),
        ("backup", None, "backup=None is not supported by the v5 executor"),
    ),
)
def test_v5_transition_executor_rejects_unsupported_arguments_before_prepare(
    unsupported_argument, unsupported_value, expected_message
):
    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}
    repository = _in_memory_v5_repository(current, replayed=True)

    def prepare(_state):
        raise AssertionError("unsupported arguments must be rejected before prepare")

    with pytest.raises(ValueError) as error:
        repository.execute_transition_effects(
            prepare, **{unsupported_argument: unsupported_value}
        )

    assert str(error.value) == expected_message


@pytest.mark.parametrize(
    ("unsupported_argument", "unsupported_value"),
    (
        ("effect_transaction", object()),
        ("verify_published", object()),
        ("backup", False),
    ),
)
def test_v5_evidence_executor_reentrancy_fence_precedes_argument_validation(
    unsupported_argument, unsupported_value
):
    from mission_persistence.fenced_commit import FencedCommitError

    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}
    repository = _in_memory_v5_repository(current)

    def reenter(_state):
        repository.execute_evidence_transition_effects(
            _prepared_verification,
            **{unsupported_argument: unsupported_value},
        )

    with pytest.raises(FencedCommitError) as error:
        repository.execute_evidence_transition_effects(reenter)

    assert error.value.code == "request-invalid"
    assert error.value.detail == (
        "execute_evidence_transition_effects is not allowed while a decision is being executed"
    )


def test_v5_delegating_transition_preserves_pinned_invalid_result_detail():
    from mission_application.ports import PreparedTransitionOperation
    from mission_persistence.fenced_commit import FencedCommitError

    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}
    evidence = _prepared_verification(current)
    transition = PreparedTransitionOperation(evidence.command, (), {})

    def invalid_result_detail(call):
        repository = _in_memory_v5_repository(current)
        repository.execute = lambda _command: object()
        with pytest.raises(FencedCommitError) as error:
            call(repository)
        assert error.value.code == "decision-invalid"
        return error.value.detail

    assert invalid_result_detail(
        lambda repository: repository.execute_evidence_transition_effects(
            lambda _state: evidence
        )
    ) == "typed evidence result is invalid"
    assert invalid_result_detail(
        lambda repository: repository.execute_transition_effects(
            lambda _state: transition
        )
    ) == "typed transition result is invalid"


def test_v5_delegating_transition_preserves_pinned_reentrancy_detail():
    from mission_persistence.fenced_commit import FencedCommitError

    current = {"phase": "executing", "loop_active": True, "session_id": "portable"}

    def reentrancy_detail(entry):
        repository = _in_memory_v5_repository(current)

        def reenter(_state):
            entry(repository)

        with pytest.raises(FencedCommitError) as error:
            repository.execute_evidence_transition_effects(reenter)
        assert error.value.code == "request-invalid"
        return error.value.detail

    assert reentrancy_detail(
        lambda repository: repository.execute_evidence_transition_effects(
            _prepared_verification
        )
    ) == (
        "execute_evidence_transition_effects is not allowed while a decision is being executed"
    )
    assert reentrancy_detail(
        lambda repository: repository.execute_transition_effects(
            _prepared_verification
        )
    ) == "execute_transition_effects is not allowed while a decision is being executed"
